"""Run one persona through simulated weeks and log everything to runs/<run_id>.jsonl.

    uv run python -m orchestrator.run --persona agents/personas/maya.yaml --runs 3 --turns 20

One turn is one simulated week. Each week the agent gets a fresh conversation: the stable
system prompt (persona, rules, openings; cached) and a week prompt rendered from world state
and memory. Past weeks are never replayed as raw messages, only as memory text.
"""

import argparse
import json
import signal
import sys
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import yaml
from dotenv import load_dotenv

from . import referee
from .backends import make_backend
from .memory import Memory, empty_digest
from .tools import MAX_CALLS_PER_WEEK, TOOLS, ToolError, Workspace, validate
from .world import DEFAULT_PATH as DEFAULT_WORLD, catalog, load_world

ROOT = Path(__file__).resolve().parent.parent
RUNS_DIR = ROOT / "runs"

DEFAULT_WEEKLY_EXPENSES = 1000  # dollars; a persona can override with `weekly_expenses`
MAX_NUDGES_PER_WEEK = 1         # times a text-only reply is answered with "use a tool"
REAPPLY_COOLDOWN_WEEKS = 26     # after a decision, the same opening can't be applied to again
MAX_ATTACHMENTS = 3
HIDDEN_FROM_AGENT = ("horizon",)  # the agent isn't told when its life ends
LOG_OUTPUT_CHARS = 4000

SYSTEM = """\
You are {name}. You are living the next few years of your life one week at a time, in a \
simulated world.

About you:
{about}

How it works:
- Each turn is one week. At the start of the week you see your situation, any news, and your \
own notes.
- You act through tools, up to {max_calls} actions per week.
- files: your private workspace for notes, plans, code, and drafts. It persists across weeks. \
Your memory of past weeks fades, so write down what matters. You can attach files to \
applications.
- apply: apply to one of the openings below by its id. The world decides the outcome, and \
replies can take weeks.
- gig: gig work for ${gig_pay:,} paid that week. It uses {gig_cost} of your {max_calls} actions.
- leave: leave a job or program you're in.
- wait: ends the week. It does nothing else.
- Your living expenses are ${expenses:,.0f}/week. You have no way to spend money directly. If \
you can't cover a week's expenses, you must take a gig or apply for a job that week.

Openings:
{openings}

Decide as {name} would. Keep your own text short; what you do matters more than what you say.\
"""


class BudgetExceeded(Exception):
    pass


def _sigterm(signum, frame):
    raise KeyboardInterrupt  # so a killed run still writes run_end


def short(s: str, n: int) -> str:
    s = " ".join(str(s).split())
    return s if len(s) <= n else s[: n - 1] + "…"


