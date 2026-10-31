"""Every number, quote, link and chart on the project page (docs/index.html), pulled from the logs
and results files, never retyped. Writes them into the page and lists each with its source.

    uv run python analysis/site_facts.py            # fill docs/index.html, write analysis/site_facts.md
    uv run python analysis/site_facts.py --check    # exit 1 if the page isn't what the data says

In the page:
  <span data-fact="id">…</span>   the element's text becomes the fact's value
  <a data-link="id" href="…">     the href becomes the fact's link (a replay week, a results file)
  <script id="site-data">          chart data, quotes and prediction tables, as JSON
Claims the prose makes in words ("none of them", "every life") are asserted here, so the page
can't say them unless the data does. Reads logs from runs/ and the experiment worktrees.
"""

import argparse
import glob
import html
import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "analysis"))

import analysis.diversity as dv  # noqa: E402
import analysis.exp03 as e3  # noqa: E402
import analysis.self_blame as sb  # noqa: E402
from analysis.self_lives import profile  # noqa: E402

PAGE = ROOT / "docs" / "index.html"
SOURCES = ROOT / "analysis" / "site_facts.md"
VIEWER = ROOT.parent / "hundred-lives-viewer" / "runs"
EXP02B = ROOT.parent / "hundred-lives-exp02b" / "runs"
EXP03 = ROOT.parent / "hundred-lives-exp03" / "runs"
REPO = "https://github.com/atishaypahadiya/hundred-lives/blob/master/"

FACTS: dict[str, dict] = {}
CLAIMS: list[tuple[str, str]] = []
DATA: dict = {}


def rel(p) -> str:
    p = Path(p).resolve()
    try:
        return str(p.relative_to(ROOT))
    except ValueError:
        return "../" + str(p.relative_to(ROOT.parent))


def fact(fid: str, value, source: str, link: str | None = None):
    FACTS[fid] = {"value": str(value), "source": source, "link": link}


def claim(ok: bool, text: str, source: str):
    if not ok:
        sys.exit(f"claim no longer holds: {text} ({source})")
    CLAIMS.append((text, source))


def logs(pattern: str) -> list[str]:
    return sorted(f for f in glob.glob(str(pattern)) if "baseline" not in f)


def find(path: str, pred) -> tuple[int, dict]:
    for n, line in enumerate(open(path), 1):
        r = json.loads(line)
        if pred(r):
            return n, r
    sys.exit(f"not found in {path}")


def sentence(text: str, needle: str) -> str:
    """The sentence of `text` that contains `needle`, verbatim (a quote mark that opens the
    sentence is kept, so a quoted phrase stays balanced)."""
    i = text.index(needle)
    start = max(text.rfind(c, 0, i) for c in ".!?\n") + 1
    ends = [j for c in ".!?" if (j := text.find(c, i + len(needle))) >= 0]
    return text[start:min(ends) + 1].strip()


def replay(run_id: str, week: int) -> str:
    return f"runs/#/run/{run_id}?w={week}"


def life_no(run_id: str) -> int:
    return int(run_id.rsplit("-", 1)[-1])


def life_title(run_id: str, persona: str) -> str:
    """A life's name as the viewer titles it ("Self, base life 3", "Self, Track A", "Maya, exp 01 a life 4").
    viewer/index.html lifeTitle() is the same rule; the quote-link check compares the two."""
    m = re.match(r"^[a-z]+-(?:(.*?)-)?(\d{8}-\d{6})-(\d{3})$", run_id)
    if not m:
        return f"{persona}, {run_id}"
    tag, n = re.sub(r"^(\w+)-\1$", r"\1", m.group(1) or ""), int(m.group(3))
    if re.fullmatch(r"tracka\d*", tag):
        return f"{persona}, Track A"
    if e := re.fullmatch(r"exp(\d\d)([a-z])", tag):
        tag = f"exp {e.group(1)} {e.group(2)}"
    elif v := re.fullmatch(r"showv(\d+)", tag):
        tag = f"showcase v{v.group(1)}"
    return f"{persona}, {tag} life {n}" if tag else f"{persona}, life {n}"


def cite(run_id: str, persona: str, week: int) -> str:
    return f"{life_title(run_id, persona)}, week {week}"


