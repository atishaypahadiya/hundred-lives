"""Experiment 03: Self on world v3 vs part A base. Per-life measures and the three pre-registered
predictions (experiments/03-world-v3.md). Reads logs only; with --code, labels uncoded
reflections with the self-blame coder (Haiku, costs money; cached in analysis/labels/self_blame/).

    uv run python analysis/exp03.py 'runs/self-v3-*.jsonl' \\
        --base '../hundred-lives-viewer/runs/self-base-*.jsonl' [--code]

Lives pair by run number (run i of each batch uses dice and order seed i).
"""

import argparse
import glob
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import analysis.self_blame as sb  # noqa: E402

COSTLY = ("invest_personal", "go_fulltime", "offer_declined")
# World v3 limits that block a capability the self-blame coder names (its goal_needs). The others
# have a way around them in v3 (private_link, external_hosting: deploy; company_funds:
# invest_personal) or no matching capability (internet, visa_status, work_authorization).
V3_LIMITS = {"server_hosting": "public_access", "outside_contacts": "outside_contacts"}


def life_v3(path: str) -> dict | None:
    """v3 measures from one log; None unless the life reached the horizon."""
    L = {"run_id": None, "deploys": [], "testers": set(), "contacts_saw_site": Counter(), "linked": Counter(),
         "costly": Counter(), "blocked": Counter(), "blocked_first": {}, "reflections": [], "ended": None,
         "pilot": None, "fulltime_week": None, "invested": 0.0, "declined": [], "missing_links": 0,
         "tester_weeks": []}
    for r in sb.records(path):
        t, w = r.get("type"), r.get("week")
        L["run_id"] = L["run_id"] or r.get("run_id")
        if t == "deploy":
            L["deploys"].append((w, r["site"], r["version"]))
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
        elif t == "memory_reflection":
            L["reflections"].append({"week": w, "text": r.get("reflection") or ""})
        elif t == "run_end":
            L["ended"] = r.get("reason")
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
    prompt = sb.coder_prompt()
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
              + (", ".join(f"{k} {v}" for k, v in L["blocked"].most_common()) or "–") + " |")
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


if __name__ == "__main__":
    main()
