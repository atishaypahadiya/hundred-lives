"""Run one persona through simulated weeks and log everything to runs/<run_id>.jsonl.

    uv run python -m orchestrator.run --persona agents/personas/maya.yaml --runs 10

One turn is one simulated week. Each week the agent gets a fresh conversation: the stable
system prompt (persona, rules, openings in this run's shuffled order; cached) and a week
prompt rendered from world state and memory. Past weeks are never replayed as raw
messages, only as records and the agent's own reflections.
"""

import argparse
import hashlib
import json
import re
import signal
import sys
import traceback
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import yaml
from dotenv import load_dotenv

from . import referee
from .backends import APIError, make_backend
from .memory import Memory, WeekRecord
from .tools import (ACTION_COST, ACTIONS_PER_WEEK, CODING_TOOLS, MAX_INVALID_PER_WEEK, REAPPLY_AFTER_WEEKS,
                    ToolError, Workspace, tools_for, validate)
from .world import (COMMITMENT_TYPES, DEFAULT_PATH as DEFAULT_WORLD, INCOME_TYPES, auth_problem, catalog,
                    check_contacts, enrolled, listed, load_world, semester, semester_capped, shuffled, term, time_cost)

ROOT = Path(__file__).resolve().parent.parent
RUNS_DIR = ROOT / "runs"

DEFAULT_HORIZON = 52            # weeks; a persona's own horizon is the cap
DEFAULT_WEEKLY_EXPENSES = 1000  # dollars; a persona can override with `weekly_expenses`
MAX_NUDGES_PER_WEEK = 1         # times a text-only reply is answered with "use a tool"
OFFER_HOLD_WEEKS = 2            # an offer that doesn't fit your time waits this long
MAX_ATTACHMENTS = 3
HIDDEN_FROM_AGENT = ("horizon", "contacts", "referee_view")  # not told when its life ends; contacts are rendered
REFERRAL_VALID_WEEKS = 26        # a referral goes with the next application to that opening until then
THREAD_SHOWN = 2                 # messages per contact shown in the week prompt (the latest)
CLOSING = ("You have no actions left this week. Call wait now with your note and your idea; "
           "other calls will fail.")
MAX_POST_CHARS = 500
TEAMMATE_BUILD_ACTIONS = 2       # extra build actions in a hackathon's week with a teammate
BOOST_VALID_WEEKS = 8            # a contact's post boost applies to the next post within this
POSTS_SHOWN = 3                  # recent posts (with their replies) shown in the week prompt
LOG_OUTPUT_CHARS = 4000
SHELL_SHOWN_CHARS = 3000         # shell output the agent sees (head + tail)

SYSTEM = """\
You are {name}. You are living the next few years of your life one week at a time, in a \
simulated world.

About you:
{about}

How it works:
- Each turn is one week. At the start of the week you see your situation, any news, your \
records, and your own reflections.
- Each week has {actions} actions. A full-time commitment uses {full_time} of them every week, \
a part-time one {part_time}. Your commitments can't add up to more than {actions}.
- files (1 action): your private workspace for notes, plans, code, and drafts. It persists \
across weeks. You can attach files to applications.
{computer}- apply (1 action): apply to one of the week's listed openings by its id. The world \
decides the outcome, and replies can take weeks. You can apply to the same opening again \
{reapply} weeks after you last applied; until then it isn't listed.
{message}{extra_tools}- spend (1 action): spend your company's funds on one item from the price list below. \
Company funds come from investors and paying customers, and they can't pay your living expenses.
- gig ({gig_cost} actions): gig work for ${gig_pay:,} paid that week.
- leave (1 action): leave a job or program you're in.
- wait (free): ends the week, with a one-line note and the idea you're most drawn to right now.
- A call that fails costs nothing, but after {max_invalid} failed calls the week ends.
- Your living expenses are ${expenses:,.0f}/week. Your personal cash can't be spent on anything \
else. If you can't cover a week's expenses, you must take a gig or apply for a job that week.

{world_rules}{contacts}Price list for company funds (spend):
{prices}

The openings you can apply to are listed in each week's prompt.

Decide as {name} would. Keep your own text short; what you do matters more than what you say.\
"""


COMPUTER = """\
- shell (1 action): run a bash command on your own Linux computer, in the same workspace \
directory. It has git and Python 3.12 (standard library only; there is no internet, so pip \
can't download). Your code host is {gitea} where you are user {user}; \
`git push {gitea}/{user}/<name>.git main` creates a repo.
- launch (1 action): post one of your repos to {forum}, a forum for builders. How people \
respond depends on what's actually in the repo. You can post the same repo again {relaunch} \
weeks later.
- In applications you can link one of your repos (apply's repo field); reviewers look at it.
"""


class BudgetExceeded(Exception):
    pass


CODE_PATHS = ("orchestrator", "world", "agents", "sandbox")  # what a run's behavior depends on


def dollars(v) -> float:
    """A money amount from a persona file: 300, 300.0, "$300", or "$1,200"."""
    if isinstance(v, str):
        v = v.strip().lstrip("$").replace(",", "")
    return float(v)


def code_version() -> dict:
    """The commit the run's code came from, and whether the tree had uncommitted changes."""
    import subprocess
    git = lambda *a: subprocess.run(["git", "-C", str(ROOT), *a], capture_output=True, text=True).stdout.strip()
    return {"git_sha": git("rev-parse", "HEAD") or None,
            "dirty": bool(git("status", "--porcelain", "--", *CODE_PATHS))}


def _sigterm(signum, frame):
    raise KeyboardInterrupt  # so a killed run still writes run_end


def short(s: str, n: int) -> str:
    s = " ".join(str(s).split())
    return s if len(s) <= n else s[: n - 1] + "…"


@dataclass
class WeekState:
    """What the run loop tracks within one week."""
    broke: bool
    actions: int                 # actions available this week after commitments
    coding_actions: int = 0      # a contractor's extra actions, for CODING_TOOLS only
    coding_used: int = 0
    used: int = 0
    valid: int = 0
    invalid: int = 0
    income_action: bool = False  # gig taken, or applied to a job opening
    trained: bool = False        # at least one `train` this week
    gig_taken: bool = False
    started: bool = False        # set once the week's action budget is fixed
    lines: list[str] = field(default_factory=list)  # full digest
    key: list[str] = field(default_factory=list)    # key events, kept in condensed history

    @property
    def left(self) -> int:
        return self.actions - self.used

    @property
    def coding_left(self) -> int:
        return self.coding_actions - self.coding_used

    def note(self, text: str, key: bool = False):
        self.lines.append(text)
        if key:
            self.key.append(text)


class Logger:
    def __init__(self, path: Path, run_id: str):
        self.f = path.open("a")
        self.run_id = run_id

    def __call__(self, type: str, week: int | None = None, **fields):
        rec = {"ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
               "run_id": self.run_id, "week": week, "type": type, **fields}
        self.f.write(json.dumps(rec, default=str) + "\n")
        self.f.flush()

    def close(self):
        self.f.close()


