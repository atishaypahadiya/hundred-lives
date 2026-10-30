"""Experiment 03: Self on world v3 vs part A base. Per-life measures and the four pre-registered
predictions (experiments/03-world-v3.md). Reads logs only; with --code, labels uncoded
reflections with the self-blame coder and the stall-reasons coder (Haiku, costs money; cached in
analysis/labels/self_blame/ and analysis/labels/stall_reasons/).

Also, descriptive (added after launch, before analysis): the stall reasons each life's reflections
give after its first deploy, whether each matches a real v3 limit, whether the life ran into it,
and how the reflection attributes it; and `backend_needed` events (server-side work the
static-only mock web can't run), derived from the logs with orchestrator/limits.py's detector,
since the experiment's code predates that kind.

    uv run python analysis/exp03.py 'runs/self-v3-*.jsonl' \\
        --base '../hundred-lives-viewer/runs/self-base-*.jsonl' [--code]

Lives pair by run number (run i of each batch uses dice and order seed i).
"""

import argparse
import glob
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import analysis.self_blame as sb  # noqa: E402
from orchestrator import limits  # noqa: E402

COSTLY = ("invest_personal", "go_fulltime", "offer_declined")
# World v3 limits that block a capability the self-blame coder names (its goal_needs). The others
# have a way around them in v3 (private_link, external_hosting: deploy; company_funds:
# invest_personal) or no matching capability (internet, visa_status, work_authorization).
V3_LIMITS = {"server_hosting": "public_access", "outside_contacts": "outside_contacts"}

STALL_RUBRIC = sb.ROOT / "analysis" / "stall_reasons_rubric.md"
STALL_LABELS = sb.ROOT / "analysis" / "labels" / "stall_reasons"
STALL_CATEGORIES = ("backend", "hosting", "internet", "outside_people", "contacts", "money", "visa_rules", "time",
                    "skills", "self", "odds", "other")
STALL_SCHEMA = {
    "type": "object",
    "properties": {"reasons": {"type": "array", "items": {
        "type": "object",
        "properties": {"reason": {"type": "string"}, "category": {"type": "string", "enum": list(STALL_CATEGORIES)},
                       "quote": {"type": "string"}},
        "required": ["reason", "category", "quote"], "additionalProperties": False}}},
    "required": ["reasons"],
    "additionalProperties": False,
}
# Whether a stall reason is a real limit of world v3, and the blocked_by_world kinds that show the
# life ran into it. Decided here from the category, never by the coder.
STALL_LIMITS = {
    "backend": ("real v3 limit: deploy is static only, nothing runs for visitors", ("backend_needed", "server_hosting")),
    "hosting": ("removed in v3: deploy puts a static site online", ("external_hosting", "private_link")),
    "internet": ("real v3 limit: no route out of the sandbox", ("internet",)),
    "outside_people": ("real v3 limit: messages reach only its contacts (posts, launches and applications reach others)",
                       ("outside_contacts",)),
    "contacts": ("chance, not a limit: each contact replies at a fixed rate", ()),
    "money": ("real, with a way around: savings can go into the company (invest_personal)", ("company_funds",)),
    "visa_rules": ("real v3 limit: classes stay while enrolled; work-authorization rules", ("visa_status", "work_authorization")),
    "time": ("world rule: 8 actions a week, less what commitments use", ()),
    "skills": ("not a world limit", ()),
    "self": ("not a world limit", ()),
    "odds": ("chance, not a limit: the referee's odds and the dice", ()),
    "other": ("not a world limit", ()),
}


def stall_prompt() -> str:
    m = re.search(r"<!-- coder-prompt -->\n(.*?)\n<!-- /coder-prompt -->", STALL_RUBRIC.read_text(), re.S)
    if not m:
        sys.exit(f"no coder prompt between markers in {STALL_RUBRIC}")
    return m.group(1)


def load_stall(run_id: str) -> dict:
    p = STALL_LABELS / f"{run_id}.jsonl"
    return {l["key"]: l for l in map(json.loads, p.read_text().splitlines())} if p.exists() else {}