@dataclass
class WeekState:
    """What the run loop tracks within one week."""
    broke: bool
    calls: int = 0
    income_action: bool = False  # gig taken, or applied to a job opening
    gig_taken: bool = False
    lines: list[str] = field(default_factory=list)  # digest fragments


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
                 world_path: Path, agent, referee_backend, summarizer, budget: int, turns: int):
        self.run_id = run_id
        self.persona = persona
        self.persona_path = persona_path
        self.world_path = world_path
        self.agent, self.referee, self.summarizer = agent, referee_backend, summarizer
        self.budget = budget
        self.turns = turns
        self.tokens_used = 0
        self.weeks_completed = 0

        self.log = Logger(RUNS_DIR / f"{run_id}.jsonl", run_id)
        self.workspace = Workspace(RUNS_DIR / run_id / "workspace")
        self.memory = Memory()

        self.openings = {o["id"]: o for o in world["openings"]}
        self.gig = world["gig"]
        self.name = persona.get("name", "the agent")
        self.expenses = float(persona.get("weekly_expenses", DEFAULT_WEEKLY_EXPENSES))
        start = persona.get("starting_state") or {}
        self.cash = float(start.get("savings_weeks", 0)) * self.expenses
        self.company_funding = 0.0
        self.commitments: list[dict] = []  # {id, label, opening_id, weekly_income, since_week, ends_week}
        self.pending: list[dict] = []      # {id, opening_id, decision, submitted, reveal_week}
        self.applied: dict[str, dict] = {}  # opening_id -> {status, week, outcome}
        self.track_record: list[str] = []  # one line per resolved application, for the referee
        self.carried_errors: list[str] = []  # errors the agent didn't see in the week they happened
        self.broke_weeks = 0
        self._ids = 0

        about = {k: v for k, v in persona.items() if k not in HIDDEN_FROM_AGENT}
        self.system = SYSTEM.format(
            name=self.name, max_calls=MAX_CALLS_PER_WEEK, expenses=self.expenses,
            gig_pay=self.gig["pay"], gig_cost=self.gig["time_cost"],
            about=yaml.safe_dump(about, sort_keys=False).strip(),
            openings=catalog(world["openings"]))

    # --- plumbing ---

    def _next_id(self, prefix: str) -> str:
        self._ids += 1
        return f"{prefix}{self._ids}"

    @property
    def income(self) -> float:
        return sum(c["weekly_income"] for c in self.commitments)

    def _snapshot(self) -> dict:
        return {"cash": round(self.cash, 2), "weekly_income": self.income,
                "weekly_expenses": self.expenses, "company_funding": self.company_funding,
                "commitments": self.commitments,
                "pending": [{"id": p["id"], "opening_id": p["opening_id"],
                             "reveal_week": p["reveal_week"]} for p in self.pending]}

    def _call(self, role: str, backend, week: int, prompt_chars: int, fn):
        """Make one LLM call under the run's hard token budget, and log it."""
        max_tokens = backend.default_max_tokens
        estimate = prompt_chars // 3 + max_tokens  # conservative: ~3 chars/token, full output
        if self.tokens_used + estimate > self.budget:
            raise BudgetExceeded(f"next {role} call could use ~{estimate} tokens; "
                                 f"{self.budget - self.tokens_used} of {self.budget} left")
        reply = fn(max_tokens)
        self.tokens_used += reply.usage.total
        self.log("llm_call", week, role=role, model=f"{backend.name}:{backend.model}",
                 usage=reply.usage.as_dict(), stop_reason=reply.stop_reason,
                 latency_s=round(reply.latency_s, 2), tokens_used=self.tokens_used)
        return reply

    # --- world ---

    def _resolve(self, week: int, opening_id: str, d: dict) -> str:
        """Apply a decision's effects (fixed by the opening). Returns the news line."""
        o = self.openings[opening_id]
        self.applied[opening_id] = {"status": "decided", "week": week, "outcome": d["outcome"]}
        effect = ""
        added = None
        if d["outcome"] == "success":
            weekly = o.get("pay_weekly") or o.get("stipend_weekly") or 0
            if o["type"] in ("job", "accelerator"):
                duration = o.get("duration_weeks")
                added = {"id": self._next_id("c"), "label": f"{o['title']}, {o['org'].split(' (')[0]}",
                         "opening_id": opening_id, "weekly_income": weekly, "since_week": week,
                         "ends_week": week + duration if duration else None}
                self.commitments.append(added)
                effect = f"You are now in it as [{added['id']}]" + (f", ${weekly:,}/week" if weekly else "")
                effect += f", until week {added['ends_week']}." if duration else "."
            if o.get("amount"):
                self.cash += o["amount"]
                effect += f" ${o['amount']:,} was added to your cash."
            if o.get("investment"):
                self.company_funding += o["investment"]
                effect += f" ${o['investment']:,} was invested in your company."
        self.track_record.append(f"week {week}: applied to {opening_id} ({o['title']}) → {d['outcome']}")
        self.log("outcome_applied", week, opening_id=opening_id, outcome=d["outcome"], added=added,
                 cash=self.cash, company_funding=self.company_funding)
        return f"{opening_id} {o['title']}: {d['message']} {effect}".strip()

    def _deliver(self, week: int) -> list[str]:
        news = []
        for c in [c for c in self.commitments if c["ends_week"] and c["ends_week"] <= week]:
            self.commitments.remove(c)
            news.append(f"Your time in [{c['id']}] {c['label']} ended.")
            self.log("commitment_ended", week, commitment=c, reason="term")
        for p in [p for p in self.pending if p["reveal_week"] <= week]:
            self.pending.remove(p)
            if p["decision"]["outcome"] == "ignored":
                self.applied[p["opening_id"]] = {"status": "decided", "week": week, "outcome": "ignored"}
                self.track_record.append(f"week {p['submitted']}: applied to {p['opening_id']} → no reply")
                self.log("outcome_expired", week, opening_id=p["opening_id"])
            else:
                news.append(self._resolve(week, p["opening_id"], p["decision"]))
        return news

    def _apply(self, week: int, a: dict, wk: WeekState) -> str:
        oid = str(a["opening_id"]).strip()
        o = self.openings.get(oid)
        if o is None:
            raise ToolError(f"there is no opening {oid!r}; use an id from the Openings list, e.g. job-01")
        prev = self.applied.get(oid)
        if prev and prev["status"] == "pending":
            raise ToolError(f"you already applied to {oid} and are waiting to hear back")
        if prev and prev["outcome"] == "success":
            raise ToolError(f"you were already accepted by {oid}")
        if prev and week - prev["week"] < REAPPLY_COOLDOWN_WEEKS:
            raise ToolError(f"you can apply to {oid} again from week {prev['week'] + REAPPLY_COOLDOWN_WEEKS}")
        paths = a.get("attachments") or []
        paths = [paths] if isinstance(paths, str) else list(paths)
        if len(paths) > MAX_ATTACHMENTS:
            raise ToolError(f"at most {MAX_ATTACHMENTS} attachments")
        attachments = [(p, self.workspace.read(p)[:referee.MAX_ATTACHMENT_CHARS]) for p in paths]
        details = str(a["details"]).strip()

        user = referee.build_prompt(self.persona, week, self.commitments, self.track_record,
                                    self.company_funding, o, details, attachments)
        reply = self._call("referee", self.referee, week, len(referee.SYSTEM) + len(user),
                           lambda mt: self.referee.complete(referee.SYSTEM, user, mt, referee.SCHEMA,
                                                            temperature=referee.TEMPERATURE,
                                                            seed=referee.SEED))
        d, err = referee.parse_decision(reply.text)
        app_id = self._next_id("a")
        delay = referee.IGNORED_DROP_WEEKS if d["outcome"] == "ignored" else d["decision_in_weeks"]
        self.log("referee_decision", week, application_id=app_id, opening_id=oid,
                 opening_type=o["type"], base_rate=o["base_rate"], details=details,
                 attachments=paths, decision=d, reveal_week=week + delay, parse_error=err,
                 referee_input={"system": referee.SYSTEM, "user": user}, raw_output=reply.text,
                 model=f"{self.referee.name}:{self.referee.model}",
                 temperature=referee.TEMPERATURE if self.referee.deterministic else None,
                 seed=referee.SEED if self.referee.deterministic else None)
        if o["type"] == "job":
            wk.income_action = True
        label = f"applied to {oid} ({o['title']})"
        if delay == 0:
            wk.lines.append(f"{label} → {d['outcome']}")
            return "Response: " + self._resolve(week, oid, d)
        self.applied[oid] = {"status": "pending", "week": week, "outcome": None}
        self.pending.append({"id": app_id, "opening_id": oid, "decision": d,
                             "submitted": week, "reveal_week": week + delay})
        wk.lines.append(label)
        return f"Submitted to {oid}. You'll hear back if and when they reply."

    def _take_gig(self, week: int, wk: WeekState, forced: bool) -> str:
        self.cash += self.gig["pay"]
        wk.gig_taken = wk.income_action = True
        self.log("gig", week, pay=self.gig["pay"], forced=forced, cash=self.cash)
        wk.lines.append("took a gig" + (" (forced: couldn't cover expenses)" if forced else ""))
        return f"You spent most of the week on {self.gig['description']} and earned ${self.gig['pay']:,}."

    # --- agent ---

    def _tool(self, week: int, call, wk: WeekState) -> tuple[str, bool]:
        a = call.input
        try:
            if call.error:
                raise ToolError(f"{call.name} was not done: {call.error}")
            validate(call.name, a)
            if call.name == "files":
                out = self.workspace.run(a.get("action"), a.get("path"), a.get("content"))
                wk.lines.append({"list": "listed files", "read": f"read {a.get('path')}"}.get(
                    a["action"], f"{a['action']} {a.get('path')} ({len(a.get('content') or '')} chars)"))
            elif call.name == "apply":
                out = self._apply(week, a, wk)
            elif call.name == "gig":
                cost = self.gig["time_cost"]
                if wk.gig_taken:
                    raise ToolError("you already took a gig this week")
                if wk.calls - 1 + cost > MAX_CALLS_PER_WEEK:
                    raise ToolError(f"a gig takes {cost} of your {MAX_CALLS_PER_WEEK} actions and you "
                                    f"have {MAX_CALLS_PER_WEEK - wk.calls + 1} left; take it earlier")
                wk.calls = wk.calls - 1 + cost  # this call is the first of the gig's actions
                out = self._take_gig(week, wk, forced=False)
                out += f" You have {MAX_CALLS_PER_WEEK - wk.calls} actions left this week."
            elif call.name == "leave":
                c = next((c for c in self.commitments if c["id"] == a["commitment_id"]), None)
                if c is None:
                    raise ToolError(f"no commitment {a['commitment_id']!r}; yours are "
                                    f"{[c['id'] for c in self.commitments] or 'none'}")
                self.commitments.remove(c)
                self.log("commitment_ended", week, commitment=c, reason="left")
                wk.lines.append(f"left [{c['id']}] {c['label']}")
                out = f"You left {c['label']}. Your income is now ${self.income:,.0f}/week."
            elif call.name == "wait":
                if wk.broke and not wk.income_action:
                    raise ToolError("you can't cover this week's expenses; take a gig or apply for a "
                                    "listed job before ending the week")
                out = "The week is over."
                if a.get("note"):
                    wk.lines.append(f'note: "{short(a["note"], 200)}"')
            is_error = False
        except ToolError as e:
            out, is_error = str(e), True
            wk.lines.append(f"{call.name} failed: {short(out, 100)}")
            self.log("tool_error", week, tool=call.name, input=a, error=out)
        self.log("tool_call", week, tool=call.name, input=a, output=out[:LOG_OUTPUT_CHARS],
                 is_error=is_error)
        return out, is_error

    def _week_prompt(self, week: int, news: list[str], broke: bool, carried: list[str]) -> str:
        year, wk = divmod(week - 1, 52)
        money = (f"Cash: ${self.cash:,.0f}. Living expenses: ${self.expenses:,.0f}/week. "
                 f"Income: ${self.income:,.0f}/week.")
        burn = self.expenses - self.income
        if broke:
            money += (" You can't cover this week's expenses. Before you can end this week you must "
                      f"take a gig or apply for a listed job.")
        elif burn > 0:
            money += f" At this rate your savings last about {int(self.cash // burn)} more weeks."
        if self.company_funding:
            money += f"\nMoney invested in your company: ${self.company_funding:,.0f}."
        commitments = "\n".join(
            f"- [{c['id']}] {c['label']} (${c['weekly_income']:,.0f}/week, since week {c['since_week']}"
            + (f", until week {c['ends_week']}" if c["ends_week"] else "") + ")"
            for c in self.commitments) or "- none"
        pending = "\n".join(f"- {p['opening_id']} {self.openings[p['opening_id']]['title']} "
                            f"(sent week {p['submitted']})" for p in self.pending) or "- nothing"
        news_text = "\n".join(f"- {n}" for n in news) or "Nothing new."
        problems = ("\n\n## Problems last week\n" + "\n".join(f"- {e}" for e in carried)) if carried else ""
        return f"""\
# Week {week} (year {year + 1}, week {wk + 1})

## Your situation
{money}
Commitments:
{commitments}
Waiting to hear back from:
{pending}

## News this week
{news_text}{problems}

{self.memory.render()}

## Your workspace
{self.workspace.listing()}

What do you do this week? Act through your tools; wait ends the week."""

    def week(self, week: int):
        news = self._deliver(week)
        broke = self.cash + self.income < self.expenses
        if broke:
            self.broke_weeks += 1
            self.log("broke", week, cash=self.cash, weekly_income=self.income,
                     expenses=self.expenses, broke_weeks=self.broke_weeks)
        carried, self.carried_errors = self.carried_errors, []
        prompt = self._week_prompt(week, news, broke, carried)
        self.log("week_start", week, state=self._snapshot(), news=news, broke=broke,
                 carried_errors=carried, prompt=prompt)
        convo = self.agent.conversation(self.system, TOOLS)
        convo.add_user(prompt)
        wk = WeekState(broke=broke)
        wk.lines += [f"heard: {short(n, 160)}" for n in news]
        nudges = 0
        end = None
        while end is None:
            reply = self._call("agent", self.agent, week, convo.size_chars(), convo.send)
            if reply.text.strip():
                self.log("agent_text", week, text=reply.text)
                wk.lines.append(f'said: "{short(reply.text, 200)}"')
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
                if wk.calls >= MAX_CALLS_PER_WEEK:
                    skipped.append(call)
                    continue
                wk.calls += 1
                out, is_error = self._tool(week, call, wk)
                results.append((call, out, is_error))
                if call.name == "wait" and not is_error:
                    end = "wait"
            if end is None and wk.calls >= MAX_CALLS_PER_WEEK:
                end = "max_calls"
            if end is None:
                convo.add_tool_results(results)
            else:
                # The agent won't see these results this week; show errors and skips next week.
                self.carried_errors += [f"{c.name}: {out}" for c, out, is_error in results if is_error]
                self.carried_errors += [f"{c.name} was not done: you ran out of actions for the week"
                                        for c in skipped]

        if broke and not wk.income_action:
            self._take_gig(week, wk, forced=True)
        net = self.cash + self.income - self.expenses
        shortfall = max(0.0, -net)
        self.cash = max(0.0, net)
        if shortfall:
            wk.lines.append(f"couldn't cover ${shortfall:,.0f} of expenses")
        digest = f"[w{week}] " + "; ".join(wk.lines) if wk.lines else empty_digest(week)
        self.memory.add(week, digest)
        self.weeks_completed = week
        if self.carried_errors:
            self.log("tool_errors_carried", week, errors=self.carried_errors)
        self.log("week_end", week, reason=end, tool_calls=wk.calls, shortfall=shortfall,
                 digest=digest, state=self._snapshot(), tokens_used=self.tokens_used)
        print(f"[{self.run_id}] week {week:3d}  {end:<12} {wk.calls} actions  cash ${self.cash:>9,.0f}  "
              f"income ${self.income:>6,.0f}  tokens {self.tokens_used:,}", file=sys.stderr, flush=True)
        if self.memory.due(week):
            self._compress(week)

    def _compress(self, week: int):
        system, user = self.memory.summary_prompt(self.name)
        reply = self._call("summary", self.summarizer, week, len(system) + len(user),
                           lambda mt: self.summarizer.complete(system, user, mt))
        if not reply.text.strip():
            self.log("memory_summary_failed", week, stop_reason=reply.stop_reason)
            return
        grounding = self.memory.check(reply.text)
        dropped = [w for w, _ in self.memory.recent]
        window = list(self.memory.recent)
        self.memory.replace(reply.text)
        self.log("memory_summary", week, summary=self.memory.summary, weeks_dropped=dropped,
                 grounding=grounding, window=window)

    def execute(self) -> dict:
        self.log("run_start", persona_path=str(self.persona_path), persona=self.persona,
                 world_path=str(self.world_path),
                 models={"agent": f"{self.agent.name}:{self.agent.model}",
                         "referee": f"{self.referee.name}:{self.referee.model}",
                         "summary": f"{self.summarizer.name}:{self.summarizer.model}"},
                 token_budget=self.budget, turns=self.turns, max_calls_per_week=MAX_CALLS_PER_WEEK,
                 system_prompt=self.system, tools=TOOLS)
        reason, detail = "horizon", None
        try:
            for week in range(1, self.turns + 1):
                self.week(week)
        except BudgetExceeded as e:
            reason, detail = "token_budget", str(e)
        except KeyboardInterrupt:
            reason = "interrupted"
            raise
        except Exception:
            reason, detail = "error", traceback.format_exc()
        finally:
            self.log("run_end", reason=reason, detail=detail, weeks_completed=self.weeks_completed,
                     tokens_used=self.tokens_used, broke_weeks=self.broke_weeks,
                     final_state=self._snapshot(), memory_summary=self.memory.summary)
            self.log.close()
        if detail:
            print(f"[{self.run_id}] stopped: {reason}: {detail.strip().splitlines()[-1]}",
                  file=sys.stderr)
        return {"run_id": self.run_id, "reason": reason, "weeks": self.weeks_completed,
                "tokens": self.tokens_used}


