"""Summarize run logs: per-run paths and metrics, then cross-run comparisons.

    uv run python analysis/summarize.py runs/maya-*.jsonl
"""

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean


def load(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.open() if line.strip()]


def ranges(weeks: list[int]) -> str:
    """[2,3,4,7] -> 'w2–4, w7'"""
    out, start, prev = [], None, None
    for w in sorted(weeks) + [None]:
        if start is None:
            start = prev = w
        elif w is not None and w == prev + 1:
            prev = w
        else:
            out.append(f"w{start}" + (f"–{prev}" if prev != start else ""))
            start = prev = w
    return ", ".join(out)


def path_summary(recs: list[dict]) -> str:
    """Milestones by week from structured events, with gig and broke weeks as ranges."""
    by_week = defaultdict(list)
    for r in recs:
        t, w = r["type"], r["week"]
        if t == "referee_decision":
            by_week[w].append(f"apply {r['opening_id']}")
        elif t == "outcome_applied":
            by_week[w].append(("✓" if r["outcome"] == "success" else "✗") + r["opening_id"])
        elif t == "commitment_started":
            by_week[w].append(f"start {r['commitment']['opening_id']}")
        elif t == "commitment_ended":
            by_week[w].append(f"{'quit' if r['reason'] == 'left' else 'end'} {r['commitment']['opening_id']}")
        elif t == "offer_held":
            by_week[w].append(f"hold {r['opening_id']}")
        elif t == "offer_lapsed":
            by_week[w].append(f"lapse {r['opening_id']}")
    parts = []
    for w in sorted(by_week):
        events = by_week[w]
        applies = [e.split()[1] for e in events if e.startswith("apply ")]
        rest = sorted((e for e in events if not e.startswith("apply ")),
                      key=lambda e: e[0] not in "✓✗")  # outcome before what it started
        text = (f"apply {','.join(applies)}" if applies else "") + (" " if applies and rest else "") + " ".join(rest)
        parts.append(f"w{w} {text}")
    gigs = [r["week"] for r in recs if r["type"] == "gig"]
    broke = [r["week"] for r in recs if r["type"] == "broke"]
    tail = []
    if gigs:
        tail.append(f"gigs {ranges(gigs)} ({len(gigs)})")
    if broke:
        tail.append(f"broke {ranges(broke)}")
    return " · ".join(parts) + ("\n      " + " · ".join(tail) if tail else "")


def summarize(path: Path) -> dict:
    recs = load(path)
    by = lambda t: [r for r in recs if r["type"] == t]
    start, end = by("run_start")[0], (by("run_end") or [{}])[-1]
    order = start["opening_order"]
    calls = by("llm_call")
    tokens = Counter()
    for c in calls:
        tokens[c["role"]] += c["usage"]["total"]
    applies = [t for t in by("tool_call") if t["tool"] == "apply" and t["input"].get("opening_id") in order]
    first = applies[0]["input"]["opening_id"] if applies else None
    first_real = (by("referee_decision") or [{}])[0].get("opening_id")
    starts = by("commitment_started")
    code = start.get("code")
    return {
        "run_id": start["run_id"], "seed": start["seed"], "order": order,
        "code": f"{code['git_sha'][:7]}{'+dirty' if code['dirty'] else ''}" if code else None,
        "pilot": not code or code["dirty"],
        "end": end.get("reason"), "weeks": end.get("weeks_completed"), "tokens": end.get("tokens_used"),
        "tokens_by_role": dict(tokens),
        "valid": end.get("valid_actions"), "invalid": end.get("invalid_actions"),
        "valid_rate": end.get("valid_action_rate"),
        "week_end_reasons": Counter(r["reason"] for r in by("week_end")),
        "first": first, "first_pos": order.index(first) + 1 if first else None,
        "first_real": first_real,
        "job02_pos": order.index("job-02") + 1 if "job-02" in order else None,
        "decisions": by("referee_decision"),
        "first_start_week": starts[0]["week"] if starts else None,
        "path": path_summary(recs),
        "errors": Counter(e["error"].split(";")[0][:60] for e in by("tool_error")),
        "reflection": end.get("reflection", ""),
        "final_state": end.get("final_state", {}),
    }


