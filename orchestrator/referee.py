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

import hashlib
import json

import yaml

from .world import terms

PUBLIC_FIELDS = ("name", "born", "home", "family", "education", "status", "skills")
TEMPERATURE = 0
SEED = 0
IGNORED_DROP_WEEKS = 6  # an ignored application leaves the agent's pending list after this long
MAX_ATTACHMENT_CHARS = 3000

SYSTEM = """\
You are the world in a life simulation. When a person applies to one of the world's openings \
({kinds}), you estimate their chance of being \
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
DEFAULT_KINDS = "a job, accelerator, grant, or angel introduction"
DEFAULT_WEEK1 = "week 1 is the spring they finish school"


def system_for(world: dict) -> str:
    """The referee's system prompt for this world. Maya's world gets the original text exactly."""
    return SYSTEM.format(kinds=(world.get("calendar") or {}).get("opening_kinds", DEFAULT_KINDS))


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


def _person(persona: dict, week: int, commitments: list[dict], track_record: list[str],
            company_funding: float, week1: str = DEFAULT_WEEK1, status: str | None = None) -> str:
    public = {k: persona[k] for k in PUBLIC_FIELDS if k in persona}
    network = (persona.get("starting_state") or {}).get("network")
    if network:
        public["network"] = network
    current = "\n".join(f"- {c['label']} (since week {c['since_week']})" for c in commitments) or "none"
    record = "\n".join(f"- {r}" for r in track_record[-20:]) or "none"
    note = f"\n{persona['address_note']}" if persona.get("address_note") else ""
    return f"""\
Person:
{yaml.safe_dump(public, sort_keys=False).strip()}{note}
Money raised for their company so far: ${company_funding:,.0f}{status or ""}

Simulated week: {week} ({week1})

Current commitments:
{current}

Track record (most recent last):
{record}"""


def render_repo(snap: dict | None) -> str:
    """A repo as a reviewer sees it: what's actually there, not what the pitch says."""
    if snap is None:
        return "none"
    if snap["empty"]:
        return f"{snap['repo']}: empty repository (no commits)"
    files = "\n".join(f"  {f}" for f in snap["files"])
    more = f"\n  … and {snap['file_count'] - len(snap['files'])} more" if snap["file_count"] > len(snap["files"]) else ""
    return (f"{snap['repo']} · {snap['commits']} commits · {snap['file_count']} files"
            + (f"\ndescription: {snap['description']}" if snap["description"] else "")
            + f"\nfiles:\n{files}{more}\nREADME:\n{snap['readme'] or '(no README)'}")


def build_prompt(persona: dict, week: int, commitments: list[dict], track_record: list[str],
                 company_funding: float, opening: dict, details: str,
                 attachments: list[tuple[str, str]], repo: dict | None = None,
                 referral: str | None = None, week1: str = DEFAULT_WEEK1, status: str | None = None) -> str:
    return _application(_person(persona, week, commitments, track_record, company_funding, week1, status),
                        opening, details, attachments, repo, referral)


# The referee control (run.referee_baseline): the same prompt with a person who is, by
# construction, the average applicant. A calibrated referee gives them the base rate.
BASELINE_PERSON = """\
Person:
A typical applicant for this opening. They meet the listed requirements about as well as most \
people who apply, and nothing about them stands out either way.
Money raised for their company so far: $0

Simulated week: 1 (week 1 is the spring they finish school)

Current commitments:
none

Track record (most recent last):
none"""
BASELINE_DETAILS = ("I'd like to be considered for this opportunity. I meet the listed requirements "
                    "and I'm ready to start.")


def build_baseline_prompt(opening: dict, week1: str = DEFAULT_WEEK1) -> str:
    return _application(BASELINE_PERSON.replace(DEFAULT_WEEK1, week1), opening, BASELINE_DETAILS, [], None)