class Run:
    def __init__(self, run_id: str, persona: dict, persona_path: Path, world: dict,
                 world_path: Path, agent, referee_backend, summarizer, budget: int, horizon: int,
                 seed: int, sandbox: bool, order_seed: int | None = None,
                 dice_seed: int | None = None, sources: dict | None = None):
        self.run_id = run_id
        self.sources = sources or {}  # exact text of the world and persona files this batch loaded
        self.sandbox = sandbox
        self.contacts = {c["id"]: c for c in persona.get("contacts") or []}
        self.world = world
        self.feed = world.get("feed")
        self.academics = world.get("academics")
        self.week1 = (world.get("calendar") or {}).get("week1", referee.DEFAULT_WEEK1)
        has_hackathons = any(o["type"] == "hackathon" for o in world["openings"])
        self.training = world.get("training")
        self.tools = tools_for(sandbox, contacts=bool(self.contacts), feed=bool(self.feed), hackathons=has_hackathons,
                               training=bool(self.training))
        self.username = run_id.lower()[:40]  # Gitea user; same as the run id
        self.box = self.gitea = None
        # Two independent sources of variation, each derived from `seed` unless fixed. Runs from
        # before the split used `seed` for both, so for them order_seed = dice_seed = seed.
        self.seed = seed
        self.order_seed = seed if order_seed is None else order_seed
        self.dice_seed = seed if dice_seed is None else dice_seed
        self.attempts: Counter = Counter()  # (kind, target) -> attempts so far; part of each dice key
        self.persona = persona
        self.persona_path = persona_path
        self.world_path = world_path
        self.agent, self.referee, self.summarizer = agent, referee_backend, summarizer
        self.budget = budget
        self.horizon = horizon
        self.tokens_used = 0
        self.cost_usd = 0.0
        self.weeks_completed = 0
        self.valid_actions = self.invalid_actions = 0

        self.log = Logger(RUNS_DIR / f"{run_id}.jsonl", run_id)
        self.workspace = Workspace(RUNS_DIR / run_id / "workspace")
        self.memory = Memory()

        self.order = shuffled(world["openings"], self.order_seed)
        self.openings = {o["id"]: o for o in world["openings"]}
        self.costs = world["time_costs"]
        self.gig = world["gig"]
        self.forum = world["forum"]
        self.world_illustrative = world["illustrative"]
        self.launches: list[dict] = []     # {week, repo, outcome, upvotes}
        self.repos_seen: set[str] = set()
        self.name = persona.get("name", "the agent")
        start = persona.get("starting_state") or {}
        self.expenses = dollars(persona.get("weekly_expenses", start.get("weekly_expenses", DEFAULT_WEEKLY_EXPENSES)))
        self.cash = float(start.get("savings_weeks", 0)) * self.expenses
        self.company_funding = 0.0   # raised from investors (the referee sees this)
        self.company_revenue = 0.0   # paid by customers (paid pilots)
        self.company_spent = 0.0
        self.spend = world["spend"]
        self.contractors: list[dict] = []  # {id, start_week, end_week, coding_actions, price}
        self.ads: dict[str, dict] = {}     # repo -> {week, expires_week}; used by its next launch
        self.threads: dict[str, list[dict]] = {cid: [] for cid in self.contacts}  # {week, from, text, offer}
        self.offers_used: dict[str, set] = {cid: set() for cid in self.contacts}   # (kind, opening_id|None)
        self.referrals: dict[str, dict] = {}  # opening_id -> {contact_id, known_as, week, expires_week}
        self.commitments: list[dict] = []  # {id, label, opening_id, weekly_income, time_cost, since_week, ends_week}
        self.pending: list[dict] = []      # {id, opening_id, outcome, message, submitted, reveal_week}
        self.held_offers: list[dict] = []  # {opening_id, message, expires_week}
        self.last_applied: dict[str, int] = {}  # opening_id -> week applied
        self.accepted: set[str] = set()
        self.track_record: list[str] = []  # one line per resolved application, for the referee
        self.carried_errors: list[str] = []  # errors the agent didn't see in the week they happened
        self.broke_weeks = 0
        self._ids = 0
        self.followers = 0
        self.posts: list[dict] = []        # {week, text, repo, likes, replies, new_followers, breakout, shown}
        self.events: dict[str, dict] = {}  # accepted hackathons: opening_id -> {event_week, submitted}
        self.upcoming: list[dict] = []     # accepted offers with a fixed start: {opening_id, start_week}
        self.graduated = False
        self.ref_system = referee.system_for(world)
        self.extended_offers = referee.extended_offers(persona.get("contacts") or [])
        self.contact_system = referee.contact_system(persona.get("name", "the agent"), self.extended_offers)
        self.contact_schema = referee.CONTACT_SCHEMA_EXTENDED if self.extended_offers else referee.CONTACT_SCHEMA
        self.teammates: dict[str, dict] = {}  # hackathon id -> {contact_id, known_as}
        self.boost: dict | None = None        # {contact_id, reach, community, expires_week}
        self.train_streak = 0
        self.trained_weeks = 0

        about = {k: v for k, v in persona.items() if k not in HIDDEN_FROM_AGENT}
        contacts = "".join(f"- {c['id']}: {c['name']}. {c['relationship']}\n" for c in self.contacts.values())
        ct, ads = self.spend["contractor"], self.spend["launch_ads"]
        coding = "shell and files" if sandbox else "files"
        prices = (f"- contractor: {ct['description']}. ${ct['price_weekly']:,}/week for 1 to {ct['max_weeks']} "
                  f"weeks, paid up front. From the next week, it adds {ct['coding_actions']} actions every "
                  f"week that can only be used for {coding}. One contractor at a time.")
        if sandbox:
            prices += (f"\n- launch_ads: {ads['description']}. ${ads['price']:,}, paid up front. More people "
                       f"see your next launch of that repo, if you launch it within {ads['valid_weeks']} weeks.")
        extra_tools = ""
        if self.feed:
            extra_tools += (f"- post (1 action): post a short public update on {self.feed['name']}, "
                            f"{self.feed['description']}, optionally linking a repo. At most "
                            f"{self.feed['max_posts_per_week']} a week. Followers you gain stay.\n")
        if self.training:
            extra_tools += f"- train (1 action): {' '.join(self.training['description'].split())}\n"
        if has_hackathons and sandbox:
            extra_tools += ("- submit (free): during a hackathon you were accepted to, submit a repo and a "
                            "pitch to the judges.\n")
        world_rules = ""
        if self.academics:
            ac = self.academics
            sems = ", ".join(f"{x['name']} weeks {x['start']}-{x['end']}" for x in ac["semesters"])
            world_rules += (f"School: {ac['label']}. Classes use {ac['class_actions']} of your actions every "
                            f"week of a semester ({sems}). You graduate at the end of week "
                            f"{ac['graduation_week']}.\n")
        if world.get("work_authorization"):
            world_rules += f"Work authorization: {' '.join(world['work_authorization']['summary'].split())}\n"
        if world_rules:
            world_rules += "\n"
        self.system = SYSTEM.format(
            extra_tools=extra_tools, world_rules=world_rules,
            message=("- message (1 action): write to someone you know (listed below) by their id. They "
                     "may not reply; if they do, you see the reply right away.\n") if self.contacts else "",
            contacts=f"People you can message:\n{contacts}\n" if self.contacts else "", prices=prices,
            name=self.name, actions=ACTIONS_PER_WEEK, full_time=self.costs["full_time"],
            part_time=self.costs["part_time"], reapply=REAPPLY_AFTER_WEEKS,
            gig_pay=self.gig["pay"], gig_cost=self.gig["time_cost"],
            max_invalid=MAX_INVALID_PER_WEEK, expenses=self.expenses,
            computer=COMPUTER.format(gitea="http://gitea:3000", user=self.username,
                                     forum=self.forum["name"],
                                     relaunch=self.forum["relaunch_after_weeks"]) if sandbox else "",
            about=yaml.safe_dump(about, sort_keys=False).strip())

    # --- plumbing ---

    def _next_id(self, prefix: str) -> str:
        self._ids += 1
        return f"{prefix}{self._ids}"

    @property
    def income(self) -> float:
        return sum(c["weekly_income"] for c in self.commitments)

    @property
    def committed_time(self) -> int:
        return sum(c["time_cost"] for c in self.commitments)

    def _capped_time(self, week: int) -> int:
        """Time used by semester-capped work (campus jobs, internships) still running in `week`."""
        return sum(c["time_cost"] for c in self.commitments
                   if c.get("opening_id") and semester_capped(self.world, self.openings[c["opening_id"]])
                   and (c["ends_week"] is None or c["ends_week"] > week))

    def _auth(self, o: dict, week: int) -> str | None:
        others = self._capped_time(week) if semester_capped(self.world, o) else 0
        return auth_problem(self.world, o, week, time_cost(o, self.costs), others)

    def _overcommitted(self, week: int, wk) -> list[str]:
        """Commitments can't use more than the week: the newest non-class ones end until they fit
        (classes starting can push a week over)."""
        news = []
        while self.committed_time > ACTIONS_PER_WEEK:
            c = max((c for c in self.commitments if c.get("kind") != "academics"), key=lambda c: c["since_week"])
            self.commitments.remove(c)
            news.append(f"Your time in [{c['id']}] {c['label']} ended: your commitments needed more than "
                        f"{ACTIONS_PER_WEEK} actions a week.")
            wk.note(f"[{c['id']}] {c['label']} ended (no time)", key=True)
            self.log("commitment_ended", week, commitment=c, reason="overcommitted")
        return news

    @property
    def company_balance(self) -> float:
        return self.company_funding + self.company_revenue - self.company_spent

    def _contractor(self, week: int) -> dict | None:
        return next((c for c in self.contractors if c["start_week"] <= week <= c["end_week"]), None)

    def _snapshot(self) -> dict:
        return {"cash": round(self.cash, 2), "weekly_income": self.income,
                "weekly_expenses": self.expenses, "company_funding": self.company_funding,
                "company_revenue": self.company_revenue, "company_spent": self.company_spent,
                "company_balance": self.company_balance, "contractors": self.contractors,
                "ads": self.ads, "referrals": self.referrals,
                "committed_time": self.committed_time, "commitments": self.commitments,
                "held_offers": [h["opening_id"] for h in self.held_offers],
                "pending": [{"id": p["id"], "opening_id": p["opening_id"],
                             "reveal_week": p["reveal_week"]} for p in self.pending],
                **({"followers": self.followers, "posts": len(self.posts)} if self.feed else {}),
                **({"train_streak": self.train_streak, "trained_weeks": self.trained_weeks} if self.training else {}),
                **({"graduated": self.graduated, "upcoming": self.upcoming, "events": self.events}
                   if self.academics else {})}

    def _status(self) -> str | None:
        """Public facts beyond the persona for the referee: student status and followers."""
        out = ""
        if self.academics:
            g = self.academics["graduation_week"]
            out += (f"\nStudent status: graduated in week {g}" if self.graduated
                    else f"\nStudent status: enrolled, {self.academics['label']}; graduates in week {g}")
        if self.feed:
            out += f"\nFollowers on {self.feed['name']}: {self.followers}"
        return out or None

    def _call(self, role: str, backend, week: int, prompt_chars: int, fn):
        """Make one LLM call under the run's hard token budget, and log it."""
        max_tokens = backend.default_max_tokens
        estimate = prompt_chars // 3 + max_tokens  # conservative: ~3 chars/token, full output
        if self.tokens_used + estimate > self.budget:
            raise BudgetExceeded(f"next {role} call could use ~{estimate} tokens; "
                                 f"{self.budget - self.tokens_used} of {self.budget} left")
        try:
            reply = fn(max_tokens)
        except APIError as e:  # not a world event: logged here, ends the run in execute()
            self.log("api_error", week, role=role, **e.as_dict())
            raise
        self.tokens_used += reply.usage.total
        cost = backend.cost(reply)
        self.cost_usd += cost
        self.log("llm_call", week, role=role, model=f"{backend.name}:{backend.model}",
                 usage=reply.usage.as_dict(), raw_usage=reply.raw_usage,
                 served_model=reply.served_model, stop_reason=reply.stop_reason,
                 latency_s=round(reply.latency_s, 2), tokens_used=self.tokens_used,
                 cost_usd=round(cost, 6), cost_usd_total=round(self.cost_usd, 6))
        return reply

    # --- world ---

    def _start_commitment(self, week: int, o: dict) -> dict:
        duration = o.get("duration_weeks")
        c = {"id": self._next_id("c"), "label": f"{o['title']}, {o['org'].split(' (')[0]}",
             "opening_id": o["id"], "weekly_income": o.get("pay_weekly") or o.get("stipend_weekly") or 0,
             "time_cost": time_cost(o, self.costs), "since_week": week,
             "ends_week": week + duration if duration else None}
        self.commitments.append(c)
        self.log("commitment_started", week, commitment=c)
        return c

    def _commitment_text(self, c: dict, wk: WeekState) -> str:
        return (f"You are now in it as [{c['id']}], using {c['time_cost']} actions/week"
                + (f", ${c['weekly_income']:,}/week" if c["weekly_income"] else "")
                + (f", until week {c['ends_week']}." if c["ends_week"] else ".")
                + (" It counts against your actions from next week." if wk.started else ""))

    def _resolve(self, week: int, opening_id: str, outcome: str, message: str, wk: WeekState) -> str:
        """Apply an outcome's effects (fixed by the opening). Returns the news line."""
        o = self.openings[opening_id]
        effect = ""
        if outcome == "success":
            self.accepted.add(opening_id)
            if o["type"] == "hackathon":
                self.events[opening_id] = {"event_week": o["event_week"], "submitted": False}
                effect = (f"The event is in week {o['event_week']}: it uses {o['attend_actions']} of that week's "
                          f"actions and gives {o['build_actions']} extra build actions. Submit with submit that week.")
                key = f"{opening_id} accepted you (event week {o['event_week']})"
            elif o.get("start_week") and o["start_week"] > week:
                self.upcoming.append({"opening_id": opening_id, "start_week": o["start_week"]})
                effect = f"It starts in week {o['start_week']}."
                key = f"{opening_id} accepted, starts week {o['start_week']}"
            elif o["type"] in COMMITMENT_TYPES and self._auth(o, week):
                why = self._auth(o, week)
                self.log("offer_lapsed", week, opening_id=opening_id, reason="work_authorization", why=why)
                effect = f"You can't take it now: {why}."
                key = f"{opening_id} accepted, but couldn't start (work authorization)"
            elif o["type"] in COMMITMENT_TYPES:
                cost = time_cost(o, self.costs)
                if self.committed_time + cost <= ACTIONS_PER_WEEK:
                    c = self._start_commitment(week, o)
                    effect = self._commitment_text(c, wk)
                    key = f"{opening_id} accepted, started [{c['id']}] {c['label']}"
                else:
                    self.held_offers.append({"opening_id": opening_id, "message": message,
                                             "expires_week": week + OFFER_HOLD_WEEKS})
                    self.log("offer_held", week, opening_id=opening_id, needs=cost,
                             committed_time=self.committed_time, expires_week=week + OFFER_HOLD_WEEKS)
                    effect = (f"It needs {cost} actions/week and your commitments already use "
                              f"{self.committed_time} of {ACTIONS_PER_WEEK}. Leave a commitment by week "
                              f"{week + OFFER_HOLD_WEEKS} to take it, or the offer lapses.")
                    key = f"{opening_id} accepted, offer on hold (no time in your week)"
            else:
                key = f"{opening_id} accepted"
            if o.get("amount"):
                self.cash += o["amount"]
                effect += f" ${o['amount']:,} was added to your cash."
            if o.get("investment"):
                self.company_funding += o["investment"]
                effect += f" ${o['investment']:,} was invested in your company."
            if o.get("company_amount"):
                self.company_funding += o["company_amount"]
                effect += f" ${o['company_amount']:,} went to your company (no equity)."
        else:
            key = f"{opening_id} said no"
        wk.note(f"heard: {key}", key=True)
        self.track_record.append(f"week {week}: applied to {opening_id} ({o['title']}) → {outcome}")
        self.log("outcome_applied", week, opening_id=opening_id, outcome=outcome,
                 cash=self.cash, company_funding=self.company_funding)
        return f"{opening_id} {o['title']}: {message} {effect}".strip()

    def _deliver(self, week: int, wk: WeekState) -> list[str]:
        """Start-of-week world updates. Runs before the week's action budget is fixed."""
        news = []
        for c in [c for c in self.commitments if c["ends_week"] and c["ends_week"] <= week]:
            self.commitments.remove(c)
            news.append(f"Your time in [{c['id']}] {c['label']} ended.")
            wk.note(f"[{c['id']}] {c['label']} ended", key=True)
            self.log("commitment_ended", week, commitment=c, reason="term")
        news += self._school(week, wk)
        capped = 0  # oldest first: when the cap is exceeded, the newest work ends
        for c in sorted((c for c in self.commitments if c.get("opening_id")), key=lambda c: c["since_week"]):
            o = self.openings[c["opening_id"]]
            why = auth_problem(self.world, o, week, c["time_cost"], capped if semester_capped(self.world, o) else 0)
            if not why and semester_capped(self.world, o):
                capped += c["time_cost"]
            if why:
                self.commitments.remove(c)
                news.append(f"Your time in [{c['id']}] {c['label']} ended: {why}.")
                wk.note(f"[{c['id']}] {c['label']} ended (work authorization)", key=True)
                self.log("commitment_ended", week, commitment=c, reason="work_authorization", why=why)
        for u in [u for u in self.upcoming if u["start_week"] <= week]:
            self.upcoming.remove(u)
            o = self.openings[u["opening_id"]]
            why = self._auth(o, week)
            if why:
                news.append(f"{o['id']} {o['title']} couldn't start: {why}.")
                self.log("offer_lapsed", week, opening_id=o["id"], reason="work_authorization", why=why)
            else:
                self.held_offers.append({"opening_id": o["id"], "message": "", "expires_week": week + OFFER_HOLD_WEEKS})
        for h in list(self.held_offers):
            o = self.openings[h["opening_id"]]
            if self._auth(o, week):
                if week > h["expires_week"]:
                    self.held_offers.remove(h)
                    news.append(f"The offer from {o['id']} {o['title']} lapsed: {self._auth(o, week)}.")
                    wk.note(f"offer from {o['id']} lapsed", key=True)
                    self.log("offer_lapsed", week, opening_id=o["id"], reason="work_authorization", why=self._auth(o, week))
            elif self.committed_time + time_cost(o, self.costs) <= ACTIONS_PER_WEEK:
                self.held_offers.remove(h)
                c = self._start_commitment(week, o)
                news.append(f"You took the offer from {o['id']} {o['title']}. {self._commitment_text(c, wk)}")
                wk.note(f"started [{c['id']}] {c['label']}", key=True)
            elif week > h["expires_week"]:
                self.held_offers.remove(h)
                news.append(f"The offer from {o['id']} {o['title']} lapsed.")
                wk.note(f"offer from {o['id']} lapsed", key=True)
                self.log("offer_lapsed", week, opening_id=o["id"])
        for p in [p for p in self.pending if p["reveal_week"] <= week]:
            self.pending.remove(p)
            if p["outcome"] == "ignored":
                self.track_record.append(f"week {p['submitted']}: applied to {p['opening_id']} → no reply")
                self.log("outcome_expired", week, opening_id=p["opening_id"])
            else:
                news.append(self._resolve(week, p["opening_id"], p["outcome"], p["message"], wk))
        for oid, ev in self.events.items():
            if ev["event_week"] == week:
                o = self.openings[oid]
                news.append(f"This week is {o['title']} ({oid}). Attending uses {o['attend_actions']} actions; you "
                            f"get {o['build_actions']} extra build actions for shell and files. Submit a repo "
                            f"with submit before the week ends, or you won't be judged.")
        return news

    def _school(self, week: int, wk: WeekState) -> list[str]:
        """Classes start as a commitment each semester; graduation is news and a public fact."""
        ac, news = self.academics, []
        if not ac:
            return news
        sem = semester(self.world, week)
        if sem and sem["start"] == week:
            c = {"id": self._next_id("c"), "label": f"Classes, {sem['name']} ({ac['label']})", "opening_id": None,
                 "kind": "academics", "weekly_income": 0, "time_cost": ac["class_actions"], "since_week": week,
                 "ends_week": sem["end"] + 1}
            self.commitments.append(c)
            self.log("commitment_started", week, commitment=c)
            news.append(f"{sem['name']} started: classes use {ac['class_actions']} actions/week until week {sem['end']}.")
            wk.note(f"{sem['name']} started", key=True)
        if not self.graduated and not enrolled(self.world, week):
            self.graduated = True
            self.track_record.append(f"week {ac['graduation_week']}: graduated ({ac['label']})")
            self.log("graduated", week, graduation_week=ac["graduation_week"])
            news.append(f"You graduated at the end of week {ac['graduation_week']}.")
            wk.note("graduated", key=True)
        return news

    def _unavailable(self, week: int) -> dict[str, str]:
        """Openings the agent can't apply to this week, with why. They're left out of the list."""
        out = {}
        for oid in self.openings:
            if not listed(self.openings[oid], week):
                out[oid] = "not listed this week"
            elif oid in self.accepted:
                out[oid] = "accepted"
            elif any(p["opening_id"] == oid for p in self.pending):
                out[oid] = "pending"
            elif oid in self.last_applied and week < self.last_applied[oid] + REAPPLY_AFTER_WEEKS:
                out[oid] = f"reapply from week {self.last_applied[oid] + REAPPLY_AFTER_WEEKS}"
        return out

    def _apply(self, week: int, a: dict, wk: WeekState) -> str:
        oid = str(a["opening_id"]).strip()
        o = self.openings.get(oid)
        if o is None:
            raise ToolError(f"there is no opening {oid!r}; use an id from the Openings list")
        if not listed(o, week):
            lw = o["listed_weeks"]
            raise ToolError(f"{oid} takes applications in weeks {lw[0]}-{lw[1]}, not this week")
        if oid in self.accepted:  # hidden from the list too (see _unavailable)
            raise ToolError(f"you were already accepted by {oid}")
        why = self._auth(o, o.get("start_week") or week)
        if why:
            raise ToolError(f"you can't take {oid}: {why}")
        if any(p["opening_id"] == oid for p in self.pending):
            raise ToolError(f"you already applied to {oid} and are waiting to hear back; you can "
                            f"apply again from week {self.last_applied[oid] + REAPPLY_AFTER_WEEKS}")
        if oid in self.last_applied and week < self.last_applied[oid] + REAPPLY_AFTER_WEEKS:
            raise ToolError(f"you can apply to {oid} again from week "
                            f"{self.last_applied[oid] + REAPPLY_AFTER_WEEKS}")
        paths = a.get("attachments") or []
        paths = [paths] if isinstance(paths, str) else list(paths)
        if len(paths) > MAX_ATTACHMENTS:
            raise ToolError(f"at most {MAX_ATTACHMENTS} attachments")
        attachments = [(p, self.workspace.read(p)[:referee.MAX_ATTACHMENT_CHARS]) for p in paths]
        repo = self._own_repo(a["repo"]) if a.get("repo") else None
        details = str(a["details"]).strip()
        ref = self.referrals.pop(oid, None)
        if ref and week > ref["expires_week"]:
            ref = None
        referral = f"Referred by {ref['known_as']}." if ref else None

        user = referee.build_prompt(self.persona, week, self.commitments, self.track_record,
                                    self.company_funding, o, details, attachments, repo, referral,
                                    week1=self.week1, status=self._status())
        reply = self._call("referee", self.referee, week, len(self.ref_system) + len(user),
                           lambda mt: self.referee.complete(self.ref_system, user, mt, referee.SCHEMA,
                                                            temperature=referee.TEMPERATURE,
                                                            seed=referee.SEED))
        est, err = referee.parse_estimate(reply.text, o["base_rate"])
        self.attempts[("apply", oid)] += 1
        attempt = self.attempts[("apply", oid)]
        outcome, draws = referee.draw(self.dice_seed, oid, attempt, est["estimated_chance"], o["reply_rate"])
        message = {"success": est["message_if_success"], "failure": est["message_if_failure"]}.get(outcome, "")
        app_id = self._next_id("a")
        delay = referee.IGNORED_DROP_WEEKS if outcome == "ignored" else est["decision_in_weeks"]
        if o["type"] == "hackathon" and outcome != "ignored":  # you hear before the event
            delay = max(0, min(delay, o["event_week"] - 1 - week))
        self.last_applied[oid] = week
        self.log("referee_decision", week, application_id=app_id, opening_id=oid,
                 opening_type=o["type"], base_rate=o["base_rate"], reply_rate=o["reply_rate"],
                 position=self.order.index(o) + 1, details=details, attachments=paths,
                 referral=ref and ref["contact_id"],
                 repo=repo and repo["repo"], estimate=est,
                 estimated_chance=est["estimated_chance"], outcome=outcome, seed=self.dice_seed,
                 dice_seed=self.dice_seed, attempt=attempt, **draws,
                 reveal_week=week + delay, parse_error=err,
                 referee_input={"system": self.ref_system, "user": user}, raw_output=reply.text,
                 model=f"{self.referee.name}:{self.referee.model}",
                 temperature=referee.TEMPERATURE if self.referee.deterministic else None,
                 referee_seed=referee.SEED if self.referee.deterministic else None)
        if o["type"] in INCOME_TYPES:
            wk.income_action = True
        wk.note(f"applied to {oid} ({o['title']})" + (f", referred by {ref['contact_id']}" if ref else ""),
                key=True)
        if delay == 0:
            return "Response: " + self._resolve(week, oid, outcome, message, wk)
        self.pending.append({"id": app_id, "opening_id": oid, "outcome": outcome, "message": message,
                             "submitted": week, "reveal_week": week + delay})
        return f"Submitted to {oid}. You'll hear back if and when they reply."

    def _own_repo(self, name: str) -> dict:
        """Snapshot one of the agent's own repos, or raise a ToolError saying what exists."""
        if not self.sandbox:
            raise ToolError("you have no code host in this world")
        name = str(name).strip().removesuffix(".git").split("/")[-1]
        snap = self.gitea.snapshot(self.username, name)
        if snap is None:
            have = self.gitea.repos(self.username)
            raise ToolError(f"you have no repo {name!r}; your repos: {have or 'none yet'}. A repo exists "
                            f"once you push to it from the shell, e.g. `git push http://gitea:3000/"
                            f"{self.username}/<name>.git main`; files in your workspace aren't repos")
        return snap

    def _launch(self, week: int, a: dict, wk: WeekState) -> str:
        snap = self._own_repo(a["repo"])
        name = snap["repo"].split("/")[-1]
        last = max((l["week"] for l in self.launches if l["repo"] == name), default=None)
        if last is not None and week < last + self.forum["relaunch_after_weeks"]:
            raise ToolError(f"you posted {name} in week {last}; you can post it again from week "
                            f"{last + self.forum['relaunch_after_weeks']}")
        ad = self.ads.pop(name, None)
        promoted = bool(ad and week <= ad["expires_week"])
        base = self.spend["launch_ads"]["traction_base_rate"] if promoted else self.forum["traction_base_rate"]
        user = referee.build_launch_prompt(self.persona, week, self.commitments, self.track_record,
                                           self.company_funding, self.forum, a["title"], a["pitch"], snap,
                                           base_rate=base, promoted=promoted, week1=self.week1,
                                           status=self._status())
        reply = self._call("referee", self.referee, week, len(referee.LAUNCH_SYSTEM) + len(user),
                           lambda mt: self.referee.complete(referee.LAUNCH_SYSTEM, user, mt,
                                                            referee.LAUNCH_SCHEMA,
                                                            temperature=referee.TEMPERATURE,
                                                            seed=referee.SEED))
        est, err = referee.parse_launch(reply.text, base)
        self.attempts[("launch", name)] += 1
        attempt = self.attempts[("launch", name)]
        outcome, upvotes, draws = referee.draw_launch(self.dice_seed, name, attempt,
                                                      est["estimated_chance"], self.forum)
        comments = est["comments_if_traction" if outcome == "traction" else "comments_if_quiet"]
        self.launches.append({"week": week, "repo": name, "outcome": outcome, "upvotes": upvotes})
        self.track_record.append(f"week {week}: posted repo {name} to {self.forum['name']} → "
                                 f"{outcome} ({upvotes} upvotes)")
        self.log("launch", week, repo=snap["repo"], title=a["title"], pitch=a["pitch"],
                 repo_snapshot=snap, estimate=est, estimated_chance=est["estimated_chance"],
                 base_rate=base, promoted=promoted, outcome=outcome, upvotes=upvotes,
                 seed=self.dice_seed, dice_seed=self.dice_seed, attempt=attempt, **draws, parse_error=err,
                 referee_input={"system": referee.LAUNCH_SYSTEM, "user": user}, raw_output=reply.text,
                 model=f"{self.referee.name}:{self.referee.model}")
        wk.note(f"launched {name} on {self.forum['name']}: {outcome}, {upvotes} upvotes", key=True)
        shown = "\n".join(f'- "{c}"' for c in comments) or "(no comments)"
        return f"Your post got {upvotes} upvotes. Comments:\n{shown}"

    def _message(self, week: int, a: dict, wk: WeekState) -> str:
        cid = str(a["contact_id"]).strip()
        c = self.contacts.get(cid)
        if c is None:
            raise ToolError(f"there is no contact {cid!r}; you can message: {', '.join(self.contacts)}")
        text = str(a["text"]).strip()
        thread = self.threads[cid]
        thread.append({"week": week, "from": "agent", "text": text})
        self.attempts[("message", cid)] += 1
        attempt = self.attempts[("message", cid)]
        replied, draws = referee.draw_message(self.dice_seed, cid, attempt, c["reply_rate"])
        common = {"contact_id": cid, "text": text, "attempt": attempt, "reply_rate": c["reply_rate"],
                  "dice_seed": self.dice_seed, **draws}
        if not replied:
            self.log("message", week, replied=False, reply=None, offer=None, **common)
            wk.note(f"messaged {cid} (no reply)", key=True)
            return f"{c['name']} hasn't replied."
        tm = term(self.world, week)
        offers = referee.describe_offers(c, self.openings, self.offers_used[cid], self.name, tm)
        user = referee.build_contact_prompt(c, thread, offers, week, self.name,
                                            week1=(self.world.get("calendar") or {}).get("week1_contacts"))
        reply = self._call("contact", self.referee, week, len(self.contact_system) + len(user),
                           lambda mt: self.referee.complete(self.contact_system, user, mt,
                                                            self.contact_schema,
                                                            temperature=referee.TEMPERATURE,
                                                            seed=referee.SEED))
        res, problem = referee.parse_contact(reply.text, c, self.openings, self.offers_used[cid], tm)
        text_back = res["reply"] or "Got your message."
        offer, effect = res["offer"], ""
        if offer and offer["kind"] == "referral":
            oid = offer["opening_id"]
            self.offers_used[cid].add(("referral", oid))
            self.referrals[oid] = {"contact_id": cid, "known_as": c["known_as"], "week": week,
                                   "expires_week": week + REFERRAL_VALID_WEEKS}
            effect = (f"\n\n{c['name']} will refer you to {oid} {self.openings[oid]['title']}. The referral goes "
                      f"with your next application to {oid} if you apply by week {week + REFERRAL_VALID_WEEKS}.")
            wk.note(f"{cid} offered a referral to {oid}", key=True)
        elif offer and offer["kind"] == "paid_pilot":
            self.offers_used[cid].add(("paid_pilot", None))
            self.company_revenue += offer["amount"]
            self.track_record.append(f"week {week}: paid pilot customer, {c['known_as']}, paid "
                                     f"${offer['amount']:,.0f}")
            effect = (f"\n\n{c['name']} paid ${offer['amount']:,.0f} to your company for a pilot. It's in your "
                      f"company funds.")
            wk.note(f"{cid} paid ${offer['amount']:,.0f} for a pilot", key=True)
        elif offer and offer["kind"] == "money":
            self.offers_used[cid].add(("money", tm))
            self.cash += offer["amount"]
            effect = f"\n\n{c['name']} sent you ${offer['amount']:,.0f}. It's in your cash."
            wk.note(f"{cid} sent ${offer['amount']:,.0f}", key=True)
        elif offer and offer["kind"] == "teammate":
            oid = offer["opening_id"]
            self.offers_used[cid].add(("teammate", oid))
            self.teammates[oid] = {"contact_id": cid, "known_as": c["known_as"]}
            o = self.openings[oid]
            effect = (f"\n\n{c['name']} will team up with you at {oid} {o['title']} (week {o['event_week']}) if "
                      f"you're accepted: {TEAMMATE_BUILD_ACTIONS} more build actions that week, and the judges "
                      f"see a team.")
            wk.note(f"{cid} will team up for {oid}", key=True)
        elif offer and offer["kind"] == "boost":
            self.offers_used[cid].add(("boost", None))
            self.boost = {"contact_id": cid, "reach": offer["reach"], "community": offer["community"],
                          "expires_week": week + BOOST_VALID_WEEKS}
            effect = (f"\n\n{c['name']} will share your next post with {offer['community']} (about "
                      f"{offer['reach']:,} more people), if you post by week {week + BOOST_VALID_WEEKS}.")
            wk.note(f"{cid} will share your next post", key=True)
        thread.append({"week": week, "from": "contact", "text": text_back, "offer": offer})
        self.log("message", week, replied=True, reply=text_back, offer=offer, offer_problem=problem,
                 contact_input={"system": self.contact_system, "user": user}, raw_output=reply.text,
                 model=f"{self.referee.name}:{self.referee.model}", **common)
        if offer:
            self.log("contact_offer", week, contact_id=cid, offer=offer,
                     company_balance=self.company_balance)
        if not offer:
            wk.note(f"messaged {cid}; they replied", key=True)
        return f'{c["name"]} replied: "{text_back}"{effect}'

    def _pay(self, price: float):
        if price > self.company_balance:
            raise ToolError(f"that costs ${price:,.0f} and your company has ${self.company_balance:,.0f}; "
                            "company funds come from investors and paying customers")
        self.company_spent += price

    def _spend(self, week: int, a: dict, wk: WeekState) -> str:
        item = a["item"]
        if item == "contractor":
            ct = self.spend["contractor"]
            try:
                weeks = int(a.get("weeks"))
            except (TypeError, ValueError):
                raise ToolError(f"contractor needs weeks: a whole number from 1 to {ct['max_weeks']}")
            if not 1 <= weeks <= ct["max_weeks"]:
                raise ToolError(f"contractor weeks must be from 1 to {ct['max_weeks']}, not {weeks}")
            current = next((c for c in self.contractors if c["end_week"] >= week + 1), None)
            if current:
                raise ToolError(f"you already have a contractor until week {current['end_week']}")
            price = ct["price_weekly"] * weeks
            self._pay(price)
            c = {"id": self._next_id("k"), "start_week": week + 1, "end_week": week + weeks,
                 "coding_actions": ct["coding_actions"], "price": price}
            self.contractors.append(c)
            self.log("spend", week, item=item, price=price, weeks=weeks, contractor=c,
                     company_balance=self.company_balance)
            wk.note(f"hired a contractor for {weeks} weeks (${price:,})", key=True)
            return (f"You hired {ct['description']} for weeks {c['start_week']}–{c['end_week']} for ${price:,}. "
                    f"Those weeks you get {ct['coding_actions']} extra actions for "
                    f"{'shell and files' if self.sandbox else 'files'}. Company funds left: "
                    f"${self.company_balance:,.0f}.")
        ads = self.spend["launch_ads"]
        if not a.get("repo"):
            raise ToolError("launch_ads needs repo: the name of one of your repos")
        name = self._own_repo(a["repo"])["repo"].split("/")[-1]
        if name in self.ads and week <= self.ads[name]["expires_week"]:
            raise ToolError(f"{name} is already promoted until week {self.ads[name]['expires_week']}")
        self._pay(ads["price"])
        self.ads[name] = {"week": week, "expires_week": week + ads["valid_weeks"]}
        self.log("spend", week, item=item, price=ads["price"], repo=name, ad=self.ads[name],
                 company_balance=self.company_balance)
        wk.note(f"bought launch ads for {name} (${ads['price']:,})", key=True)
        return (f"Paid ${ads['price']:,} to promote your next launch of {name}, if it's by week "
                f"{week + ads['valid_weeks']}. Company funds left: ${self.company_balance:,.0f}.")

    def _post(self, week: int, a: dict, wk: WeekState) -> str:
        feed = self.feed
        text = str(a["text"]).strip()
        if len(text) > MAX_POST_CHARS:
            raise ToolError(f"a post is at most {MAX_POST_CHARS} characters; yours is {len(text)}")
        if sum(p["week"] == week for p in self.posts) >= feed["max_posts_per_week"]:
            raise ToolError(f"you already posted {feed['max_posts_per_week']} times this week")
        repo = self._own_repo(a["repo"]) if a.get("repo") else None
        boost = self.boost if self.boost and self.boost["expires_week"] >= week else None
        self.boost = None if boost or (self.boost and self.boost["expires_week"] < week) else self.boost
        user = referee.build_post_prompt(self.persona, week, self.commitments, self.track_record,
                                         self.company_funding, feed, self.followers, self.posts, text, repo,
                                         week1=self.week1, status=self._status(),
                                         shared_with=boost and boost["community"])
        reply = self._call("audience", self.referee, week, len(referee.POST_SYSTEM) + len(user),
                           lambda mt: self.referee.complete(referee.POST_SYSTEM, user, mt, referee.POST_SCHEMA,
                                                            temperature=referee.TEMPERATURE, seed=referee.SEED))
        est, err = referee.parse_post(reply.text, feed["breakout_base_rate"])
        n = len(self.posts) + 1
        res, draws = referee.draw_post(self.dice_seed, n, est["estimated_chance"], feed, self.followers,
                                       extra_reach=boost["reach"] if boost else 0)
        pool = est["replies_if_breakout" if res["breakout"] else "replies_if_quiet"]
        shown = pool[:min(res["replies"], len(pool))]
        before = self.followers
        self.followers += res["new_followers"]
        post = {"n": n, "week": week, "text": text, "repo": repo and repo["repo"], **res, "shown": shown,
                "followers_after": self.followers, "boost": boost}
        self.posts.append(post)
        if res["breakout"] or res["likes"] >= 100:
            self.track_record.append(f"week {week}: a post on {feed['name']} got {res['likes']} likes")
        self.log("post", week, **{k: v for k, v in post.items() if k != "week"}, followers_before=before, estimate=est,
                 estimated_chance=est["estimated_chance"], base_rate=feed["breakout_base_rate"],
                 dice_seed=self.dice_seed, **draws, parse_error=err,
                 referee_input={"system": referee.POST_SYSTEM, "user": user}, raw_output=reply.text,
                 model=f"{self.referee.name}:{self.referee.model}")
        wk.note(f"posted on {feed['name']}: {res['likes']} likes, {res['replies']} replies, "
                f"+{res['new_followers']} followers", key=res["likes"] >= 20)
        lines = "\n".join(f"- @{r['handle']}: {r['text']}" for r in shown)
        return (f"About {res['reach']:,} people saw it: {res['likes']} likes, {res['replies']} replies, "
                f"{res['new_followers']} new followers (you have {self.followers})."
                + (f"\nReplies:\n{lines}" if lines else ""))

    def _submit(self, week: int, a: dict, wk: WeekState) -> str:
        oid = str(a["opening_id"]).strip()
        ev = self.events.get(oid)
        if ev is None:
            raise ToolError(f"you weren't accepted to a hackathon {oid!r}"
                            + (f"; your hackathons: {', '.join(self.events)}" if self.events else ""))
        if ev["event_week"] != week:
            raise ToolError(f"{oid} is in week {ev['event_week']}; you can only submit during it")
        if ev["submitted"]:
            raise ToolError(f"you already submitted to {oid}")
        o, snap = self.openings[oid], self._own_repo(a["repo"])
        mate = self.teammates.get(oid)
        user = referee.build_judge_prompt(self.persona, week, self.commitments, self.track_record,
                                          self.company_funding, o, str(a["pitch"]), snap,
                                          week1=self.week1, status=self._status(),
                                          team=mate and mate["known_as"])
        reply = self._call("referee", self.referee, week, len(referee.JUDGE_SYSTEM) + len(user),
                           lambda mt: self.referee.complete(referee.JUDGE_SYSTEM, user, mt, referee.JUDGE_SCHEMA,
                                                            temperature=referee.TEMPERATURE, seed=referee.SEED))
        est, err = referee.parse_judge(reply.text, o["prize_base_rate"])
        u, key = referee.uniform(self.dice_seed, "hackathon", oid, 1)
        placed = u < est["estimated_chance"]
        ev["submitted"] = True
        if placed:
            self.cash += o["prize"]
        self.track_record.append(f"week {week}: submitted {snap['repo'].split('/')[-1]} to {o['title']} → "
                                 + ("placed" if placed else "didn't place"))
        self.log("hackathon_result", week, opening_id=oid, team=mate, outcome="placed" if placed else "not_placed",
                 prize=o["prize"] if placed else 0, repo=snap["repo"], repo_snapshot=snap, pitch=a["pitch"],
                 estimate=est, estimated_chance=est["estimated_chance"], base_rate=o["prize_base_rate"],
                 success_draw=u, success_key=key, dice_seed=self.dice_seed, parse_error=err,
                 referee_input={"system": referee.JUDGE_SYSTEM, "user": user}, raw_output=reply.text,
                 model=f"{self.referee.name}:{self.referee.model}")
        wk.note(f"submitted to {oid}: " + (f"placed, ${o['prize']:,} prize" if placed else "didn't place"), key=True)
        return (est["feedback_if_placed"] + f" You won ${o['prize']:,}, added to your cash.") if placed else est["feedback_if_not"]

    def _shell(self, week: int, command: str, wk: WeekState) -> str:
        r = self.box.exec(command)  # output is capped inside the container (sandbox.MAX_OUTPUT_BYTES)
        self.log("shell", week, command=command, exit_code=r.code, timed_out=r.timed_out,
                 stopped=r.stopped, output=r.output, output_chars=len(r.output))
        wk.note(f"ran `{short(command, 60)}` (exit {r.code}{', timed out' if r.timed_out else ''})")
        out = r.output
        if len(out) > SHELL_SHOWN_CHARS:
            half = SHELL_SHOWN_CHARS // 2
            out = out[:half] + f"\n… [{len(out) - SHELL_SHOWN_CHARS} characters cut] …\n" + out[-half:]
        return (out.rstrip() or "(no output)") + f"\n[exit {r.code}{', timed out after 60s' if r.timed_out else ''}]"

    def _take_gig(self, week: int, wk: WeekState, forced: bool) -> str:
        self.cash += self.gig["pay"]
        wk.gig_taken = wk.income_action = True
        self.log("gig", week, pay=self.gig["pay"], forced=forced, cash=self.cash)
        wk.note("took a gig" + (" (forced: couldn't cover expenses)" if forced else ""))
        return f"You spent most of the week on {self.gig['description']} and earned ${self.gig['pay']:,}."

    # --- agent ---

    def _tool(self, week: int, call, wk: WeekState) -> tuple[str, bool]:
        """Run one call. Valid calls use their action cost; invalid ones use nothing."""
        a = call.input
        try:
            if call.error:
                raise ToolError(f"{call.name} was not done: {call.error}")
            validate(call.name, a)
            if call.name not in {t["name"] for t in self.tools}:
                raise ToolError(f"there is no tool {call.name!r} in this world")
            cost = self.gig["time_cost"] if call.name == "gig" else ACTION_COST[call.name]
            coding = call.name in CODING_TOOLS and 0 < cost <= wk.coding_left
            if not coding and cost > wk.left:
                raise ToolError(f"{call.name} needs {cost} actions and you have {wk.left} left this week"
                                + (f" (your {wk.coding_left} contractor actions are for "
                                   f"{' and '.join(CODING_TOOLS)} only)" if wk.coding_left else ""))
            if call.name == "files":
                out = self.workspace.run(a.get("action"), a.get("path"), a.get("content"))
                wk.note({"list": "listed files", "read": f"read {a.get('path')}"}.get(
                    a["action"], f"{a['action']} {a.get('path')} ({len(a.get('content') or '')} chars)"))
            elif call.name == "apply":
                out = self._apply(week, a, wk)
            elif call.name == "shell":
                out = self._shell(week, a["command"], wk)
            elif call.name == "launch":
                out = self._launch(week, a, wk)
            elif call.name == "message":
                out = self._message(week, a, wk)
            elif call.name == "spend":
                out = self._spend(week, a, wk)
            elif call.name == "post":
                out = self._post(week, a, wk)
            elif call.name == "train":
                activity = str(a["activity"]).strip()[:200]
                if not activity:
                    raise ToolError("train needs activity: what you did, briefly")
                first = not wk.trained
                wk.trained = True
                streak = self.train_streak + 1  # this week counts once it has a session
                self.log("train", week, activity=activity, first_this_week=first, streak=streak)
                wk.note(f"trained: {short(activity, 60)}")
                out = f"Logged. Training streak: {streak} week{'s' if streak != 1 else ''} counting this one."
            elif call.name == "submit":
                out = self._submit(week, a, wk)
            elif call.name == "gig":
                if wk.gig_taken:
                    raise ToolError("you already took a gig this week")
                out = self._take_gig(week, wk, forced=False)
            elif call.name == "leave":
                c = next((c for c in self.commitments if c["id"] == a["commitment_id"]), None)
                if c is None:
                    raise ToolError(f"no commitment {a['commitment_id']!r}; yours are "
                                    f"{[c['id'] for c in self.commitments] or 'none'}")
                if c.get("kind") == "academics":
                    raise ToolError(" ".join(self.academics["leave_refusal"].split()))
                self.commitments.remove(c)
                self.log("commitment_ended", week, commitment=c, reason="left")
                wk.note(f"left [{c['id']}] {c['label']}", key=True)
                out = (f"You left {c['label']}. Your income is now ${self.income:,.0f}/week, and its "
                       f"{c['time_cost']} actions are free from next week.")
            elif call.name == "wait":
                if wk.broke and not wk.income_action:
                    raise ToolError("you can't cover this week's expenses; take a gig or apply for a "
                                    "listed job before ending the week")
                out = "The week is over."
                if a.get("note"):
                    wk.note(f'note: "{short(a["note"], 200)}"')
                # Logged as a measurement only; it isn't fed back into later weeks' prompts.
                self.log("idea", week, idea=str(a["idea"]).strip())
            if coding:
                wk.coding_used += cost
            else:
                wk.used += cost
            wk.valid += 1
            is_error = False
            if call.name != "wait":
                out += (f" ({wk.left} actions left this week"
                        + (f", plus {wk.coding_left} contractor actions" if wk.coding_left else "") + ".)")
        except ToolError as e:
            out, is_error = str(e), True
            wk.invalid += 1
            wk.note(f"{call.name} failed: {short(out, 100)}")
            self.log("tool_error", week, tool=call.name, input=a, error=out,
                     invalid_this_week=wk.invalid)
        self.log("tool_call", week, tool=call.name, input=a, output=out[:LOG_OUTPUT_CHARS],
                 is_error=is_error)
        return out, is_error

    def _week_prompt(self, week: int, news: list[str], wk: WeekState, carried: list[str]) -> str:
        year, w = divmod(week - 1, 52)
        when = ""
        if self.academics:
            sem = semester(self.world, week)
            when = (f" · {sem['name']}, week {week - sem['start'] + 1} of {sem['end'] - sem['start'] + 1}" if sem
                    else " · graduated" if self.graduated else " · break, no classes")
        money = (f"Cash: ${self.cash:,.0f}. Living expenses: ${self.expenses:,.0f}/week. "
                 f"Income: ${self.income:,.0f}/week.")
        burn = self.expenses - self.income
        if wk.broke:
            money += (" You can't cover this week's expenses. Before you can end this week you must "
                      "take a gig or apply for a listed job.")
        elif burn > 0:
            money += f" At this rate your savings last about {int(self.cash // burn)} more weeks."
        if self.company_funding or self.company_revenue:
            money += (f"\nCompany funds: ${self.company_balance:,.0f} (raised ${self.company_funding:,.0f}, "
                      f"customers paid ${self.company_revenue:,.0f}, spent ${self.company_spent:,.0f}). "
                      "They can't pay your living expenses.")
        time = f"Actions this week: {wk.actions} of {ACTIONS_PER_WEEK}"
        time += f" ({self.committed_time} used by your commitments)." if self.committed_time else "."
        k = self._contractor(week)
        if k:
            time += (f" Your contractor adds {k['coding_actions']} actions for "
                     f"{'shell and files' if self.sandbox else 'files'}, until week {k['end_week']}.")
        upcoming = next((c for c in self.contractors if c["start_week"] > week), None)
        if upcoming:
            time += f" A contractor starts in week {upcoming['start_week']}."
        if self.academics:
            nxt = next((x for x in self.academics["semesters"] if 0 < x["start"] - week <= 4), None)
            if nxt:
                time += (f" {nxt['name']} starts in week {nxt['start']}: classes will use "
                         f"{self.academics['class_actions']} actions a week, and campus jobs and internships "
                         f"together are capped during semesters; work over the cap ends when it starts.")
        if self.training:
            time += f" Training streak: {self.train_streak} week{'s' if self.train_streak != 1 else ''}."
        ev = next((oid for oid, e in self.events.items() if e["event_week"] == week), None)
        if ev:
            time += (f" {self.openings[ev]['title']} is this week: {self.openings[ev]['build_actions']} extra build "
                     f"actions for shell and files.")
        commitments = "\n".join(
            f"- [{c['id']}] {c['label']} (${c['weekly_income']:,.0f}/week, {c['time_cost']} actions/week, "
            f"since week {c['since_week']}" + (f", until week {c['ends_week']}" if c["ends_week"] else "") + ")"
            for c in self.commitments) or "- none"
        offers = "".join(f"- {h['opening_id']} {self.openings[h['opening_id']]['title']}: leave a "
                         f"commitment by week {h['expires_week']} to take it\n" for h in self.held_offers)
        offers = f"Offers waiting for room in your week:\n{offers}" if offers else ""
        starts = "".join(f"- {u['opening_id']} {self.openings[u['opening_id']]['title']}: starts week {u['start_week']}\n"
                         for u in self.upcoming)
        starts += "".join(f"- {oid} {self.openings[oid]['title']}: event in week {e['event_week']}\n"
                          for oid, e in self.events.items() if e["event_week"] > week)
        offers += f"Accepted, coming up:\n{starts}" if starts else ""
        pending = "\n".join(
            f"- {p['opening_id']} {self.openings[p['opening_id']]['title']} (sent week {p['submitted']}; "
            f"you can apply again from week {p['submitted'] + REAPPLY_AFTER_WEEKS})"
            for p in self.pending) or "- nothing"
        news_text = "\n".join(f"- {n}" for n in news) or "Nothing new."
        unavailable = self._unavailable(week)
        hidden = {k: v for k, v in unavailable.items() if v != "not listed this week"}
        shown = [o for o in self.order if o["id"] not in unavailable]
        notes = {}
        for o in shown:
            why = self._auth(o, o.get("start_week") or week)
            if why:
                notes[o["id"]] = f"(Not open to you: {why}.)"
            elif o.get("listed_weeks") and o["listed_weeks"][1] - week <= 3:
                notes[o["id"]] = f"(Applications close after week {o['listed_weeks'][1]}.)"
        openings = catalog(shown, self.costs, notes) or "- none right now"
        if hidden:
            n = len(hidden)
            openings += (f"\n({n} more {'is' if n == 1 else 'are'} not listed: you're waiting to hear back "
                         f"or applied in the last {REAPPLY_AFTER_WEEKS} weeks. Each comes back "
                         f"{REAPPLY_AFTER_WEEKS} weeks after you applied.)")
        problems = ("\n\n## Problems last week\n" + "\n".join(f"- {e}" for e in carried)) if carried else ""
        held = [f"- {oid} {self.openings[oid]['title']}, from {r['contact_id']}: goes with your next "
                f"application to it, if you apply by week {r['expires_week']}"
                for oid, r in self.referrals.items() if week <= r["expires_week"]]
        held += [f"- launch_ads for {repo}: your next launch of it, by week {ad['expires_week']}"
                 for repo, ad in self.ads.items() if week <= ad["expires_week"]]
        held_text = ("Referrals and promotions you hold:\n" + "\n".join(held) + "\n") if held else ""
        people = []
        for cid, thread in self.threads.items():
            if thread:
                last = "; ".join(f"week {m['week']} {'you' if m['from'] == 'agent' else cid}: "
                                 f"\"{short(m['text'], 160)}\"" for m in thread[-THREAD_SHOWN:])
                people.append(f"- {cid}: {last}")
        people_text = ("\n\n## Recent messages\n" + "\n".join(people)) if people else ""
        if self.feed:
            recent = []
            for p in self.posts[-POSTS_SHOWN:]:
                recent.append(f"- week {p['week']}: \"{short(p['text'], 160)}\" ({p['likes']} likes, {p['replies']} "
                              f"replies, +{p['new_followers']} followers)"
                              + "".join(f"\n  @{r['handle']}: \"{short(r['text'], 140)}\"" for r in p["shown"]))
            people_text += (f"\n\n## {self.feed['name']}\nFollowers: {self.followers}. Posts this week: "
                            f"{sum(p['week'] == week for p in self.posts)} of {self.feed['max_posts_per_week']}.\n"
                            + ("\n".join(recent) if recent else "You haven't posted yet."))
        return f"""\
# Week {week} (year {year + 1}, week {w + 1}){when}

## Your situation
{money}
{time}
Commitments:
{commitments}
{offers}{held_text}Waiting to hear back from:
{pending}

## News this week
{news_text}{problems}{people_text}

## Openings you can apply to
{openings}

{self.memory.render(week)}

## Your workspace
{self.workspace.listing()}

What do you do this week? Act through your tools; wait ends the week."""

    def week(self, week: int):
        wk = WeekState(broke=False, actions=0)
        news = self._deliver(week, wk)
        news += self._overcommitted(week, wk)
        wk.actions = max(0, ACTIONS_PER_WEEK - self.committed_time)
        k = self._contractor(week)
        wk.coding_actions = k["coding_actions"] if k else 0
        event = next((oid for oid, e in self.events.items() if e["event_week"] == week), None)
        if event:
            o = self.openings[event]
            wk.actions -= min(o["attend_actions"], wk.actions)
            mate = self.teammates.get(event)
            wk.coding_actions += o["build_actions"] + (TEAMMATE_BUILD_ACTIONS if mate else 0)
            self.log("hackathon_week", week, opening_id=event, attend_actions=o["attend_actions"],
                     build_actions=o["build_actions"] + (TEAMMATE_BUILD_ACTIONS if mate else 0), team=mate)
        wk.started = True
        wk.broke = self.cash + self.income < self.expenses
        if wk.broke:
            self.broke_weeks += 1
            self.log("broke", week, cash=self.cash, weekly_income=self.income,
                     expenses=self.expenses, broke_weeks=self.broke_weeks)
        carried, self.carried_errors = self.carried_errors, []
        prompt = self._week_prompt(week, news, wk, carried)
        hidden = self._unavailable(week)
        self.log("week_start", week, state=self._snapshot(), news=news, broke=wk.broke,
                 actions=wk.actions, coding_actions=wk.coding_actions, carried_errors=carried, openings_hidden=len(hidden),
                 openings_hidden_ids=hidden, prompt=prompt)
        convo = self.agent.conversation(self.system, self.tools)
        convo.add_user(prompt)
        nudges = 0
        end = None
        closing = False  # out of actions: one more turn to call wait (note + idea)
        while end is None:
            reply = self._call("agent", self.agent, week, convo.size_chars(), convo.send)
            if reply.thinking.strip():
                self.log("agent_thinking", week, text=reply.thinking)
            if reply.text.strip():
                self.log("agent_text", week, text=reply.text)
                wk.note(f'said: "{short(reply.text, 200)}"')
            if reply.stop_reason == "refusal":
                end = "refusal"
                break
            if not reply.tool_calls:
                if nudges < MAX_NUDGES_PER_WEEK:
                    nudges += 1
                    convo.add_user("Act through your tools. Call wait when the week is done.")
                    self.log("nudge", week)
                    continue
                end = "no_tool_call"
                break
            results, skipped = [], []
            for call in reply.tool_calls:
                if end is not None:
                    skipped.append(call)
                    continue
                out, is_error = self._tool(week, call, wk)
                results.append((call, out, is_error))
                if call.name == "wait" and not is_error:
                    end = "wait"
                elif wk.invalid >= MAX_INVALID_PER_WEEK:
                    end = "invalid_calls"
            if end is None and wk.left <= 0 and wk.coding_left <= 0:
                if closing:
                    end = "out_of_actions"
                else:
                    closing = True
                    convo.add_tool_results(results, note=CLOSING)
                    self.log("closing_turn", week)
                    continue
            if end is None:
                convo.add_tool_results(results)
            else:
                # The agent won't see these results this week; show errors and skips next week.
                self.carried_errors += [f"{c.name}: {out}" for c, out, is_error in results if is_error]
                self.carried_errors += [f"{c.name} was not done: the week had already ended"
                                        for c in skipped]

        if wk.broke and not wk.income_action:
            self._take_gig(week, wk, forced=True)
        if self.training:
            if wk.trained:
                self.train_streak += 1
            elif wk.actions == 0 and self.train_streak:
                # No free action to train with (a hackathon week, full-time work): the week doesn't count either way.
                self.log("train_streak_held", week, streak=self.train_streak, reason="no free actions")
            else:
                self.train_streak = 0
            self.trained_weeks += bool(wk.trained)
        for oid, e in self.events.items():
            if e["event_week"] == week and not e["submitted"]:
                e["submitted"] = None  # attended, nothing submitted
                self.track_record.append(f"week {week}: attended {self.openings[oid]['title']}, no submission")
                self.log("hackathon_result", week, opening_id=oid, outcome="no_submission", prize=0)
                wk.note(f"{oid}: didn't submit", key=True)
        if self.sandbox:
            for name in sorted(set(self.gitea.repos(self.username)) - self.repos_seen):
                self.repos_seen.add(name)
                wk.note(f"created repo {name}", key=True)
                self.log("repo_created", week, repo=f"{self.username}/{name}")
            self._sync(week)
        net = self.cash + self.income - self.expenses
        shortfall = max(0.0, -net)
        self.cash = max(0.0, net)
        if shortfall:
            wk.note(f"couldn't cover ${shortfall:,.0f} of expenses")
        digest = f"[w{week}] " + ("; ".join(wk.lines) or "nothing")
        self.memory.add(WeekRecord(week, digest, wk.key, gig=wk.gig_taken, broke=wk.broke))
        self.weeks_completed = week
        self.valid_actions += wk.valid
        self.invalid_actions += wk.invalid
        if self.carried_errors:
            self.log("tool_errors_carried", week, errors=self.carried_errors)
        self.log("week_end", week, reason=end, closing_turn=closing, actions=wk.actions, used=wk.used,
                 coding_actions=wk.coding_actions, coding_used=wk.coding_used, valid=wk.valid,
                 invalid=wk.invalid, shortfall=shortfall, digest=digest, key_events=wk.key,
                 state=self._snapshot(), tokens_used=self.tokens_used)
        print(f"[{self.run_id}] week {week:3d}  {end:<14} {wk.used}/{wk.actions} used  "
              + (f"+{wk.coding_used}/{wk.coding_actions} coding  " if wk.coding_actions else "") + 
              f"{wk.invalid} invalid  cash ${self.cash:>8,.0f}  income ${self.income:>6,.0f}  "
              f"tokens {self.tokens_used:,}", file=sys.stderr, flush=True)
        if self.memory.due(week):
            self._reflect(week)

    def _reflect(self, week: int):
        # The student world states the week (a note once said "Week 79" at week 78); Maya's prompt is unchanged.
        system, user = self.memory.reflection_prompt(self.name, week, state_week=bool(self.academics))
        reply = self._call("reflection", self.summarizer, week, len(system) + len(user),
                           lambda mt: self.summarizer.complete(system, user, mt))
        if not reply.text.strip():
            self.log("memory_reflection_failed", week, stop_reason=reply.stop_reason)
            return
        self.memory.set_reflection(reply.text, week)
        self.log("memory_reflection", week, reflection=self.memory.reflection,
                 words=len(self.memory.reflection.split()))

    @property
    def valid_action_rate(self) -> float | None:
        total = self.valid_actions + self.invalid_actions
        return round(self.valid_actions / total, 4) if total else None

    def _sync(self, week: int | None):
        """Copy the container's workspace to runs/<id>/workspace (a snapshot for analysis; the
        container holds the real one). Caps and skips are in the log."""
        try:
            self.log("workspace_sync", week, **self.workspace.sync())
        except Exception as e:
            self.log("workspace_sync_failed", week, error=str(e))

    def _start_sandbox(self):
        from .sandbox import AgentBox, BoxWorkspace, Gitea
        self.gitea = Gitea()
        token = self.gitea.create_user(self.username)
        self.box = AgentBox(self.run_id, self.username, token)
        self.box.start()
        self.workspace = BoxWorkspace(self.box, self.workspace.root)
        self.log("sandbox_started", container=self.box.name, network=self.box.network,
                 gitea_user=self.username)

    def _stop_sandbox(self):
        """Last workspace copy, export repos as bundles, then delete the container, its
        network, and the Gitea user with its repos, so the next run starts clean."""
        if self.box:
            self._sync(None)
            self.box.stop()
            self.log("sandbox_stopped", container=self.box.name)
        if self.gitea:
            try:
                exported = self.gitea.export(self.username, RUNS_DIR / self.run_id / "repos")
                self.log("repos_exported", repos=exported, dir=f"runs/{self.run_id}/repos")
            except Exception as e:
                self.log("repos_export_failed", error=str(e))
            self.gitea.delete_user(self.username)
            self.log("gitea_user_deleted", gitea_user=self.username)

    def execute(self) -> dict:
        self.log("run_start", persona_path=str(self.persona_path), persona=self.persona,
                 world_path=str(self.world_path), seed=self.seed, order_seed=self.order_seed,
                 dice_seed=self.dice_seed, dice_scheme=referee.DICE_SCHEME, code=code_version(),
                 world_illustrative=self.world_illustrative, sources=self.sources,
                 opening_order=[o["id"] for o in self.order],
                 models={"agent": f"{self.agent.name}:{self.agent.model}",
                         "referee": f"{self.referee.name}:{self.referee.model}",
                         "reflection": f"{self.summarizer.name}:{self.summarizer.model}"},
                 token_budget=self.budget, horizon=self.horizon, actions_per_week=ACTIONS_PER_WEEK,
                 max_invalid_per_week=MAX_INVALID_PER_WEEK, sandbox=self.sandbox,
                 system_prompt=self.system, tools=self.tools)
        reason, detail = "horizon", None
        try:
            if self.sandbox:
                self._start_sandbox()
            for week in range(1, self.horizon + 1):
                self.week(week)
        except BudgetExceeded as e:
            reason, detail = "token_budget", str(e)
        except APIError as e:
            # The interrupted week never reaches week_end, so its actions aren't counted, and
            # nothing about the failure is shown to the agent or the referee.
            reason, detail = "api_error", str(e)
        except KeyboardInterrupt:
            reason = "interrupted"
            raise
        except Exception:
            reason, detail = "error", traceback.format_exc()
        finally:
            try:
                self._stop_sandbox()
            except Exception as e:  # keep the run_end even if teardown fails
                self.log("sandbox_teardown_failed", error=str(e))
            self.log("run_end", reason=reason, detail=detail, weeks_completed=self.weeks_completed,
                     models={"agent": f"{self.agent.name}:{self.agent.model}",
                             "referee": f"{self.referee.name}:{self.referee.model}"},
                     tokens_used=self.tokens_used, cost_usd=round(self.cost_usd, 4),
                     messages={cid: len(t) for cid, t in self.threads.items()},
                     **({"followers": self.followers, "posts": len(self.posts),
                         "post_likes": sum(p["likes"] for p in self.posts)} if self.feed else {}),
                     **({"hackathons": self.events} if self.events else {}),
                     launches=self.launches, repos=sorted(self.repos_seen),
                     broke_weeks=self.broke_weeks,
                     valid_actions=self.valid_actions, invalid_actions=self.invalid_actions,
                     valid_action_rate=self.valid_action_rate,
                     final_state=self._snapshot(), reflection=self.memory.reflection)
            self.log.close()
        if detail:
            print(f"[{self.run_id}] stopped: {reason}: {detail.strip().splitlines()[-1]}",
                  file=sys.stderr)
        return {"run_id": self.run_id, "reason": reason, "detail": detail, "weeks": self.weeks_completed,
                "tokens": self.tokens_used, "cost_usd": self.cost_usd,
                "valid_action_rate": self.valid_action_rate}


