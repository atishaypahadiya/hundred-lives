"""End-to-end check of world v3 with no model calls: a scripted agent, canned referee and contact
replies, and the real sandbox (container + Gitea), so deploy reads a real pushed repo. The run log
and repo bundles go to a temp dir, never runs/. Needs `orchestrator.sandbox up` and OrbStack.

    uv run python scripts/smoke_v3.py

Checks each v3 mechanic and its refusals, the blocked_by_world events, and that the contact,
reviewer and feed see a deployed site when it's linked.
"""

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import yaml  # noqa: E402

import orchestrator.run as R  # noqa: E402
from orchestrator import referee  # noqa: E402
from orchestrator.backends import Reply, ToolCall, Usage  # noqa: E402
from orchestrator.world import load_world  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
RUN_ID = "smokev3-001"
USER = RUN_ID
SITE = "https://conscious.mockweb.local"
PUSH = (f"cd {{d}} && git init -q -b main && git add -A && git -c user.email=s@agents.local -c user.name=s "
        f"commit -qm init && git push -q http://gitea:3000/{USER}/{{d}}.git main 2>&1 | tail -2")
INDEX = ("<html><head><title>Conscious</title><script src=\"app.js\"></script></head><body><h1>Conscious</h1>"
         "<p>Plan your study week in two minutes.</p><a href=\"about.html\">About</a>"
         "<form><input name=\"email\" placeholder=\"you@sjsu.edu\"><button>Join the beta</button></form></body></html>")


def call(name, **kw):
    return (name, kw)


# Week -> batches of tool calls (one batch per agent turn). After the last batch: wait.
SCRIPT = {
    1: [[call("shell", command=f"mkdir -p conscious/docs && cd conscious && printf '%s' '{INDEX}' > docs/index.html "
                                 "&& echo 'function plan(){return 42}' > docs/app.js "
                                 "&& printf '<p>Built by Self.</p>' > docs/about.html && echo 'print(1)' > app.py && cd .. && "
                                 + PUSH.format(d="conscious"))],
        [call("deploy", repo="conscious")],
        [call("message", contact_id="marcus", text=f"Try it: {SITE} (code: http://gitea:3000/{USER}/conscious)")],
        [call("invest_personal", amount=10**6)],  # refused: more than cash
        [call("invest_personal", amount=500)]],
    2: [[call("shell", command="pip install requests 2>&1 | tail -3; npx vercel --prod 2>&1 | tail -1")],
        [call("message", contact_id="stranger", text="hi")],  # refused: not a contact
        [call("apply", opening_id="oc-01", details=f"I build things; see {SITE}")],  # accepted at once (canned)
        [call("post", text=f"Shipped the Conscious beta: {SITE}")],
        [call("go_fulltime_on_venture", venture="Conscious")],  # ends oc-01; classes stay
        [call("leave", commitment_id="c1")]],  # refused: classes
    3: [[call("spend", item="contractor", weeks=1)],  # refused: $1,500 > $500 in the company
        [call("shell", command="mkdir -p api && cd api && echo 'print(1)' > app.py && echo 'x' > requirements.txt && cd .. && "
                                + PUSH.format(d="api"))],
        [call("deploy", repo="api")],  # refused: no index.html, server code
        [call("apply", opening_id="in-03", details="Data intern")],  # accepted, starts week 21
        [call("decline_offer", opening_id="in-03")]],
    4: [[call("decline_offer", opening_id="nope")],  # refused (3 refusals in one week would end it)
        [call("apply", opening_id="oc-04", details="Tutor")],  # starts now: ends full-time
        [call("deploy", repo="conscious")]],  # version 2
}


class Agent:
    name, model, deterministic, default_max_tokens = "scripted", "agent", False, 1000
    week = 0

    def cost(self, reply):
        return 0.0

    def conversation(self, system, tools):
        Agent.week += 1
        return Convo(list(SCRIPT.get(Agent.week, [])))


class Convo:
    n = 0

    def __init__(self, batches):
        self.batches, self.prompts = batches, []

    def add_user(self, text):
        self.prompts.append(text)
        PROMPTS.append(text)

    def add_tool_results(self, results, note=None):
        for c, out, err in results:
            OUTPUTS.append((Agent.week, c.name, out, err))

    def size_chars(self):
        return 1000

    def send(self, max_tokens):
        batch = self.batches.pop(0) if self.batches else [("wait", {"idea": "Conscious", "note": "done"})]
        Convo.n += 1
        return Reply("", [ToolCall(f"t{Convo.n}", n, kw) for n, kw in batch], Usage(), "tool_use", 0.0)


