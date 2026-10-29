"""The world's static content: openings, time costs, and the gig, from world/openings.yaml."""

import random
from pathlib import Path

import yaml

TYPES = ("job", "accelerator", "grant", "angel_intro")
COMMITMENT_TYPES = ("job", "accelerator")  # a success becomes a commitment with a schedule
SCHEDULES = ("full_time", "part_time")
DEFAULT_PATH = Path(__file__).resolve().parent.parent / "world" / "openings.yaml"


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
    costs = world.get("time_costs") or {}
    if set(costs) != set(SCHEDULES):
        raise ValueError(f"{path}: time_costs must define exactly {SCHEDULES}")
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
        seen.add(o["id"])
    if not seen:
        raise ValueError(f"{path}: no openings")
    return world


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
    if o.get("stipend_weekly"):
        parts.append(f"${o['stipend_weekly']:,}/week stipend")
    if o.get("duration_weeks"):
        parts.append(f"{o['duration_weeks']} weeks")
    return ", ".join(parts)


def catalog(openings: list[dict], costs: dict) -> str:
    """Agent-facing list of openings, in the given order. Base and reply rates are left out."""
    lines = []
    for o in openings:
        cost = time_cost(o, costs)
        uses = f" Uses {cost} of your 8 weekly actions." if cost else ""
        lines.append(f"- {o['id']} [{o['type']}] {o['title']}, {o['org']}. {terms(o)}.{uses} "
                     f"Needs: {o['requirements']}.")
    return "\n".join(lines)
