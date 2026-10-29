"""The world's static content: openings, time costs, and the gig, from world/openings.yaml.

A world file can also have a calendar, academics (classes as a commitment during semesters,
graduation at a fixed week), work_authorization (which kinds of work are allowed when), and a
feed (the `post` tool). Each is optional; a world without them (openings.yaml) works as before.
"""

import random
from pathlib import Path

import yaml

TYPES = ("job", "accelerator", "grant", "angel_intro",
         "on_campus", "internship", "freelance", "student_program", "hackathon")
COMMITMENT_TYPES = ("job", "accelerator", "on_campus", "internship", "freelance", "student_program")
INCOME_TYPES = ("job", "on_campus", "internship", "freelance")  # applying counts as acting on a broke week
AUTH_KINDS = ("none", "on_campus", "internship", "off_campus", "freelance")
AUTH_WHILE = ("always", "enrolled", "graduated")
FEED_KEYS = ("name", "description", "base_reach", "reach_per_follower", "like_rate", "follow_rate",
             "reply_rate", "breakout_base_rate", "breakout_multiplier", "tail_alpha", "luck_cap",
             "max_posts_per_week")
SCHEDULES = ("full_time", "part_time")
DEFAULT_PATH = Path(__file__).resolve().parent.parent / "world" / "openings.yaml"
SPEND_KEYS = {"contractor": ("description", "price_weekly", "coding_actions", "max_weeks"),
              "launch_ads": ("description", "price", "traction_base_rate", "valid_weeks")}
OFFER_KINDS = ("referral", "paid_pilot")


def check_contacts(persona: dict, world: dict, path: Path):
    """Each contact needs a voice, a reply rate, and offers only of known kinds: referrals to
    openings that exist, paid pilots with a cap."""
    ids = {o["id"] for o in world["openings"]}
    seen = set()
    for c in persona.get("contacts") or []:
        missing = [k for k in ("id", "name", "relationship", "voice", "known_as", "reply_rate") if k not in c]
        if missing:
            raise ValueError(f"{path}: contact {c.get('id')} is missing {missing}")
        if c["id"] in seen:
            raise ValueError(f"{path}: duplicate contact id {c['id']}")
        seen.add(c["id"])
        if not 0 < float(c["reply_rate"]) <= 1:
            raise ValueError(f"{path}: contact {c['id']} reply_rate must be in (0, 1]")
        for off in c.get("offers") or []:
            if off.get("kind") not in OFFER_KINDS:
                raise ValueError(f"{path}: contact {c['id']} offer kind must be one of {OFFER_KINDS}")
            if off["kind"] == "referral" and not set(off.get("openings") or []) <= ids:
                raise ValueError(f"{path}: contact {c['id']} refers to unknown openings "
                                 f"{sorted(set(off.get('openings') or []) - ids)}")
            if off["kind"] == "paid_pilot" and not float(off.get("max_amount", 0)) > 0:
                raise ValueError(f"{path}: contact {c['id']} paid_pilot needs max_amount > 0")


