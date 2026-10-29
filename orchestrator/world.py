"""The world's static content: openings and the gig, loaded from world/openings.yaml."""

from pathlib import Path

import yaml

TYPES = ("job", "accelerator", "grant", "angel_intro")
DEFAULT_PATH = Path(__file__).resolve().parent.parent / "world" / "openings.yaml"


def load_world(path: Path = DEFAULT_PATH) -> dict:
    world = yaml.safe_load(path.read_text())
    gig = world.get("gig") or {}
    for k in ("pay", "time_cost", "description"):
        if k not in gig:
            raise ValueError(f"{path}: gig.{k} is required")
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
        seen.add(o["id"])
    if not seen:
        raise ValueError(f"{path}: no openings")
    return world


def terms(o: dict) -> str:
    """What a success gives, in words. Shown to the agent and the referee."""
    parts = []
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


def catalog(openings: list[dict]) -> str:
    """Agent-facing list of openings. Base rates are deliberately left out."""
    return "\n".join(f"- {o['id']} [{o['type']}] {o['title']}, {o['org']}. {terms(o)}. "
                     f"Needs: {o['requirements']}." for o in openings)