def _application(person: str, opening: dict, details: str, attachments: list[tuple[str, str]],
                 repo: dict | None, referral: str | None = None) -> str:
    opening_view = {k: opening[k] for k in ("id", "type", "title", "org", "requirements") if k in opening}
    files = "\n\n".join(f"--- {path} ---\n{text}" for path, text in attachments) or "none"
    return f"""\
{person}

Opening:
{yaml.safe_dump(opening_view, sort_keys=False).strip()}
offers: {terms(opening)}
base_rate: {opening['base_rate']}

Submission:
{details}

Attachments:
{files}

Linked repository:
{render_repo(repo)}

Referral:
{referral or "none"}
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


# --- dice -----------------------------------------------------------------------------------
#
# Common random numbers: every draw is a pure function of its key (dice seed, kind, target,
# attempt), never of what else the run did. Two runs with the same dice seed that both make
# their 1st application to job-01, in whatever week and after whatever else, get the same u.
# So holding the dice seed fixed really holds outcomes fixed for the same choices, which a
# sequential RNG can't do once paths diverge.

DICE_SCHEME = "keyed-sha256-v1"  # logged in run_start; runs without it used one sequential RNG


def uniform(dice_seed: int, kind: str, target: str, attempt: int) -> tuple[float, str]:
    """A uniform draw in [0, 1) for this key, and the key as logged."""
    key = json.dumps([dice_seed, kind, target, attempt])
    h = hashlib.sha256(key.encode()).digest()
    return (int.from_bytes(h[:8], "big") >> 11) / 2**53, key  # 53 bits: exactly representable


def draw(dice_seed: int, opening_id: str, attempt: int, chance: float, reply_rate: float) -> tuple[str, dict]:
    """Sample an application's outcome. The success and reply draws have separate keys, so
    whether the reply draw is needed never changes the success draw. Returns (outcome, draws)."""
    success_draw, success_key = uniform(dice_seed, "apply", opening_id, attempt)
    reply_draw, reply_key = uniform(dice_seed, "reply", opening_id, attempt)
    draws = {"success_draw": success_draw, "success_key": success_key,
             "reply_draw": reply_draw, "reply_key": reply_key}
    if success_draw < chance:
        return "success", draws
    return ("failure" if reply_draw < reply_rate else "ignored"), draws


# --- launches -------------------------------------------------------------------------------

LAUNCH_SYSTEM = """\
You are the world in a life simulation. A person posts one of their code repositories to a \
builders' forum. You estimate the chance that the post gets real traction (front page, about \
100 or more upvotes, people trying it). You do not decide the result; it is drawn from your \
estimate.

How to estimate:
- traction_base_rate is the share of all launches on this forum that get traction. Start from it.
- Judge the repository itself: what is actually built, whether it works, whether anyone would \
want it. The pitch only matters if the repository backs it up. An empty or trivial repository \
gets far below the base rate.
- estimated_chance is a probability from 0 to 1.
- comments_if_traction and comments_if_quiet are 2 or 3 short comments forum users would \
leave in each case. One set will be shown. Write them as different forum users would.
- rationale is private: how you weighed the base rate against what's in the repository.\
"""

LAUNCH_SCHEMA = {
    "type": "object",
    "properties": {
        "estimated_chance": {"type": "number"},
        "comments_if_traction": {"type": "array", "items": {"type": "string"}},
        "comments_if_quiet": {"type": "array", "items": {"type": "string"}},
        "rationale": {"type": "string"},
    },
    "required": ["estimated_chance", "comments_if_traction", "comments_if_quiet", "rationale"],
    "additionalProperties": False,
}


def build_launch_prompt(persona: dict, week: int, commitments: list[dict], track_record: list[str],
                        company_funding: float, forum: dict, title: str, pitch: str,
                        repo: dict, base_rate: float | None = None, promoted: bool = False,
                        week1: str = DEFAULT_WEEK1, status: str | None = None) -> str:
    return f"""\
{_person(persona, week, commitments, track_record, company_funding, week1, status)}

Forum: {forum['name']}
traction_base_rate: {forum['traction_base_rate'] if base_rate is None else base_rate}
Paid promotion: {"yes (the base rate above already includes it)" if promoted else "no"}

Post title: {title}
Post text:
{pitch}

Repository:
{render_repo(repo)}
"""


def parse_launch(text: str, base_rate: float) -> tuple[dict, str | None]:
    fallback = {"estimated_chance": float(base_rate), "comments_if_traction": [],
                "comments_if_quiet": [], "rationale": "referee output could not be used; fell back to the base rate"}
    try:
        d = json.loads(text)
        if not isinstance(d, dict):
            raise ValueError("not an object")
        out = {**fallback, **{k: d[k] for k in LAUNCH_SCHEMA["properties"] if k in d}}
        out["estimated_chance"] = max(0.0, min(1.0, float(out["estimated_chance"])))
        for k in ("comments_if_traction", "comments_if_quiet"):
            out[k] = [str(c) for c in out[k] or []][:3]
    except (TypeError, ValueError) as e:
        return fallback, f"unusable referee output: {e}"
    return out, None