def results_table(path: Path, header: str) -> list[list[str]]:
    """Rows of the first markdown table after `header` (cells stripped, separator row dropped)."""
    text = path.read_text()
    block = text[text.index(header):]
    rows = []
    for line in block.splitlines()[1:]:
        if line.startswith("|"):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if not set("".join(cells)) <= set("-: "):
                rows.append(cells)
        elif rows:
            break
    return rows


def md_plain(s: str) -> str:
    return re.sub(r"\*\*|`", "", s)


# ---------------------------------------------------------------- hero and story

def hero_and_story():
    runs_json = ROOT / "docs" / "runs" / "runs.json"
    n = len(json.loads(runs_json.read_text())["runs"])
    fact("lives.published", n, f"{rel(runs_json)}: runs listed (built by scripts/publish_runs.sh from viewer/publish.yaml)")

    base3 = str(VIEWER / "self-base-20261029-082337-003.jsonl")
    line, r = find(base3, lambda r: r["type"] == "memory_reflection" and r["week"] == 104)
    q = sentence(r["reflection"], "No internet here")
    # Inside the pull quote, the reflection's own double quotes become single (typography only).
    q = q.replace('"', "\u2018", 1).replace('"', "\u2019", 1)
    fact("q.nointernet", q, f"{rel(base3)} line {line}: memory_reflection, week 104 (its double quotes shown as single, inside the pull quote)", replay(r["run_id"], 104))
    fact("q.nointernet.cite", cite(r["run_id"], "Self", r["week"]), f"{rel(base3)} line {line}: run id and week")

    # Track A
    ta = str(VIEWER / "self-tracka4-20261029-035616-001.jsonl")
    weeks = [json.loads(l) for l in open(ta)]
    last = [r for r in weeks if r["type"] == "week_end"][-1]
    posts = [r for r in weeks if r["type"] == "post"]
    jobs = [c["label"] for c in last["state"]["commitments"] if c.get("kind") != "academics"]
    fact("tracka.weeks", last["week"], f"{rel(ta)}: last week_end")
    job = jobs[0]
    title, org = job.split(", ", 1)
    fact("tracka.job_prose", f"{title.lower()} at {org}", f"{rel(ta)}: commitments at week {last['week']} ('{job}'), as a phrase")
    fact("tracka.followers", posts[-1]["followers_after"], f"{rel(ta)}: followers_after on the last post")
    claim(not any(r["type"] == "launch" for r in weeks), "Track A never launched Conscious", rel(ta))

    # Part A base: five lives
    base = [profile(f) for f in logs(VIEWER / "self-base-*.jsonl")]
    fact("base.n", len(base), "../hundred-lives-viewer/runs/self-base-*.jsonl: lives (analysis/self_lives.py)")
    in_job = sum(job in b["commitments"] for b in base)
    holding_nothing = sum(not b["commitments"] for b in base)
    fact("base.same_job", in_job, f"same logs: lives holding '{job}' at week 104")
    fact("base.nothing", holding_nothing, "same logs: lives holding no job at week 104")
    claim(all(b["costly"] == 0 for b in base), "no part A base life made a costly bet", "self_lives.py costly bets")
    claim(all(b["messages"]["marcus"][0] > 0 for b in base), "every base life messaged Marcus", "self_lives.py messages")
    claim(min(b["trained_weeks"] for b in base) >= 100, "every base life trained in at least 100 of 104 weeks", "self_lives.py trained weeks")

    line, r = find(base3, lambda r: r["type"] == "message" and r["week"] == 22 and r["contact_id"] == "kabir")
    fact("q.kabir", sentence(r["reply"], "most 2024 sentence"), f"{rel(base3)} line {line}: message reply from kabir, week 22", replay(r["run_id"], 22))
    fact("q.kabir.cite", cite(r["run_id"], "Self", r["week"]), f"{rel(base3)} line {line}: run id and week")

    v31 = str(EXP03 / "self-v3-20261030-033852-001.jsonl")
    line, r = find(v31, lambda r: r["type"] == "message" and r["week"] == 40 and r["contact_id"] == "marcus")
    fact("q.marcus", sentence(r["reply"], "drowning in homework"), f"{rel(v31)} line {line}: message reply from marcus, week 40", replay(r["run_id"], 40))
    fact("q.marcus.cite", cite(r["run_id"], "Self", r["week"]), f"{rel(v31)} line {line}: run id and week")
    claim(not r.get("sites_seen"), "Marcus's week-40 reply wasn't a reaction to a site (none linked), so the story doesn't present it as one", f"{rel(v31)} line {line}: sites_seen")

    partb = ROOT / "experiments" / "02-results-partB.md"
    m = re.search(r"The pattern sits in (\d+) of (\d+) lives", partb.read_text())
    fact("wall.lives", m.group(1), f"{rel(partb)}: \"{m.group(0)}\"")
    fact("wall.of", m.group(2), f"{rel(partb)}: \"{m.group(0)}\"")
    m = re.search(r"blamed the agent itself in (\d+) of (\d+) cases", partb.read_text())
    fact("blame.self", m.group(1), f"{rel(partb)}: \"{m.group(0)}\" (provisional: LLM-coded)")
    fact("blame.of", m.group(2), f"{rel(partb)}: \"{m.group(0)}\"")