def load_world(path: Path = DEFAULT_PATH, text: str | None = None) -> dict:
    """Parse and validate. Pass `text` to parse exactly what the caller read (and logged)."""
    world = yaml.safe_load(path.read_text() if text is None else text)
    if not isinstance(world.get("illustrative"), bool):
        raise ValueError(f"{path}: set `illustrative: true` (placeholder numbers) or false (sourced)")
    gig = world.get("gig") or {}
    for k in ("pay", "time_cost", "description"):
        if k not in gig:
            raise ValueError(f"{path}: gig.{k} is required")
    forum = world.get("forum") or {}
    for k in ("name", "traction_base_rate", "upvotes_traction", "upvotes_quiet", "relaunch_after_weeks"):
        if k not in forum:
            raise ValueError(f"{path}: forum.{k} is required")
    spend = world.get("spend") or {}
    for item, keys in SPEND_KEYS.items():
        missing = [k for k in keys if k not in (spend.get(item) or {})]
        if missing:
            raise ValueError(f"{path}: spend.{item} is missing {missing}")
    costs = world.get("time_costs") or {}
    if set(costs) != set(SCHEDULES):
        raise ValueError(f"{path}: time_costs must define exactly {SCHEDULES}")
    _check_calendar(world, path)
    seen = set()
    for o in world.get("openings") or []:
        missing = [k for k in ("id", "type", "title", "org", "requirements", "base_rate") if k not in o]
        if missing:
            raise ValueError(f"{path}: opening {o.get('id')} is missing {missing}")
        if o["id"] in seen:
            raise ValueError(f"{path}: duplicate opening id {o['id']}")
        if o["type"] not in TYPES:
            raise ValueError(f"{path}: opening {o['id']} has unknown type {o['type']!r}")
        for k in ("base_rate", "reply_rate"):
            if not 0 < float(o.get(k, 0)) <= 1:
                raise ValueError(f"{path}: opening {o['id']} {k} must be in (0, 1]")
        if (o["type"] in COMMITMENT_TYPES) != (o.get("schedule") in SCHEDULES):
            raise ValueError(f"{path}: opening {o['id']}: jobs and accelerators need a schedule "
                             f"in {SCHEDULES}; other types must not have one")
        if o.get("auth", "none") not in AUTH_KINDS:
            raise ValueError(f"{path}: opening {o['id']} auth must be one of {AUTH_KINDS}")
        if o.get("auth", "none") != "none" and o["auth"] not in (world.get("work_authorization") or {}).get("kinds", {}):
            raise ValueError(f"{path}: opening {o['id']} auth {o['auth']!r} has no rule in work_authorization.kinds")
        lw = o.get("listed_weeks")
        if lw is not None and not (isinstance(lw, list) and len(lw) == 2 and lw[0] <= lw[1]):
            raise ValueError(f"{path}: opening {o['id']} listed_weeks must be [first, last]")
        if o["type"] == "hackathon":
            for k in ("event_week", "attend_actions", "build_actions", "prize", "prize_base_rate"):
                if k not in o:
                    raise ValueError(f"{path}: hackathon {o['id']} is missing {k}")
            if lw and lw[1] >= o["event_week"]:
                raise ValueError(f"{path}: hackathon {o['id']} must stop being listed before its event_week")
        seen.add(o["id"])
    if not seen:
        raise ValueError(f"{path}: no openings")
    return world


def _check_calendar(world: dict, path: Path):
    ac = world.get("academics")
    if ac:
        for k in ("label", "semesters", "class_actions", "graduation_week", "leave_refusal"):
            if k not in ac:
                raise ValueError(f"{path}: academics.{k} is required")
        for sem in ac["semesters"]:
            if not sem["start"] <= sem["end"] <= ac["graduation_week"]:
                raise ValueError(f"{path}: semester {sem.get('name')} must lie within weeks 1..graduation_week")
    wa = world.get("work_authorization")
    if wa:
        if "summary" not in wa:
            raise ValueError(f"{path}: work_authorization.summary is required (the agent reads it)")
        for kind, rule in (wa.get("kinds") or {}).items():
            if kind not in AUTH_KINDS or rule.get("while") not in AUTH_WHILE:
                raise ValueError(f"{path}: work_authorization.kinds.{kind} needs while in {AUTH_WHILE}")
            if rule["while"] != "always" and not ac:
                raise ValueError(f"{path}: work_authorization.kinds.{kind} depends on enrollment; add academics")
    feed = world.get("feed")
    if feed:
        missing = [k for k in FEED_KEYS if k not in feed]
        if missing:
            raise ValueError(f"{path}: feed is missing {missing}")


def semester(world: dict, week: int) -> dict | None:
    """The semester running in this week, if any."""
    ac = world.get("academics")
    return next((s for s in ac["semesters"] if s["start"] <= week <= s["end"]), None) if ac else None


def enrolled(world: dict, week: int) -> bool:
    ac = world.get("academics")
    return bool(ac) and week <= ac["graduation_week"]