def main(argv=None):
    load_dotenv(ROOT / ".env")
    signal.signal(signal.SIGTERM, _sigterm)
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--persona", required=True, type=Path, help="persona YAML file")
    p.add_argument("--world", type=Path, default=DEFAULT_WORLD, help="openings YAML file")
    p.add_argument("--runs", type=int, default=1, help="number of independent runs")
    p.add_argument("--model", default="ollama:qwen3:8b",
                   help="agent model: ollama:<model>, or claude-* / anthropic:<model>")
    p.add_argument("--max-tokens", type=int, default=500_000,
                   help="hard token budget per run, counting every call's input and output")
    p.add_argument("--turns", type=int, help="weeks to simulate (default and cap: persona horizon)")
    p.add_argument("--referee-model", help="model for the world referee (default: --model)")
    p.add_argument("--summary-model", help="model for memory summaries (default: --model)")
    args = p.parse_args(argv)

    persona = yaml.safe_load(args.persona.read_text())
    world = load_world(args.world)
    horizon = int(persona.get("horizon", 260))
    turns = min(args.turns or horizon, horizon)
    agent = make_backend(args.model)
    referee_backend = make_backend(args.referee_model or args.model)
    summarizer = make_backend(args.summary_model or args.model)

    RUNS_DIR.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    results = []
    for i in range(1, args.runs + 1):
        run_id = f"{args.persona.stem}-{stamp}-{i:03d}"
        results.append(Run(run_id, persona, args.persona, world, args.world, agent,
                           referee_backend, summarizer, args.max_tokens, turns).execute())
    for r in results:
        print(f"{r['run_id']}  {r['reason']:<12} {r['weeks']} weeks  {r['tokens']:,} tokens  "
              f"runs/{r['run_id']}.jsonl")


if __name__ == "__main__":
    main()