# ---------------------------------------------------------------- experiment 01

def exp01():
    conds = {}
    for c in "abcd":
        runs = [dv.load(Path(f)) for f in logs(ROOT / f"runs/maya-exp01{c}-*.jsonl")]
        conds[c] = [r for r in runs if r and r["reason"] == "horizon"]
    allr = [r for rs in conds.values() for r in rs]
    fact("e01.n", len(allr), "runs/maya-exp01{a,b,c,d}-*.jsonl: lives that reached week 52 (analysis/diversity.py)")

    def job_at_end(path):
        last = [json.loads(l) for l in open(path) if '"week_end"' in l][-1]
        return any((c.get("time_cost") or 0) >= 5 and not (c.get("opening_id") or "").startswith("acc-")
                   for c in last["state"]["commitments"])
    files = [f for c in "abcd" for f in logs(ROOT / f"runs/maya-exp01{c}-*.jsonl")]
    jobs = sum(job_at_end(f) for f in files)
    fact("e01.jobs", jobs, "same logs: a full-time commitment (time cost 5, not an accelerator) in the last week_end")
    types = {c: len({tuple(k for i, k in enumerate(r["states"]) if i == 0 or k != r["states"][i - 1]) for r in rs})
             for c, rs in conds.items()}
    fact("e01.types_min", min(types.values()), f"same logs: distinct commitment paths per condition, repeats collapsed {types}")
    fact("e01.types_max", max(types.values()), "same")
    fact("e01.per_cond", len(conds["a"]), "same logs: lives per condition")
    claim(len({len(rs) for rs in conds.values()}) == 1, "the four experiment 01 conditions have equal sizes", "same logs")
    fact("e01.groups", len(conds), "same logs: conditions (a, b, c, d)")
    # chart: each life's weekly state, without the +raised suffix; sorted by the week a job first starts
    rows = []
    for c, rs in conds.items():
        for r in rs:
            st = [s.split("+")[0] for s in r["states"]]
            rows.append({"id": r["run_id"], "s": "".join({"none": "n", "job": "j", "part-time": "p", "accelerator": "a", "broke": "b"}[x] for x in st)})
    rows.sort(key=lambda r: (r["s"].find("j") if "j" in r["s"] else 999, r["s"]))
    DATA["e01"] = {"rows": rows, "source": "runs/maya-exp01*-*.jsonl via analysis/diversity.py (weekly commitment state)"}
    res = ROOT / "experiments" / "01-diversity-attribution.md"
    preds = results_table(res, "| # | prediction")
    DATA["e01"]["predictions"] = [{"n": r[0], "text": md_plain(r[1]), "result": md_plain(r[2]), "verdict": md_plain(r[3])} for r in preds]
    DATA["e01"]["predictions_source"] = f"{rel(res)}: predictions table"


# ---------------------------------------------------------------- experiment 02

E02 = [("base", "as written", VIEWER / "self-base-*.jsonl"), ("nosolo", "no \u201csolo\u201d", EXP02B / "self-nosolo-nosolo-*.jsonl"),
       ("socialgoal", "social goal", EXP02B / "self-socialgoal-socialgoal-*.jsonl"),
       ("goalonly", "goal only", EXP02B / "self-goalonly-goalonly-*.jsonl"),
       ("hiddenstreak", "streak hidden", EXP02B / "self-hiddenstreak-*.jsonl")]