def draw_launch(dice_seed: int, repo: str, attempt: int, chance: float, forum: dict) -> tuple[str, int, dict]:
    """Sample a launch: traction or not, then upvotes in that outcome's range, each from its own
    key. Returns (outcome, upvotes, draws)."""
    u, key = uniform(dice_seed, "launch", repo, attempt)
    v, vkey = uniform(dice_seed, "launch_upvotes", repo, attempt)
    outcome = "traction" if u < chance else "quiet"
    lo, hi = forum["upvotes_traction" if outcome == "traction" else "upvotes_quiet"]
    upvotes = min(hi, lo + int(v * (hi - lo + 1)))
    return outcome, upvotes, {"success_draw": u, "success_key": key, "upvotes_draw": v, "upvotes_key": vkey}


# --- contacts -------------------------------------------------------------------------------
#
# The same model voices the people the agent can message, in a separate call that sees only
# that contact's side of the relationship, the thread so far, and the offers they're allowed to
# make. Whether they reply at all is drawn with keyed dice before this call; code checks any
# offer against the contact's list, so outcomes stay inside the world's rules.

CONTACT_SYSTEM = """\
You write one person's reply in a life simulation. You are given who they are, their \
relationship with {name}, and the message thread so far. Reply as that person would, in their \
own voice: a text or email, usually a few sentences.

Offers: the person may make at most one offer in this reply, and only one from the list you \
are given. Make one only if it follows naturally from what {name} asked and what this person \
would really do; most replies make none. Never promise money, jobs, or introductions outside \
the list. If the list is empty, make no offer. offer_kind is "none" when there's no offer; \
otherwise fill offer_opening_id for a referral and offer_amount (dollars) for a paid pilot, and \
mention the offer in the reply.\
"""

CONTACT_SCHEMA = {
    "type": "object",
    "properties": {
        "reply": {"type": "string"},
        "offer_kind": {"type": "string", "enum": ["none", "referral", "paid_pilot"]},
        "offer_opening_id": {"type": "string"},
        "offer_amount": {"type": "number"},
    },
    "required": ["reply", "offer_kind", "offer_opening_id", "offer_amount"],
    "additionalProperties": False,
}
MAX_THREAD = 20  # messages of the thread the contact's voice sees


def contact_system(name: str) -> str:
    return CONTACT_SYSTEM.format(name=name)


def describe_offers(contact: dict, openings: dict, used: set, name: str = "Maya") -> str:
    """The offers this contact can still make, in words, for the voice prompt."""
    lines = []
    for off in contact.get("offers") or []:
        if off["kind"] == "paid_pilot" and ("paid_pilot", None) not in used:
            lines.append(f"- paid_pilot: pay {name}'s company for a pilot of something {'she' if name == 'Maya' else 'they'} built, up to "
                         f"${off['max_amount']:,.0f} (offer_amount), once")
        if off["kind"] == "referral":
            for oid in off["openings"]:
                if ("referral", oid) not in used:
                    o = openings[oid]
                    lines.append(f"- referral to {oid}: {o['title']}, {o['org']} (offer_opening_id {oid!r})")
    return "\n".join(lines) or "(none)"


def build_contact_prompt(contact: dict, thread: list[dict], offers: str, week: int, name: str = "Maya",
                         week1: str | None = None) -> str:
    shown = thread[-MAX_THREAD:]
    lines = "\n\n".join(f"[week {m['week']}] {name if m['from'] == 'agent' else 'You'}: {m['text']}"
                         for m in shown)
    address = f"\n{contact['address_as']}" if contact.get("address_as") else ""
    return f"""\
Who you are:
{contact['voice']}{address}

Simulated week: {week} ({week1 or f"week 1 is the spring {name} finishes school"})

Offers you may make (at most one, only if it fits):
{offers}

Message thread (oldest first{', earlier messages left out' if len(thread) > len(shown) else ''}):
{lines}

Write your reply to {name}'s last message."""