class Canned:
    """Referee, contacts, audience, judges and the reflection: fixed JSON, every prompt kept."""
    name, model, deterministic, default_max_tokens = "canned", "world", False, 1000

    def cost(self, reply):
        return 0.0

    def complete(self, system, user, max_tokens, schema=None, **kw):
        CALLS.append((system, user))
        props = (schema or {}).get("properties", {})
        if "message_if_success" in props:
            out = {"estimated_chance": 1.0, "decision_in_weeks": 0, "message_if_success": "Welcome aboard.",
                   "message_if_failure": "No.", "rationale": "canned"}
        elif "offer_kind" in props:
            out = {"reply": "Opened it. The planner is clear.", "offer_kind": "none", "offer_opening_id": "", "offer_amount": 0}
        elif "replies_if_breakout" in props:
            out = {"estimated_chance": 0.0, "replies_if_breakout": [], "replies_if_quiet": [], "rationale": "canned"}
        else:
            return Reply("Reflection: keep shipping.", [], Usage(), "end_turn", 0.0)
        return Reply(json.dumps(out), [], Usage(), "end_turn", 0.0)


PROMPTS, OUTPUTS, CALLS = [], [], []


def main():
    tmp = Path(tempfile.mkdtemp(prefix="smokev3-"))
    R.RUNS_DIR = tmp
    persona_path, world_path = ROOT / "agents/personas/self.yaml", ROOT / "world/student-v3.yaml"
    persona = yaml.safe_load(persona_path.read_text())
    world = load_world(world_path)
    # A dice seed whose first message to marcus gets a reply, so the contact call happens.
    seed = next(s for s in range(100) if referee.draw_message(s, "marcus", 1, 0.6)[0])
    run = R.Run(RUN_ID, persona, persona_path, world, world_path, Agent(), Canned(), Canned(), 10**9, 5,
                seed=seed, sandbox=True, dice_seed=seed)
    res = run.execute()
    log = [json.loads(l) for l in (tmp / f"{RUN_ID}.jsonl").read_text().splitlines()]
    ev = lambda t: [r for r in log if r["type"] == t]
    errors = [(r["week"], r["tool"], r["error"][:90]) for r in ev("tool_error")]
    checks = []

    def check(name, ok, detail=""):
        ok = bool(ok)
        checks.append(ok)
        print(f"{'ok ' if ok else 'FAIL'}  {name}" + (f"  [{detail}]" if detail and not ok else ""))

    check("run reached the horizon", res["reason"] == "horizon", res)
    d = ev("deploy")
    check("deploy served docs/ with the visitor view", d and d[0]["root"] == "docs/" and "Join the beta" in d[0]["view"]
          and "function plan" in d[0]["view"], d and d[0]["view"][:300])
    check("app.py isn't served", d and "app.py" not in d[0]["served"] and d[0]["not_served"] >= 1)
    check("redeploy is version 2", len(d) == 2 and d[1]["version"] == 2)
    m = [r for r in ev("message") if r["contact_id"] == "marcus"]
    check("marcus saw the site and the private link", m and m[0]["replied"] and m[0]["sites_seen"] == ["conscious"]
          and m[0]["private_link"], m)
    contact_user = next((u for s, u in CALLS if s.startswith("You write one person's reply")), "")
    check("contact prompt shows the site", "What you can see" in contact_user and "Plan your study week" in contact_user
          and "private code host" in contact_user)
    check("contact system has the no-promises rule", "never promise to send files" in run.contact_system)
    apply_user = next((u for s, u in CALLS if "Linked websites" in u and "Opening:" in u), "")
    check("reviewer sees the linked site", "Plan your study week" in apply_user)
    post_user = next((u for s, u in CALLS if "breakout_base_rate" in u), "")
    check("feed sees the linked site", "Linked websites" in post_user and "Plan your study week" in post_user)
    inv = ev("invest_personal")
    check("invest_personal moved $500, refused the rest", len(inv) == 1 and inv[0]["amount"] == 500
          and any(t == "invest_personal" for _, t, _ in errors))
    check("spend refusal names savings as a source",
          any(r["tool"] == "spend" and "invest_personal" in r["error"] for r in ev("tool_error")))
    ft = ev("go_fulltime")
    ended = [r for r in ev("commitment_ended") if r["reason"] == "go_fulltime"]
    check("go_fulltime ended the campus job, classes stayed", ft and ended and ended[0]["commitment"]["opening_id"] == "oc-01"
          and ft[0]["enrolled"])
    check("taking paid work ended full-time", any(r["reason"] == "took_paid_work" for r in ev("commitment_ended")))
    dec = ev("offer_declined")
    check("declined the upcoming internship", dec and dec[0]["opening_id"] == "in-03" and dec[0]["stage"] == "upcoming")
    kinds = sorted({r["kind"] for r in ev("blocked_by_world")})
    want = ["company_funds", "external_hosting", "internet", "outside_contacts", "private_link", "server_hosting", "visa_status"]
    check("blocked_by_world kinds", kinds == want, kinds)
    check("week prompt shows websites and savings", any("Your websites:" in p for p in PROMPTS)
          and any("your savings $500" in p for p in PROMPTS))
    status_apply = next((u for s, u in CALLS if "Opening:" in u and "own savings" in u), "")
    check("reviewer sees savings in the company and the website", "Websites: https://conscious.mockweb.local" in status_apply)
    check("gitea user deleted", bool(ev("gitea_user_deleted")))
    print(f"\n{sum(checks)}/{len(checks)} checks passed; tool errors: {errors}\nlog: {tmp / (RUN_ID + '.jsonl')}")
    sys.exit(0 if all(checks) else 1)


if __name__ == "__main__":
    main()