def exp02():
    conds = []
    for key, label, pat in E02:
        ps = [profile(f) for f in logs(pat)]
        ps = [p for p in ps if p["reason"] == "horizon"]
        conds.append({"key": key, "label": label, "lives": [{"run": p["run"], "messages": sum(s for s, _ in p["messages"].values()),
                                                             "posts": p["posts"], "contacts": sum(s > 0 for s, _ in p["messages"].values())} for p in ps]})
    DATA["e02"] = {"conditions": conds, "source": "analysis/self_lives.py profile() on each life's log"}
    fact("e02.n", sum(len(c["lives"]) for c in conds), "../hundred-lives-viewer/runs/self-base-*, ../hundred-lives-exp02b/runs/self-*: lives at week 104")
    fact("e02.versions", len(conds), "same: persona versions")
    by = {c["key"]: c["lives"] for c in conds}
    pairs = list(zip(by["base"], by["socialgoal"]))
    fact("e02.social_msgs_up", sum(s["messages"] > b["messages"] for b, s in pairs), "self_lives.py: seed pairs (base life i vs social-goal life i) with more messages")
    fact("e02.social_posts_up", sum(s["posts"] > b["posts"] for b, s in pairs), "self_lives.py: seed pairs with more posts")
    fact("e02.pairs", len(pairs), "self_lives.py: seed pairs")
    claim(max(l["posts"] for l in by["goalonly"]) <= 3 and min(l["posts"] for l in by["base"]) >= 4,
          "with the goal alone, posting nearly stopped (at most 3 posts a life, against 4 to 7 as written)", "self_lives.py posts")
    WIDE = 5
    wide = sum(l["contacts"] == WIDE for l in by["goalonly"])
    fact("e02.goalonly_five", wide, f"self_lives.py: goal-only lives that wrote to {WIDE} different contacts")
    fact("e02.wide", WIDE, "the contact count that sentence uses")
    sizes = {len(c["lives"]) for c in conds}
    claim(len(sizes) == 1, "every experiment 02 version has the same number of lives", "self_lives.py")
    fact("e02.per_version", sizes.pop(), "same logs: lives per persona version")
    fact("e02.base_contacts", f"{min(l['contacts'] for l in by['base'])} to {max(l['contacts'] for l in by['base'])}", "self_lives.py: distinct contacts per base life")
    partb = ROOT / "experiments" / "02-results-partB.md"
    t = partb.read_text()
    preds = re.findall(r"^(\d)\. \*\*(.+?):\*\* (.+?) \*\*(Not met|Met)\.\*\*$", t, re.M)
    DATA["e02"]["predictions"] = [{"n": n, "text": md_plain(p), "result": md_plain(r).rstrip(", "), "verdict": v.lower()} for n, p, r, v in preds]
    DATA["e02"]["predictions_source"] = f"{rel(partb)}: Hidden-streak prediction (pre-registered)"
    claim(len(preds) == 2, "experiment 02 has two pre-registered predictions (hidden streak)", rel(partb))


# ---------------------------------------------------------------- experiment 03