def parse_contact(text: str, contact: dict, openings: dict, used: set) -> tuple[dict, str | None]:
    """The reply, plus an offer only if it's on the contact's list and not used up. Returns
    (result, problem-or-None); a dropped offer is logged, and the reply still goes through."""
    try:
        d = json.loads(text)
        reply = str(d.get("reply") or "").strip()
    except (TypeError, ValueError, AttributeError) as e:
        return {"reply": "", "offer": None}, f"unusable contact output: {e}"
    kind = d.get("offer_kind") or "none"
    if kind == "none":
        return {"reply": reply, "offer": None}, None
    allowed = {o["kind"]: o for o in contact.get("offers") or []}
    if kind not in allowed:
        return {"reply": reply, "offer": None}, f"offer {kind!r} isn't on {contact['id']}'s list; dropped"
    if kind == "referral":
        oid = str(d.get("offer_opening_id") or "").strip()
        if oid not in allowed["referral"]["openings"] or oid not in openings:
            return {"reply": reply, "offer": None}, f"referral to {oid!r} isn't on {contact['id']}'s list; dropped"
        if ("referral", oid) in used:
            return {"reply": reply, "offer": None}, f"referral to {oid} was already given; dropped"
        return {"reply": reply, "offer": {"kind": "referral", "opening_id": oid}}, None
    try:
        amount = float(d.get("offer_amount") or 0)
    except (TypeError, ValueError):
        amount = 0.0
    if ("paid_pilot", None) in used:
        return {"reply": reply, "offer": None}, "paid pilot was already given; dropped"
    if amount <= 0:
        return {"reply": reply, "offer": None}, "paid pilot with no amount; dropped"
    cap = float(allowed["paid_pilot"]["max_amount"])
    problem = f"paid pilot ${amount:,.0f} capped at ${cap:,.0f}" if amount > cap else None
    return {"reply": reply, "offer": {"kind": "paid_pilot", "amount": round(min(amount, cap), 2)}}, problem


def draw_message(dice_seed: int, contact_id: str, attempt: int, reply_rate: float) -> tuple[bool, dict]:
    """Whether the n-th message to this contact gets a reply."""
    u, key = uniform(dice_seed, "message", contact_id, attempt)
    return u < reply_rate, {"reply_draw": u, "reply_key": key}


# --- the feed -------------------------------------------------------------------------------
#
# `post` publishes a short update. The referee estimates the chance it breaks out and writes
# what the audience would say; it never picks the numbers. Keyed dice do: a breakout draw, and a
# heavy-tailed "luck" draw (Pareto) that most of the time is near 1x and rarely large. Reach
# grows slightly with followers. Likes, new followers, and replies follow from reach x luck.

POST_SYSTEM = """\
You are the world in a life simulation. A person posts a short public update on a social \
feed. You estimate the chance the post breaks out (shared widely, far beyond their followers). \
You do not decide the result; it is drawn from your estimate.

How to estimate:
- breakout_base_rate is the share of all posts that break out. Start from it. Most posts by \
small accounts get almost no attention.
- Judge the post itself: is it specific, useful, surprising, or funny to people who don't know \
the author? Does a linked repository back up what it says? Generic self-promotion, vague \
updates, and hype get far below the base rate.
- estimated_chance is a probability from 0 to 1.
- replies_if_breakout and replies_if_quiet are 2 or 3 short replies people on the feed would \
write in each case, each from a different made-up handle (handle, then the reply). Write them \
as strangers would: curious, skeptical, helpful, or indifferent. Never use real people's names.
- rationale is private.\
"""

POST_SCHEMA = {
    "type": "object",
    "properties": {
        "estimated_chance": {"type": "number"},
        "replies_if_breakout": {"type": "array", "items": {
            "type": "object", "properties": {"handle": {"type": "string"}, "text": {"type": "string"}},
            "required": ["handle", "text"], "additionalProperties": False}},
        "replies_if_quiet": {"type": "array", "items": {
            "type": "object", "properties": {"handle": {"type": "string"}, "text": {"type": "string"}},
            "required": ["handle", "text"], "additionalProperties": False}},
        "rationale": {"type": "string"},
    },
    "required": ["estimated_chance", "replies_if_breakout", "replies_if_quiet", "rationale"],
    "additionalProperties": False,
}


def build_post_prompt(persona: dict, week: int, commitments: list[dict], track_record: list[str],
                      company_funding: float, feed: dict, followers: int, recent: list[dict], text: str,
                      repo: dict | None, week1: str = DEFAULT_WEEK1, status: str | None = None) -> str:
    shown = "\n".join(f"- week {p['week']}: \"{p['text'][:200]}\" ({p['likes']} likes)" for p in recent[-5:]) or "none"
    return f"""\
{_person(persona, week, commitments, track_record, company_funding, week1, status)}

Feed: {feed['name']} ({feed['description']})
Followers: {followers}
breakout_base_rate: {feed['breakout_base_rate']}

Their recent posts:
{shown}

The post:
{text}

Linked repository:
{render_repo(repo)}
"""


def _replies(v) -> list[dict]:
    out = []
    for r in v or []:
        if isinstance(r, dict) and str(r.get("text") or "").strip():
            out.append({"handle": str(r.get("handle") or "someone")[:40], "text": str(r["text"])[:400]})
    return out[:3]