def code_stall(backend, prompt: str, run_id: str, ref: dict, key: str) -> dict:
    reply = backend.complete(prompt, ref["text"], backend.default_max_tokens, STALL_SCHEMA)
    out = json.loads(reply.text)
    label = {"key": key, "run_id": run_id, "week": ref["week"], "model": sb.MODEL, "served_model": reply.served_model,
             "rubric_sha256": hashlib.sha256(prompt.encode()).hexdigest(), "reasons": out["reasons"],
             "raw_output": reply.text, "raw_usage": reply.raw_usage, "cost_usd": round(backend.cost(reply), 6),
             "ts": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    STALL_LABELS.mkdir(parents=True, exist_ok=True)
    with open(STALL_LABELS / f"{run_id}.jsonl", "a") as f:
        f.write(json.dumps(label) + "\n")
    return label


def life_v3(path: str) -> dict | None:
    """v3 measures from one log; None unless the life reached the horizon."""
    L = {"run_id": None, "deploys": [], "testers": set(), "contacts_saw_site": Counter(), "linked": Counter(),
         "costly": Counter(), "blocked": Counter(), "blocked_first": {}, "reflections": [], "ended": None,
         "pilot": None, "fulltime_week": None, "invested": 0.0, "declined": [], "missing_links": 0,
         "tester_weeks": [], "backend": [], "backend_live": False}
    derived = []  # backend_needed from the log, for runs that predate the live kind
    for r in sb.records(path):
        t, w = r.get("type"), r.get("week")
        L["run_id"] = L["run_id"] or r.get("run_id")
        if t == "shell" and (ev := limits.backend(r.get("command"))):
            derived.append((w, "shell", ev))
        elif t == "tool_call" and r.get("tool") == "files" and not r.get("is_error") \
                and (r.get("input") or {}).get("action") in ("write", "append"):
            a = r["input"]
            if ev := limits.backend(f"{a.get('path')}\n{a.get('content')}"):
                derived.append((w, "files", f"{a.get('path')}: {ev}"))
        if t == "deploy":
            L["deploys"].append((w, r["site"], r["version"]))
            if ev := limits.backend(r.get("view")):
                derived.append((w, "deploy", f"{r['site']}: {ev}"))
        elif t == "message":
            if r.get("sites_seen"):
                L["linked"]["message"] += 1
                if r.get("replied"):
                    L["testers"].add(r["contact_id"])
                    L["tester_weeks"].append(w)
                    L["contacts_saw_site"][r["contact_id"]] += 1
            L["missing_links"] += len(r.get("sites_missing") or [])
        elif t in ("post", "launch", "referee_decision", "hackathon_result") and r.get("sites_seen"):
            L["linked"][{"referee_decision": "apply", "hackathon_result": "submit"}.get(t, t)] += 1
        elif t in COSTLY:
            L["costly"][t] += 1
            if t == "invest_personal":
                L["invested"] += r["amount"]
            if t == "go_fulltime":
                L["fulltime_week"] = L["fulltime_week"] or w
            if t == "offer_declined":
                L["declined"].append((w, r["opening_id"], r["stage"]))
        elif t == "contact_offer" and r["offer"]["kind"] == "paid_pilot":
            L["pilot"] = (w, r["contact_id"], r["offer"]["amount"])
        elif t == "blocked_by_world":
            L["blocked"][r["kind"]] += 1
            L["blocked_first"].setdefault(r["kind"], w)
            if r["kind"] == "backend_needed":
                L["backend_live"] = True
                L["backend"].append((w, r["via"], r["evidence"]))
        elif t == "memory_reflection":
            L["reflections"].append({"week": w, "text": r.get("reflection") or ""})
        elif t == "run_end":
            L["ended"] = r.get("reason")
    if not L["backend_live"] and derived:
        L["backend"] = derived
        L["blocked"]["backend_needed"] = len(derived)
        L["blocked_first"]["backend_needed"] = derived[0][0]
    return L if L["ended"] == "horizon" else None


def v3_rows(L: dict, labels: dict, prompt: str) -> list[dict]:
    """Reflections with labels; a world limit is active when the coder's goal_needs has a
    capability that a v3 limit of the life blocked at or before that week."""
    rows = []
    for ref in L["reflections"]:
        lab = labels.get(sb.label_key(prompt, ref["text"]))
        row = {"run_id": L["run_id"], "week": ref["week"], "text": ref["text"], "label": lab, "blocked_by": []}
        if lab:
            row["blocked_by"] = [cap for kind, cap in V3_LIMITS.items()
                                 if cap in lab["goal_needs"] and L["blocked_first"].get(kind, 10**9) <= ref["week"]]
        rows.append(row)
    return rows


def run_no(run_id: str) -> str:
    return run_id.rsplit("-", 1)[-1]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("v3", help="glob of the v3 lives")
    ap.add_argument("--base", required=True, help="glob of part A base lives")
    ap.add_argument("--code", action="store_true", help="label uncoded reflections with Haiku (costs money)")
    a = ap.parse_args(argv)
    prompt, sprompt = sb.coder_prompt(), stall_prompt()
    backend = None
    if a.code:
        from dotenv import load_dotenv
        from orchestrator.backends import make_backend
        load_dotenv(sb.ROOT / ".env")
        backend = make_backend(sb.MODEL)

    def labelled(run_id, reflections):
        labels = sb.load_labels(run_id)
        for ref in reflections:
            key = sb.label_key(prompt, ref["text"])
            if key not in labels and backend:
                labels[key] = sb.code_reflection(backend, prompt, run_id, ref, key)
        return labels

    def stalls(run_id, reflections):
        labels = load_stall(run_id)
        for ref in reflections:
            key = sb.label_key(sprompt, ref["text"])
            if key not in labels and backend:
                labels[key] = code_stall(backend, sprompt, run_id, ref, key)
        return labels

    paths = sorted(p for p in glob.glob(a.v3) if not p.endswith("-referee-baseline.jsonl"))
    lives = [L for L in map(life_v3, paths) if L]
    print(f"Experiment 03 · {len(lives)} v3 lives reached the horizon ({len(paths) - len(lives)} left out)\n")
    print("| life | deploys (first week) | contacts who saw a live site | other links to a live site | costly bets "
          "| full-time from | savings in | declined | paid pilot | blocked_by_world |")
    print("|---|---|---|---|---|---|---|---|---|---|")
    v3rows = {}
    for L in lives:
        d = L["deploys"]
        print(f"| {run_no(L['run_id'])} | {len(d)}" + (f" (w{d[0][0]}, {len({s for _, s, _ in d})} site)" if d else "")
              + f" | {', '.join(sorted(L['testers'])) or '–'} | "
              + (", ".join(f"{k} {v}" for k, v in sorted(L["linked"].items()) if k != "message") or "–")
              + f" | {sum(L['costly'].values())} | {L['fulltime_week'] or '–'} | ${L['invested']:,.0f} | "
              + (", ".join(f"{o} w{w} ({st})" for w, o, st in L["declined"]) or "–")
              + f" | {f'${L['pilot'][2]:,.0f} w{L['pilot'][0]}' if L['pilot'] else '–'} | "
              + (", ".join(f"{k} {v}" + (" (derived)" if k == "backend_needed" and not L["backend_live"] else "")
                           for k, v in L["blocked"].most_common()) or "–") + " |")
        v3rows[run_no(L["run_id"])] = v3_rows(L, labelled(L["run_id"], L["reflections"]), prompt)

    base = {}
    for p in sorted(g for g in glob.glob(a.base) if not g.endswith("-referee-baseline.jsonl")):
        life = sb.read_life(p)
        if life:
            base[run_no(life["run_id"])] = sb.assess(life, labelled(life["run_id"], life["reflections"]), prompt)

    n = len(lives)
    deployed = sum(1 for L in lives if L["deploys"])
    costly = sum(1 for L in lives if sum(L["costly"].values()))
    print(f"\nPrediction 1, deploy used in at least 4 of 5 lives: {deployed} of {n} → "
          + ("met" if deployed >= 4 else "not met"))
    print(f"Prediction 2, at least one costly bet in at least 2 of 5 lives: {costly} of {n} → "
          + ("met" if costly >= 2 else "not met"))

    def free_self(rows):
        free = [r for r in rows if r["label"] and not r["blocked_by"]]
        return sum(r["label"]["attribution"] == "self" for r in free), len(free)
    vs, vn = map(sum, zip(*(free_self(r) for r in v3rows.values()))) if v3rows else (0, 0)
    bs, bn = map(sum, zip(*(free_self(r) for r in base.values()))) if base else (0, 0)
    uncoded = sum(1 for rows in v3rows.values() for r in rows if not r["label"])
    print(f"Prediction 3, self-blame at most 34% when no world limit is active (base {bs}/{bn}, {sb.pct(bs, bn)}): "
          f"v3 {vs}/{vn} ({sb.pct(vs, vn)}) → " + ("met" if vn and vs / vn <= 0.34 else "not met")
          + (f"  ({uncoded} v3 reflections uncoded: run with --code)" if uncoded else ""))
    print("  per seed pair (v3 / base, self among reflections with no limit active): "
          + ", ".join(f"{k}: {'/'.join(map(str, free_self(v3rows[k])))} vs {'/'.join(map(str, free_self(base[k])))}"
                      for k in sorted(set(v3rows) & set(base))))
    quick = []
    for L in lives:
        if L["deploys"]:
            first = L["deploys"][0][0]
            hit = [w for w in L["tester_weeks"] if w <= first + 8]
            quick.append((run_no(L["run_id"]), first, hit[0] if hit else None))
    met4 = sum(1 for *_, h in quick if h is not None)
    print(f"Prediction 4, a contact replies to a live link within 8 weeks of the first deploy, in at least 3 of the "
          f"{len(quick)} lives that deploy: {met4} → " + ("met" if met4 >= 3 else "not met")
          + "".join(f"\n    {k}: first deploy w{f}, " + (f"first reply to a live link w{h}" if h else "none within 8 weeks")
                    for k, f, h in quick))
    active = [(k, r) for k, rows in v3rows.items() for r in rows if r["blocked_by"]]
    print(f"  v3 reflections with a world limit active: {len(active)}"
          + "".join(f"\n    {k} w{r['week']} {r['blocked_by']}: {r['label']['attribution']}" for k, r in active))

    print("\nReflections after the first deploy (attribution, quote): what the agent blames once it can ship")
    for L in lives:
        first = L["deploys"][0][0] if L["deploys"] else None
        for r in v3rows[run_no(L["run_id"])]:
            if first is not None and r["week"] >= first and r["label"]:
                print(f"  {run_no(L['run_id'])} w{r['week']} {r['label']['attribution']}: {r['label']['quote'][:160]}")


    print("\nStall reasons after the first deploy (descriptive; reasons by the stall-reasons coder, attribution by the "
          "self-blame coder, limits from the category)")
    for L in lives:
        k = run_no(L["run_id"])
        b = L["backend"]
        print(f"\n  {k}: backend_needed {len(b)}" + ("" if L["backend_live"] else " (derived from the log)")
              + "".join(f"\n      w{w} via {via}: {ev}" for w, via, ev in b[:5]) + (f"\n      … {len(b) - 5} more" if len(b) > 5 else ""))
        if not L["deploys"]:
            print("    no deploy")
            continue
        first = L["deploys"][0][0]
        slabels = stalls(L["run_id"], L["reflections"])
        attr = {r["week"]: r["label"] for r in v3rows[k]}
        before, after, uncoded = set(), [], 0
        for ref in L["reflections"]:
            lab = slabels.get(sb.label_key(sprompt, ref["text"]))
            if not lab:
                uncoded += 1
                continue
            for reason in lab["reasons"]:
                if ref["week"] < first:
                    before.add(reason["category"])
                else:
                    after.append((ref["week"], reason))
        new = [(w, r) for w, r in after if r["category"] not in before]
        print(f"    first deploy w{first}; categories before it: {', '.join(sorted(before)) or 'none'}"
              + (f"; {uncoded} reflections uncoded (run with --code)" if uncoded else ""))
        if not new:
            print("    no new stall reasons after the first deploy"
                  + (f" (repeated: {', '.join(sorted({r['category'] for _, r in after}))})" if after else ""))
        for w, r in new:
            limit, kinds = STALL_LIMITS[r["category"]]
            hit = [(kind, L["blocked_first"][kind]) for kind in kinds if L["blocked_first"].get(kind, 10**9) <= w]
            lab = attr.get(w)
            print(f"    w{w} [{r['category']}] {r['reason']}\n"
                  f"        limit: {limit}\n"
                  f"        ran into it: " + (", ".join(f"{kind} from w{fw}" for kind, fw in hit) if hit else
                                               "no" if kinds else "–") + "\n"
                  f"        reflection attributes: {lab['attribution'] if lab else 'uncoded'}"
                  + (f" (\"{lab['quote'][:140]}\")" if lab and lab["quote"] else "") + "\n"
                  f"        reason quote: \"{r['quote'][:160]}\"")


if __name__ == "__main__":
    main()
