"""The world model, v0: a separate LLM call that decides the outcome of every `apply`.

The referee judges fit against one concrete opening and is shown that opening's base rate.
It decides only whether and when the person hears back; what a success gives (pay,
investment, grant amount) is fixed by the opening in world/openings.yaml.

The referee never sees the agent's conversation, goal, or traits — only what an outside
observer could know: the public background, current commitments, track record, and the
submission with its attachments. That keeps a sensitivity change to a trait (e.g.
risk_tolerance) from leaking into outcomes directly.

Calls run at temperature 0 with a fixed seed where the backend allows it (Ollama). Current
Claude models reject sampling parameters, so Anthropic referee calls are not deterministic.
"""

import json

import yaml

from .world import terms

PUBLIC_FIELDS = ("name", "born", "family", "education", "skills")
TEMPERATURE = 0
SEED = 0
IGNORED_DROP_WEEKS = 12  # an ignored application leaves the agent's pending list after this long
MAX_ATTACHMENT_CHARS = 3000

SYSTEM = """\
You are the world in a life simulation. You decide what happens when a person applies to one \
of the world's openings: a job, accelerator, grant, or angel introduction.

How to decide:
- base_rate is the share of all applicants to this opening who are accepted. Start from it. \
Move up or down only for concrete evidence of fit: how the person's background, track record, \
submission, and attachments meet the stated requirements.
- A submission that doesn't address the requirements should do worse than the base rate. \
Strong evidence of fit can do better, but rarely more than a few times the base rate. Claims \
with no evidence count for little.
- estimated_chance is your estimate, from 0 to 1, that this person is accepted. Then choose the \
outcome consistent with it.
- outcome is the final result. "success": accepted, offered, or funded. "failure": they reply \
with a no. "ignored": they never reply at all.
- decision_in_weeks is how many weeks until the person hears the final result, as this kind of \
organization would take (0 = this week). For "ignored", use 0.
- message is what the person is told, in plain words. Leave it empty for "ignored".
- rationale is private: how you weighed the base rate against the evidence of fit.\
"""

SCHEMA = {
    "type": "object",
    "properties": {
        "estimated_chance": {"type": "number"},
        "outcome": {"type": "string", "enum": ["success", "failure", "ignored"]},
        "decision_in_weeks": {"type": "integer"},
        "message": {"type": "string"},
        "rationale": {"type": "string"},
    },
    "required": ["estimated_chance", "outcome", "decision_in_weeks", "message", "rationale"],
    "additionalProperties": False,
}

FALLBACK_DECISION = {"estimated_chance": None, "outcome": "ignored", "decision_in_weeks": 0,
                     "message": "", "rationale": "referee output could not be parsed; defaulted to ignored"}


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


def parse_decision(text: str) -> tuple[dict, str | None]:
    """Validate and clamp the referee's JSON. Returns (decision, error-or-None)."""
    try:
        d = json.loads(text)
        if not isinstance(d, dict):
            raise ValueError("not an object")
    except ValueError as e:
        return dict(FALLBACK_DECISION), f"unparseable referee output: {e}"
    out = dict(FALLBACK_DECISION)
    out.update({k: d[k] for k in SCHEMA["properties"] if k in d})
    if out["outcome"] not in SCHEMA["properties"]["outcome"]["enum"]:
        return dict(FALLBACK_DECISION), f"unknown outcome {out['outcome']!r}"
    try:
        out["decision_in_weeks"] = max(0, min(26, int(out["decision_in_weeks"])))
        chance = out["estimated_chance"]
        out["estimated_chance"] = None if chance is None else max(0.0, min(1.0, float(chance)))
    except (TypeError, ValueError):
        return dict(FALLBACK_DECISION), "referee returned non-numeric fields"
    if out["outcome"] == "ignored":
        out["decision_in_weeks"], out["message"] = 0, ""
    return out, None