def exp03():
    prompt = e3.stall_prompt()
    lives = []
    for f in logs(EXP03 / "self-v3-*.jsonl"):
        L = e3.life_v3(f)
        recs = [json.loads(l) for l in open(f)]
        first_contact = min((r["week"] for r in recs if r["type"] == "message" and r.get("sites_seen")), default=None)
        first_public = min((r["week"] for r in recs if r["type"] in ("post", "referee_decision", "launch", "hackathon_result")
                            and r.get("sites_seen")), default=None)
        labels = e3.load_stall(L["run_id"])
        backend = [ref["week"] for ref in L["reflections"]
                   if (lab := labels.get(sb.label_key(prompt, ref["text"])))
                   and any(x["category"] == "backend" for x in lab["reasons"])]
        lives.append({"life": life_no(L["run_id"]), "run": L["run_id"], "deploys": [w for w, _, _ in L["deploys"]],
                      "first_contact_link": first_contact, "first_public_link": first_public, "backend": backend,
                      "invested": L["invested"], "declined": len(L["declined"]), "fulltime": L["fulltime_week"]})
    DATA["e03"] = {"lives": lives, "source": "analysis/exp03.py life_v3() on each log; backend weeks from the stall-reasons labels (analysis/labels/stall_reasons/)"}
    fact("e03.n", len(lives), "../hundred-lives-exp03/runs/self-v3-*.jsonl: lives at week 104")
    fact("e03.deployed", sum(bool(l["deploys"]) for l in lives), "exp03.py: lives with at least one deploy")
    fact("e03.backend_lives", sum(bool(l["backend"]) for l in lives), "stall-reasons labels: lives with a reflection naming a backend")
    claim(all(l["invested"] == 0 for l in lives), "no v3 life put savings in", "exp03.py: invest_personal")
    claim(all(l["declined"] == 0 for l in lives), "no v3 life declined an offer", "exp03.py: offer_declined")
    claim(all(not e3.life_v3(f)["backend"] for f in logs(EXP03 / "self-v3-*.jsonl")),
          "no v3 life tried anything server-side (backend_needed, derived with orchestrator/limits.py)", "exp03.py life_v3()")
    late = [l for l in lives if l["first_contact_link"] and l["deploys"] and l["first_contact_link"] - l["deploys"][0] >= 8]
    fact("e03.waited", len(late), "exp03.py: lives whose first link to a contact came 8+ weeks after the first deploy")
    gaps = sorted(l["first_contact_link"] - l["deploys"][0] for l in late)
    fact("e03.wait_min", gaps[0], "same: shortest of those waits, in weeks")
    fact("e03.wait_max", gaps[-1], "same: longest")
    res = ROOT / "experiments" / "03-results.md"
    preds = results_table(res, "| | prediction")
    DATA["e03"]["predictions"] = [{"n": r[0], "text": md_plain(r[1]), "result": md_plain(r[2]), "verdict": md_plain(r[3]),
                                   "provisional": "self-blame" in r[1]} for r in preds]
    DATA["e03"]["predictions_source"] = f"{rel(res)}: Predictions table"


# ---------------------------------------------------------------- how it works

def how():
    v31 = str(EXP03 / "self-v3-20261030-033852-001.jsonl")
    _, start = find(v31, lambda r: r["type"] == "run_start")
    tools = [t["name"] for t in start["tools"]]
    DATA["tools"] = {"names": tools, "source": f"{rel(v31)}: run_start.tools (world v3)"}
    fact("tools.n", len(tools), f"{rel(v31)}: run_start.tools")
    fact("horizon.self", start["horizon"], f"{rel(v31)}: run_start.horizon")
    m01_start = json.loads(open(logs(ROOT / "runs/maya-exp01a-*.jsonl")[0]).readline())
    fact("horizon.maya", m01_start["horizon"], "runs/maya-exp01a-*.jsonl: run_start.horizon")
    from orchestrator.memory import COMPRESS_EVERY
    fact("reflect.every", COMPRESS_EVERY, "orchestrator/memory.py: COMPRESS_EVERY (weeks between reflections)")
    pretty = lambda m: m.split(":")[-1].replace("claude-", "Claude ").replace("-5-5", " 5.5").replace("haiku", "Haiku").replace("sonnet", "Sonnet")
    fact("model.self", pretty(start["models"]["agent"]), f"{rel(v31)}: run_start.models.agent")
    fact("model.referee", pretty(start["models"]["referee"]), f"{rel(v31)}: run_start.models.referee")
    m01 = json.loads(open(logs(ROOT / "runs/maya-exp01a-*.jsonl")[0]).readline())["models"]["agent"]
    fact("model.maya", pretty(m01), "runs/maya-exp01a-*.jsonl: run_start.models.agent")
    line, r = find(v31, lambda r: r["type"] == "message" and r["week"] == 40 and r["contact_id"] == "mei")
    raw = open(v31).read().splitlines()[line - 1]
    claim(json.loads(raw) == r, "the trace line parses back to the same record", f"{rel(v31)} line {line}")
    fact("code.trace", json.dumps(json.loads(raw), indent=2, ensure_ascii=False),
         f"{rel(v31)} line {line}, verbatim, pretty-printed (json.dumps indent 2; keys and values unchanged)")
    fact("code.trace.cite", f"{Path(v31).name}, line {line}", f"{rel(v31)}")
    fact("trace.week", r["week"], f"{rel(v31)} line {line}: week")
    fact("trace.life", life_no(r["run_id"]), f"{rel(v31)} line {line}: run id")
    fact("trace.draw", f"{r['reply_draw']:.2f}", f"{rel(v31)} line {line}: reply_draw {r['reply_draw']}, rounded")
    fact("trace.rate", r["reply_rate"], f"{rel(v31)} line {line}: reply_rate")
    claim(not r["replied"] and r["reply_draw"] >= r["reply_rate"] and r["sites_seen"] == ["conscious"],
          "in the trace line, the message linked the deployed site and the dice drew no reply", f"{rel(v31)} line {line}")
    persona = ROOT / "agents" / "personas" / "self.yaml"
    text = persona.read_text().splitlines()
    born = next(l for l in text if l.startswith("born:")).split(":", 1)[1].strip()
    fact("persona.born_year", born.split(",")[0], f"{rel(persona)}: born ({born})")
    pick = []
    for key in ("name", "born", "experience", "starting_state", "horizon"):
        i = next(n for n, l in enumerate(text) if l.startswith(key + ":"))
        j = next((n for n in range(i + 1, len(text)) if text[n] and not text[n].startswith((" ", "#"))), len(text))
        pick += [l for l in text[i:j] if not l.startswith("#")]
    comment = [l for l in text if l.startswith("# Contacts are fictional")]
    claim(len(comment) == 1, "self.yaml has the fictional-contacts comment the excerpt shortens", rel(persona))
    pick += ["# Contacts are fictional people with made-up names. Their",
             "# reply_rate, voice, known_as and offers are hidden from the agent."]
    pick.append("contacts:")
    pick.append("  # … six more fictional contacts")
    i = next(n for n, l in enumerate(text) if l.strip() == "- id: marcus")
    j = next((n for n in range(i + 1, len(text)) if text[n].startswith("  - id:")), len(text))
    pick += text[i:j]
    fact("code.selfyaml", "\n".join(pick).rstrip(), f"{rel(persona)}: name, born, experience, starting_state, horizon and the marcus contact, verbatim, except the file's three-line comment about fictional contacts, shortened to two lines, and one comment line marking the skipped contacts")


