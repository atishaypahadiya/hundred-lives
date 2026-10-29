"""The world model, v0: an LLM estimates the odds of every `apply`; code rolls the dice.

The referee judges fit against one concrete opening and is shown that opening's base rate.
It returns `estimated_chance`, how long a reply takes, and the message for each result. It
never picks the outcome. `draw` does: success with probability estimated_chance, and on a
non-success, "ignored" with probability 1 - the opening's reply_rate, else "failure". The
draws come from a per-run RNG seeded from --seed, so a run's dice are reproducible.

What a success gives (pay, investment, grant amount) is fixed by the opening in
world/openings.yaml, not by the referee.

The referee never sees the agent's conversation, goal, or traits — only what an outside
observer could know: the public background, current commitments, track record, and the
submission with its attachments. That keeps a sensitivity change to a trait (e.g.
risk_tolerance) from leaking into outcomes directly.

Calls run at temperature 0 with a fixed seed where the backend allows it (Ollama): the same
application always gets the same odds, and the dice add the variance. Current Claude models
reject sampling parameters, so Anthropic referee estimates are not deterministic.
"""

import json
import random

import yaml

from .world import terms

PUBLIC_FIELDS = ("name", "born", "family", "education", "skills")
TEMPERATURE = 0
SEED = 0
IGNORED_DROP_WEEKS = 12  # an ignored application leaves the agent's pending list after this long
MAX_ATTACHMENT_CHARS = 3000

SYSTEM = """\
You are the world in a life simulation. When a person applies to one of the world's openings \
(a job, accelerator, grant, or angel introduction), you estimate their chance of being \
accepted. You do not decide the result; it is drawn from your estimate.

How to estimate:
- base_rate is the share of all applicants to this opening who are accepted. Start from it. \
Move up or down only for concrete evidence of fit: how the person's background, track record, \
submission, and attachments meet the stated requirements.
- A submission that doesn't address the requirements should get less than the base rate. \
Strong evidence of fit can get more, but rarely more than a few times the base rate. Claims \
with no evidence count for little.
- estimated_chance is a probability from 0 to 1 that this person is accepted.
- decision_in_weeks is how many weeks until the person hears back, as this kind of \
organization would take (0 = this week).
- message_if_success and message_if_failure are what the person is told in each case, in \
plain words. Both are needed; one of them will be used.
- rationale is private: how you weighed the base rate against the evidence of fit.\
"""

SCHEMA = {
    "type": "object",
    "properties": {
        "estimated_chance": {"type": "number"},
        "decision_in_weeks": {"type": "integer"},
        "message_if_success": {"type": "string"},
        "message_if_failure": {"type": "string"},
        "rationale": {"type": "string"},
    },
    "required": ["estimated_chance", "decision_in_weeks", "message_if_success",
                 "message_if_failure", "rationale"],
    "additionalProperties": False,
}


def build_prompt(persona: dict, week: int, commitments: list[dict], track_record: list[str],
                 company_funding: float, opening: dict, details: str,
                 attachments: list[tuple[str, str]]) -> str:
    public = {k: persona[k] for k in PUBLIC_FIELDS if k in persona}
    network = (persona.get("starting_state") or {}).get("network")
    if network:
        public["network"] = network
    current = "\n".join(f"- {c['label']} (since week {c['since_week']})" for c in commitments) or "none"
    record = "\n".join(f"- {r}" for r in track_record[-20:]) or "none"
    opening_view = {k: opening[k] for k in ("id", "type", "title", "org", "requirements") if k in opening}
    files = "\n\n".join(f"--- {path} ---\n{text}" for path, text in attachments) or "none"
    return f"""\
Person:
{yaml.safe_dump(public, sort_keys=False).strip()}
Money raised for their company so far: ${company_funding:,.0f}

Simulated week: {week} (week 1 is the spring they finish school)

Current commitments:
{current}

Track record (most recent last):
{record}

Opening:
{yaml.safe_dump(opening_view, sort_keys=False).strip()}
offers: {terms(opening)}
base_rate: {opening['base_rate']}

Submission:
{details}

Attachments:
{files}
"""


def parse_estimate(text: str, base_rate: float) -> tuple[dict, str | None]:
    """Validate and clamp the referee's JSON. Returns (estimate, error-or-None).

    If the output can't be used, the estimate falls back to the opening's base rate, so a
    parse failure neither helps nor hurts the applicant.
    """
    fallback = {"estimated_chance": float(base_rate), "decision_in_weeks": 4,
                "message_if_success": "You were accepted.",
                "message_if_failure": "Thank you for applying. We won't be moving forward.",
                "rationale": "referee output could not be used; fell back to the base rate"}
    try:
        d = json.loads(text)
        if not isinstance(d, dict):
            raise ValueError("not an object")
        out = {**fallback, **{k: d[k] for k in SCHEMA["properties"] if k in d}}
        out["estimated_chance"] = max(0.0, min(1.0, float(out["estimated_chance"])))
        out["decision_in_weeks"] = max(0, min(26, int(out["decision_in_weeks"])))
    except (TypeError, ValueError) as e:
        return fallback, f"unusable referee output: {e}"
    return out, None


def draw(rng: random.Random, chance: float, reply_rate: float) -> tuple[str, dict]:
    """Sample the outcome. Returns (outcome, the raw draws for the log)."""
    success_draw = rng.random()
    if success_draw < chance:
        return "success", {"success_draw": success_draw, "reply_draw": None}
    reply_draw = rng.random()
    outcome = "failure" if reply_draw < reply_rate else "ignored"
    return outcome, {"success_draw": success_draw, "reply_draw": reply_draw}