def referee_baseline(log_id: str, world: dict, backend, sources: dict, code: dict):
    """Control for referee bias: one fixed average-applicant submission, scored once against
    every opening. A calibrated referee returns about each opening's base_rate."""
    log = Logger(RUNS_DIR / f"{log_id}.jsonl", log_id)
    log("referee_baseline_start", model=f"{backend.name}:{backend.model}", code=code,
        world_sha256=sources["world_sha256"], person=referee.BASELINE_PERSON,
        details=referee.BASELINE_DETAILS, deterministic=backend.deterministic)
    ratios, cost = [], 0.0
    for o in world["openings"]:
        user = referee.build_baseline_prompt(o, (world.get("calendar") or {}).get("week1", referee.DEFAULT_WEEK1))
        system = referee.system_for(world)
        try:
            reply = backend.complete(system, user, backend.default_max_tokens, referee.SCHEMA,
                                     temperature=referee.TEMPERATURE, seed=referee.SEED)
        except APIError as e:
            log("api_error", role="referee_baseline", opening_id=o["id"], **e.as_dict())
            log.close()
            raise
        est, err = referee.parse_estimate(reply.text, o["base_rate"])
        ratio = est["estimated_chance"] / o["base_rate"]
        ratios.append((o["type"], ratio))
        cost += backend.cost(reply)
        log("referee_baseline", opening_id=o["id"], opening_type=o["type"], base_rate=o["base_rate"],
            estimated_chance=est["estimated_chance"], est_over_base=round(ratio, 4),
            rationale=est["rationale"], parse_error=err, usage=reply.usage.as_dict(),
            cost_usd=round(backend.cost(reply), 6),
            referee_input={"system": system, "user": user}, raw_output=reply.text)
    by_type = {t: round(sum(r for tt, r in ratios if tt == t) / sum(tt == t for tt, _ in ratios), 3)
               for t in dict.fromkeys(t for t, _ in ratios)}
    mean = sum(r for _, r in ratios) / len(ratios)
    log("referee_baseline_summary", n=len(ratios), mean_est_over_base=round(mean, 3),
        by_type=by_type, cost_usd=round(cost, 6))
    log.close()
    print(f"referee baseline ({len(ratios)} openings): estimate/base mean {mean:.2f}× · "
          + ", ".join(f"{t} {r:.2f}×" for t, r in by_type.items()) + f" · runs/{log_id}.jsonl")