# ---------------------------------------------------------------- plain-English predictions

# Each scorecard row shows a plain-English line above the committed wording, which stays verbatim.
# These lines are written here, by hand; plain_lines() refuses any number that isn't in the
# committed wording, so the plain version can't state a different threshold.
PLAIN = {
    "e01": {
        "1": "Model sampling alone, with dice and order held fixed, would produce at least half the path variety of letting both vary.",
        "2": "Which openings she applies to would depend more on the order she reads them in than on the dice.",
        "3": "Which job takes her, and when, would depend more on the dice than on the order.",
        "4": "There would be no sign that dice and order interact, on either distance.",
        "5": "In at least 90% of lives she would message her parents 10 or more times.",
        "6": "A paid pilot would happen in 20 to 60% of lives.",
        "7": "A contractor would be hired in at most 20% of lives, and launch ads bought in at most 10%.",
        "8": "At least 80% of lives would end week 52 in a full-time job.",
        "9": "No life would hit the token cap, each would cost $0.08 to $0.14, and the total would stay under $12.",
    },
    "e02": {
        "1": "With the streak hidden, training would take a smaller share of Self's actions.",
        "2": "With the streak hidden, posts and messages would take a larger share of its actions.",
    },
    "e03": {
        "1": "At least 4 of 5 lives would deploy a site.",
        "2": "At least 2 of 5 lives would make a costly bet: put savings in, go full time on the company, or turn down an offer.",
        "3": "In reflections written while nothing in the world was blocking it, Self would blame itself at most 34% of the time, against 44% in the base lives.",
        "4": "In at least 3 of the lives that deploy, a contact would reply to a link to the live site within 8 weeks of the first deploy.",
    },
}


def plain_lines():
    num = re.compile(r"\d+(?:\.\d+)?")
    for key, lines in PLAIN.items():
        preds = DATA[key]["predictions"]
        if sorted(lines) != sorted(p["n"] for p in preds):
            sys.exit(f"{key}: plain-English lines {sorted(lines)} don't match predictions {[p['n'] for p in preds]}")
        for p in preds:
            extra = set(num.findall(lines[p["n"]])) - set(num.findall(p["text"]))
            if extra:
                sys.exit(f"{key} prediction {p['n']}: plain line has numbers {sorted(extra)} not in {p['text']!r}")
            p["plain"] = lines[p["n"]]


# ---------------------------------------------------------------- write