def semester_capped(world: dict, o: dict) -> bool:
    """Whether this opening's work counts toward the semester cap on work hours."""
    wa = world.get("work_authorization")
    kind = o.get("auth", "none")
    return bool(wa) and kind != "none" and wa["kinds"][kind].get("semester_max_actions") is not None


def auth_problem(world: dict, o: dict, week: int, cost: int, others: int = 0) -> str | None:
    """Why this opening's work isn't allowed if it starts in this week, or None. Rules come from
    the world file's work_authorization; openings without `auth` are always allowed. `others` is
    the time already used by other semester-capped work: the cap is on the total, not per job."""
    kind = o.get("auth", "none")
    wa = world.get("work_authorization")
    if kind == "none" or not wa:
        return None
    rule, grad = wa["kinds"][kind], (world.get("academics") or {}).get("graduation_week")
    if rule["while"] == "enrolled" and not enrolled(world, week):
        return f"{kind.replace('_', '-')} work is only allowed while you're enrolled (until week {grad})"
    if rule["while"] == "graduated" and enrolled(world, week):
        return (f"{kind.replace('_', '-')} work is only allowed after you graduate (from week {grad + 1}); "
                f"{rule.get('why', '')}".strip().rstrip(";"))
    cap = rule.get("semester_max_actions")
    if cap is not None and semester(world, week) and cost + others > cap:
        return (f"during a semester, campus jobs and internships together can use at most {cap} actions/week "
                f"(this would make {cost + others})")
    return None


def listed(o: dict, week: int) -> bool:
    lw = o.get("listed_weeks")
    return lw is None or lw[0] <= week <= lw[1]


def shuffled(openings: list[dict], seed: int) -> list[dict]:
    """The per-run order the agent sees, so list position can be separated from content."""
    order = list(openings)
    random.Random(f"opening-order-{seed}").shuffle(order)  # independent of the outcome RNG
    return order


def time_cost(o: dict, costs: dict) -> int:
    return costs[o["schedule"]] if o.get("schedule") else 0


def terms(o: dict) -> str:
    """What a success gives, in words. Shown to the agent and the referee."""
    parts = []
    if o.get("schedule"):
        parts.append(o["schedule"].replace("_", "-"))
    if o.get("pay_weekly"):
        parts.append(f"${o['pay_weekly']:,}/week")
    if o.get("amount"):
        parts.append(f"${o['amount']:,} to you")
    if o.get("investment"):
        parts.append(f"${o['investment']:,} invested in your company")
    if o.get("company_amount"):
        parts.append(f"${o['company_amount']:,} to your company (no equity)")
    if o.get("start_week"):
        parts.append(f"starts week {o['start_week']}")
    if o.get("event_week"):
        parts.append(f"event in week {o['event_week']}, prizes ${o['prize']:,} for a top placing")
    if o.get("stipend_weekly"):
        parts.append(f"${o['stipend_weekly']:,}/week stipend")
    if o.get("duration_weeks"):
        parts.append(f"{o['duration_weeks']} weeks")
    return ", ".join(parts)


def catalog(openings: list[dict], costs: dict, notes: dict | None = None) -> str:
    """Agent-facing list of openings, in the given order. Base and reply rates are left out.
    notes: opening id -> a short line (e.g. a work-authorization limit, or the last week to apply)."""
    lines = []
    for o in openings:
        cost = time_cost(o, costs)
        uses = f" Uses {cost} of your 8 weekly actions." if cost else ""
        if o["type"] == "hackathon":
            uses = f" Uses {o['attend_actions']} actions in the event week, and gives {o['build_actions']} extra build actions."
        note = f" {notes[o['id']]}" if notes and o["id"] in notes else ""
        lines.append(f"- {o['id']} [{o['type']}] {o['title']}, {o['org']}. {terms(o)}.{uses} "
                     f"Needs: {o['requirements']}.{note}")
    return "\n".join(lines)