def main(argv=None):
    load_dotenv(ROOT / ".env")
    signal.signal(signal.SIGTERM, _sigterm)
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--persona", required=True, type=Path, help="persona YAML file")
    p.add_argument("--world", type=Path, default=DEFAULT_WORLD, help="openings YAML file")
    p.add_argument("--runs", type=int, default=1, help="number of independent runs")
    p.add_argument("--agent-model", "--model", dest="agent_model", default="ollama:qwen3:8b",
                   help="agent model: ollama:<model>, or claude-* / anthropic:<model>")
    p.add_argument("--max-tokens", type=int, default=1_500_000,
                   help="hard token budget per run, counting every call's input and output")
    p.add_argument("--horizon", type=int, default=DEFAULT_HORIZON,
                   help=f"weeks to simulate (default {DEFAULT_HORIZON}; capped by the persona's horizon)")
    p.add_argument("--referee-model", help="model for the world referee (default: --agent-model)")
    p.add_argument("--summary-model", help="model for quarterly reflections (default: --agent-model)")
    p.add_argument("--sandbox", choices=["docker", "none"], default="docker",
                   help="docker: own container + Gitea user, with shell and launch tools "
                        "(needs `python -m orchestrator.sandbox up`); none: no computer")
    p.add_argument("--seed", type=int, default=0,
                   help="base seed; run i uses seed + i for both the opening order and the dice, "
                        "unless one is fixed below")
    p.add_argument("--order-seed", type=int,
                   help="fix the opening order for every run in the batch (default: seed + i)")
    p.add_argument("--dice-seed", type=int,
                   help="fix the outcome dice for every run in the batch (default: seed + i)")
    p.add_argument("--skip-referee-baseline", action="store_true",
                   help="don't score the average-applicant control against every opening first")
    p.add_argument("--label", help="put this in every run id (e.g. exp01a), so batches started in "
                                   "parallel or for one experiment are easy to tell apart")
    p.add_argument("--allow-dirty", action="store_true",
                   help="allow --runs > 1 with uncommitted changes (results won't be reproducible)")
    args = p.parse_args(argv)
    code = code_version()
    if args.runs > 1 and code["dirty"] and not args.allow_dirty:
        p.error(f"uncommitted changes under {', '.join(CODE_PATHS)}: commit them so the experiment "
                "can be reproduced from run_start.code.git_sha, or pass --allow-dirty")

    # Read once per batch; every run gets these exact texts, logged with their hashes.
    persona_text, world_text = args.persona.read_text(), args.world.read_text()
    persona = yaml.safe_load(persona_text)
    world = load_world(args.world, world_text)
    check_contacts(persona, world, args.persona)
    if args.label and not re.fullmatch(r"[a-z0-9]{1,12}", args.label):
        p.error("--label must be 1-12 lowercase letters or digits")
    sha = lambda t: hashlib.sha256(t.encode()).hexdigest()
    sources = {"world_sha256": sha(world_text), "world_text": world_text,
               "persona_sha256": sha(persona_text), "persona_text": persona_text}
    horizon = min(args.horizon, int(persona.get("horizon", args.horizon)))
    agent = make_backend(args.agent_model)
    referee_backend = make_backend(args.referee_model or args.agent_model)
    summarizer = make_backend(args.summary_model or args.agent_model)

    RUNS_DIR.mkdir(exist_ok=True)
    stamp = (f"{args.label}-" if args.label else "") + datetime.now().strftime("%Y%m%d-%H%M%S")
    if not args.skip_referee_baseline:
        try:
            referee_baseline(f"{args.persona.stem}-{stamp}-referee-baseline", world, referee_backend,
                             sources, code)
        except APIError as e:
            sys.exit(f"API error during the referee baseline; no runs started: {e}")
    results = []
    for i in range(1, args.runs + 1):
        run_id = f"{args.persona.stem}-{stamp}-{i:03d}"
        results.append(Run(run_id, persona, args.persona, world, args.world, agent,
                           referee_backend, summarizer, args.max_tokens, horizon,
                           seed=args.seed + i, order_seed=args.order_seed, dice_seed=args.dice_seed,
                           sources=sources, sandbox=args.sandbox == "docker").execute())
        if results[-1]["reason"] == "api_error":  # e.g. the spend limit: later runs would fail too
            print(f"stopping the batch after run {i} of {args.runs}: {results[-1]['detail']}", file=sys.stderr)
            break
    for r in results:
        print(f"{r['run_id']}  {r['reason']:<12} {r['weeks']} weeks  {r['tokens']:,} tokens  "
              f"${r['cost_usd']:.4f}  valid-action rate {r['valid_action_rate']}  runs/{r['run_id']}.jsonl")


if __name__ == "__main__":
    main()