def number_word(n: int) -> str:
    """A number that starts a sentence, spelled out and capitalized (1 to 99)."""
    ones = "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen".split()
    tens = "_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()
    if not 0 < n < 100:
        sys.exit(f"number_word: {n} is out of range")
    w = ones[n] if n < 20 else tens[n // 10] + ("-" + ones[n % 10] if n % 10 else "")
    return w[0].upper() + w[1:]


def render(page: str) -> str:
    def fill(m):
        if m.group(3) not in FACTS:
            sys.exit(f"page uses unknown fact {m.group(3)!r}")
        value = FACTS[m.group(3)]["value"]
        if 'data-form="word"' in m.group(1):
            value = number_word(int(value))
        return m.group(1) + html.escape(value, quote=False) + m.group(4)
    page = re.sub(r'(<(\w+)\b[^>]*\sdata-fact="([^"]+)"[^>]*>)[^<]*(</\2>)', fill, page)

    def link(m):
        if not FACTS.get(m.group(1), {}).get("link"):
            sys.exit(f"page links unknown fact {m.group(1)!r}")
        return re.sub(r'href="[^"]*"', f'href="{html.escape(FACTS[m.group(1)]["link"])}"', m.group(0))
    page = re.sub(r'<a\b[^>]*\sdata-link="([^"]+)"[^>]*>', link, page)

    def cite_attr(m):
        if m.group(1) not in FACTS:
            sys.exit(f"page cites unknown fact {m.group(1)!r}")
        tag = re.sub(r'\stitle="[^"]*"', "", m.group(0))
        return tag[:-1] + f' title="{html.escape(FACTS[m.group(1)]["value"])}">'
    page = re.sub(r'<\w+\b[^>]*\sdata-cite="([^"]+)"[^>]*>', cite_attr, page)
    listed = re.findall(r'data-tool="([^"]+)"', page)
    if listed and listed != DATA["tools"]["names"]:
        sys.exit(f"the page's tool table {listed} isn't the logged tool list {DATA['tools']['names']}")
    blob = json.dumps(DATA, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    page, n = re.subn(r'(<script id="site-data" type="application/json">).*?(</script>)', lambda m: m.group(1) + blob + m.group(2), page, flags=re.S)
    if n != 1:
        sys.exit("page has no <script id=\"site-data\"> block")
    used = set(re.findall(r'data-(?:fact|link|cite)="([^"]+)"', page))
    unused = sorted(set(FACTS) - used)
    if unused:
        print("note: facts not on the page:", ", ".join(unused), file=sys.stderr)
    return page


def sources_md() -> str:
    out = ["# Project page facts and their sources", "",
           "Generated by `analysis/site_facts.py`; don't edit. Every value below is written into `docs/index.html` by that script.", "",
           "## Values", "", "| id | value | source |", "|---|---|---|"]
    for fid, f in FACTS.items():
        v = f["value"] if len(f["value"]) < 90 else f["value"][:87] + "…"
        v = v.replace("|", "\\|").replace("\n", " ")
        out.append(f"| `{fid}` | {v} | {f['source']}{' · link ' + f['link'] if f['link'] else ''} |")
    out += ["", "## Claims checked in words", "", "| claim | source |", "|---|---|"]
    out += [f"| {t} | {s} |" for t, s in CLAIMS]
    out += ["", "## Charts and tables", ""]
    for k in ("e01", "e02", "e03", "tools"):
        out.append(f"- `{k}`: {DATA[k]['source']}" + (f"; predictions from {DATA[k]['predictions_source']}" if "predictions_source" in DATA[k] else ""))
    return "\n".join(out) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args(argv)
    hero_and_story()
    exp01()
    exp02()
    exp03()
    how()
    plain_lines()
    page = PAGE.read_text()
    new = render(page)
    if a.check:
        ok = new == page and SOURCES.exists() and SOURCES.read_text() == sources_md()
        print("page matches the data" if ok else "page or analysis/site_facts.md is out of date: run analysis/site_facts.py")
        sys.exit(0 if ok else 1)
    PAGE.write_text(new)
    SOURCES.write_text(sources_md())
    print(f"wrote {rel(PAGE)} ({len(FACTS)} facts, {len(CLAIMS)} claims) and {rel(SOURCES)}")


if __name__ == "__main__":
    main()
