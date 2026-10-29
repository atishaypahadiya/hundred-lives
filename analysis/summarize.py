"""Summarize run logs.

    uv run python analysis/summarize.py runs/maya-*.jsonl
"""

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path


def load(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.open() if line.strip()]


def summarize(path: Path) -> dict:
    recs = load(path)
    by = lambda t: [r for r in recs if r["type"] == t]
    start, end = by("run_start")[0], (by("run_end") or [{}])[-1]
    calls = by("llm_call")
    tokens, latency = Counter(), {}
    for c in calls:
        tokens[c["role"]] += c["usage"]["total"]
        latency.setdefault(c["role"], []).append(c["latency_s"])
    tools = by("tool_call")
    decisions = by("referee_decision")
    return {
        "run_id": start["run_id"],
        "models": start["models"],
        "end": end.get("reason"),
        "weeks": end.get("weeks_completed"),
        "tokens": end.get("tokens_used"),
        "tokens_by_role": dict(tokens),
        "llm_calls": len(calls),
        "latency_by_role": {r: round(sum(v) / len(v), 2) for r, v in latency.items()},
        "tool_calls": Counter(t["tool"] for t in tools),
        "tool_errors": by("tool_error"),
        "errors_carried": sum(len(r["errors"]) for r in by("tool_errors_carried")),
        "week_end_reasons": Counter(r["reason"] for r in by("week_end")),
        "nudges": len(by("nudge")),
        "decisions": decisions,
        "outcomes_applied": by("outcome_applied"),
        "broke_weeks": [r["week"] for r in by("broke")],
        "gigs": [(r["week"], r["forced"]) for r in by("gig")],
        "shortfall_weeks": [r["week"] for r in by("week_end") if r.get("shortfall")],
        "summaries": by("memory_summary"),
        "final_state": end.get("final_state", {}),
        "seed": start.get("seed"),
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
    return (f"{c['n']} apps · mean base rate {c['mean_base']:.3f} · mean estimated {c['mean_est']:.3f} "
            f"({c['est_over_base']:.1f}× base) · realized {c['realized_rate']:.3f} "
            f"({c['successes']} successes vs {c['expected_successes']:.2f} expected) · "
            f"{c['failures']} failures, {c['ignored']} ignored")


def main(paths: list[str]):
    runs = [summarize(Path(p)) for p in paths]
    for s in runs:
        fs = s["final_state"]
        print(f"=== {s['run_id']}  ({s['models']['agent']})")
        print(f"end: {s['end']} after {s['weeks']} weeks · {s['tokens']:,} tokens "
              f"{s['tokens_by_role']} · {s['llm_calls']} LLM calls · mean latency {s['latency_by_role']}")
        print(f"tools: {dict(s['tool_calls'])} · nudges {s['nudges']} · "
              f"weeks ended by {dict(s['week_end_reasons'])}")
        print(f"money: broke in weeks {s['broke_weeks'] or 'none'} · gigs "
              f"{[f'w{w}' + ('*' if f else '') for w, f in s['gigs']] or 'none'} (* forced) · "
              f"shortfall weeks {s['shortfall_weeks'] or 'none'}")
        print(f"applications (seed {s['seed']}):")
        for d in s["decisions"]:
            print(f"  w{d['week']:<3} {d['opening_id']:<9} base {d['base_rate']:<6} "
                  f"est {d['estimated_chance']:.3f}  draw {d['success_draw']:.3f}  "
                  f"{d['outcome']:<8} reveal w{d['reveal_week']:<3}"
                  + (f" PARSE ERROR {d['parse_error']}" if d.get("parse_error") else ""))
        print(f"calibration: {fmt_calibration(calibration(s['decisions']))}")
        delivered = [f"w{o['week']} {o['opening_id']} {o['outcome']}" for o in s["outcomes_applied"]]
        print(f"outcomes delivered: {delivered or 'none'}")
        print(f"tool errors ({len(s['tool_errors'])}, {s['errors_carried']} carried to next week):")
        for e in s["tool_errors"]:
            print(f"  w{e['week']:<3} {e['tool']:<6} {json.dumps(e['input'])[:90]}  → {e['error'][:110]}")
        for m in s["summaries"]:
            g = m["grounding"]
            print(f"memory summary w{m['week']}: cited {g['cited']} · "
                  + ("FLAGGED: " + "; ".join(g["reasons"]) if g["flagged"] else "grounded"))
        print(f"final: cash ${fs.get('cash', 0):,.0f} · income ${fs.get('weekly_income', 0):,.0f}/wk · "
              f"company ${fs.get('company_funding', 0):,.0f} · "
              f"commitments {[c['label'] for c in fs.get('commitments', [])]} · "
              f"pending {[p['opening_id'] for p in fs.get('pending', [])]}")
        if s["summaries"]:
            print("last memory summary:\n  " + s["summaries"][-1]["summary"].replace("\n", "\n  "))
        print()

    pooled = [d for s in runs for d in s["decisions"]]
    print(f"=== calibration, all {len(runs)} runs")
    print(f"overall: {fmt_calibration(calibration(pooled))}")
    by_type = defaultdict(list)
    for d in pooled:
        by_type[d["opening_type"]].append(d)
    for t, ds in sorted(by_type.items()):
        print(f"  {t:<12} {fmt_calibration(calibration(ds))}")


if __name__ == "__main__":
    main(sys.argv[1:] or sorted(str(p) for p in Path("runs").glob("*.jsonl")))