def calibration(decisions: list[dict]) -> dict:
    """Referee estimates vs base rates (bias) and vs the drawn outcomes (dice check)."""
    n = len(decisions)
    if not n:
        return {"n": 0}
    est = [d["estimated_chance"] for d in decisions]
    base = [d["base_rate"] for d in decisions]
    wins = sum(d["outcome"] == "success" for d in decisions)
    return {"n": n, "mean_base": sum(base) / n, "mean_est": sum(est) / n,
            "est_over_base": sum(est) / sum(base), "expected_successes": sum(est),
            "successes": wins, "realized_rate": wins / n,
            "ignored": sum(d["outcome"] == "ignored" for d in decisions),
            "failures": sum(d["outcome"] == "failure" for d in decisions)}


def fmt_calibration(c: dict) -> str:
    if not c["n"]:
        return "no applications"
    return (f"{c['n']:>3} apps · base {c['mean_base']:.3f} · est {c['mean_est']:.3f} "
            f"({c['est_over_base']:.1f}× base) · realized {c['realized_rate']:.3f} "
            f"({c['successes']} vs {c['expected_successes']:.1f} expected) · "
            f"{c['failures']} no, {c['ignored']} ignored")


def main(paths: list[str]):
    runs = [summarize(Path(p)) for p in paths]
    for s in runs:
        fs = s["final_state"]
        jobs = [c["opening_id"] for c in fs.get("commitments", [])]
        label = "PILOT (code state not reproducible) · " if s["pilot"] else ""
        print(f"=== {s['run_id']} (seed {s['seed']}) · {label}code {s['code'] or 'not recorded'} · "
              f"{s['end']} after {s['weeks']} weeks · {s['tokens']:,} tokens")
        print(f"  first apply: {s['first']} (position {s['first_pos']}/30; job-02 at {s['job02_pos']}) · "
              f"valid-action rate {s['valid_rate']} ({s['valid']} valid, {s['invalid']} invalid) · "
              f"weeks ended by {dict(s['week_end_reasons'])}")
        print(f"  path: {s['path']}")
        print(f"  end: cash ${fs.get('cash', 0):,.0f} · income ${fs.get('weekly_income', 0):,.0f}/wk · "
              f"commitments {jobs or 'none'} · company ${fs.get('company_funding', 0):,.0f}")
        print(f"  calibration: {fmt_calibration(calibration(s['decisions']))}")
        if s["errors"]:
            print(f"  top errors: {dict(s['errors'].most_common(3))}")
        print()

    n = len(runs)
    pilots = sum(s["pilot"] for s in runs)
    print(f"=== across {n} runs" + (f" · PILOT: {pilots} of them ran on uncommitted or unrecorded code, "
                                    "so these numbers can't be reproduced exactly" if pilots else ""))
    firsts = Counter(s["first"] for s in runs)
    print("first-applied opening: " + ", ".join(f"{k} ×{v}" for k, v in firsts.most_common()))
    print("  (opening, its position) per run: "
          + ", ".join(f"{s['first']}@{s['first_pos']}" for s in runs))
    print(f"  job-02 position per run: {[s['job02_pos'] for s in runs]}")
    pos = [s["first_pos"] for s in runs if s["first_pos"]]
    if pos:
        print(f"  first-applied position: mean {mean(pos):.1f} (uniform would be 15.5), "
              f"in top 3 in {sum(p <= 3 for p in pos)}/{len(pos)} runs (uniform: {len(pos) * 3 / 30:.1f})")
    rates = [s["valid_rate"] for s in runs if s["valid_rate"] is not None]
    print(f"valid-action rate: mean {mean(rates):.3f} · per run {rates}")
    starts = [s["first_start_week"] for s in runs]
    employed = sum(bool(s["final_state"].get("commitments")) for s in runs)
    print(f"first commitment started at week: {starts} · in a commitment at the end: {employed}/{n}")
    print(f"tokens per run: mean {mean(s['tokens'] for s in runs):,.0f}")
    pooled = [d for s in runs for d in s["decisions"]]
    print(f"calibration overall: {fmt_calibration(calibration(pooled))}")
    by_type = defaultdict(list)
    for d in pooled:
        by_type[d["opening_type"]].append(d)
    for t, ds in sorted(by_type.items()):
        print(f"  {t:<12} {fmt_calibration(calibration(ds))}")
    by_opening = defaultdict(list)
    for d in pooled:
        by_opening[d["opening_id"]].append(d["estimated_chance"] / d["base_rate"])
    print("  est/base by opening (n): " + ", ".join(
        f"{k} {mean(v):.1f}× ({len(v)})" for k, v in sorted(by_opening.items(), key=lambda kv: -len(kv[1]))))


if __name__ == "__main__":
    main(sys.argv[1:] or sorted(str(p) for p in Path("runs").glob("*.jsonl")))