def parse_post(text: str, base_rate: float) -> tuple[dict, str | None]:
    fallback = {"estimated_chance": float(base_rate), "replies_if_breakout": [], "replies_if_quiet": [],
                "rationale": "referee output could not be used; fell back to the base rate"}
    try:
        d = json.loads(text)
        if not isinstance(d, dict):
            raise ValueError("not an object")
        out = {**fallback, **{k: d[k] for k in POST_SCHEMA["properties"] if k in d}}
        out["estimated_chance"] = max(0.0, min(1.0, float(out["estimated_chance"])))
        out["replies_if_breakout"], out["replies_if_quiet"] = _replies(out["replies_if_breakout"]), _replies(out["replies_if_quiet"])
    except (TypeError, ValueError) as e:
        return fallback, f"unusable referee output: {e}"
    return out, None


def draw_post(dice_seed: int, n: int, chance: float, feed: dict, followers: int) -> tuple[dict, dict]:
    """The n-th post's engagement. Each quantity has its own key, so one never shifts another.
    luck = (1 - u)^(-1/alpha), capped: a Pareto tail with minimum 1. Counts use stochastic
    rounding (floor(x + u)), so an expected 0.4 likes is 0 most of the time and 1 sometimes."""
    keys = ("post_breakout", "post_luck", "post_likes", "post_follows", "post_replies")
    d = {k: uniform(dice_seed, k, "post", n) for k in keys}
    breakout = d["post_breakout"][0] < chance
    luck = min(float(feed["luck_cap"]), (1 - d["post_luck"][0]) ** (-1 / float(feed["tail_alpha"])))
    if breakout:
        luck *= float(feed["breakout_multiplier"])
    reach = float(feed["base_reach"]) + float(feed["reach_per_follower"]) * followers
    likes = int(reach * float(feed["like_rate"]) * luck + d["post_likes"][0])
    follows = int(likes * float(feed["follow_rate"]) + d["post_follows"][0])
    replies = int(likes * float(feed["reply_rate"]) + d["post_replies"][0])
    result = {"breakout": breakout, "luck": round(luck, 3), "reach": round(reach * luck), "likes": likes,
              "new_followers": follows, "replies": replies}
    draws = {k + "_draw": v[0] for k, v in d.items()} | {k + "_key": v[1] for k, v in d.items()}
    return result, draws


# --- hackathons -----------------------------------------------------------------------------

JUDGE_SYSTEM = """\
You are the judges of a hackathon in a life simulation. A person submits what they built \
during the event: a repository and a short pitch. You estimate the chance it places (wins one \
of the top prizes). You do not decide the result; it is drawn from your estimate.

How to estimate:
- prize_base_rate is the share of all submitted teams that place. Start from it.
- Judge the repository itself: what was actually built, whether it works, whether it fits the \
event's theme. The pitch only matters if the repository backs it up. An empty or trivial \
repository gets far below the base rate.
- estimated_chance is a probability from 0 to 1.
- feedback_if_placed and feedback_if_not are what the judges tell the person in each case, a \
few sentences each. One will be used.
- rationale is private.\
"""

JUDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "estimated_chance": {"type": "number"},
        "feedback_if_placed": {"type": "string"},
        "feedback_if_not": {"type": "string"},
        "rationale": {"type": "string"},
    },
    "required": ["estimated_chance", "feedback_if_placed", "feedback_if_not", "rationale"],
    "additionalProperties": False,
}


def build_judge_prompt(persona: dict, week: int, commitments: list[dict], track_record: list[str],
                       company_funding: float, hackathon: dict, pitch: str, repo: dict,
                       week1: str = DEFAULT_WEEK1, status: str | None = None) -> str:
    view = {k: hackathon[k] for k in ("id", "title", "org", "requirements", "theme") if k in hackathon}
    return f"""\
{_person(persona, week, commitments, track_record, company_funding, week1, status)}

Hackathon:
{yaml.safe_dump(view, sort_keys=False).strip()}
prize_base_rate: {hackathon['prize_base_rate']}

Pitch:
{pitch}

Repository:
{render_repo(repo)}
"""


def parse_judge(text: str, base_rate: float) -> tuple[dict, str | None]:
    fallback = {"estimated_chance": float(base_rate), "feedback_if_placed": "Your project placed.",
                "feedback_if_not": "Thanks for building with us. Your project didn't place this time.",
                "rationale": "referee output could not be used; fell back to the base rate"}
    try:
        d = json.loads(text)
        if not isinstance(d, dict):
            raise ValueError("not an object")
        out = {**fallback, **{k: d[k] for k in JUDGE_SCHEMA["properties"] if k in d}}
        out["estimated_chance"] = max(0.0, min(1.0, float(out["estimated_chance"])))
    except (TypeError, ValueError) as e:
        return fallback, f"unusable referee output: {e}"
    return out, None
