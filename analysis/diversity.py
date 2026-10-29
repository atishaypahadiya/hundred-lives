"""How different are the lives within a condition? Experiment 01's measures.

    uv run python analysis/diversity.py a='runs/maya-exp01a-*.jsonl' b='runs/maya-exp01b-*.jsonl' \
        c='runs/maya-exp01c-*.jsonl' d='runs/maya-exp01d-*.jsonl'

Each argument is name=glob; batch baseline files are skipped. Runs that didn't reach the
horizon (token budget, API error, crash) are listed and left out, since a cut-short path looks
less diverse. Per condition, over its runs:

  1. first-applied opening: distinct count and entropy (bits)
  2. application sets: mean pairwise Jaccard distance
  3. outcome paths: mean pairwise edit distance between weekly states, divided by the horizon.
     A week's state is its commitment (none / job / part-time / accelerator) or broke, plus
     whether the company has raised money (e.g. "job+raised")
  4. end state: final cash and income (mean, sd), and the share of runs in each end bucket
  5. computer and world use: shell calls, repos, launches, messages, spend

Pairwise distances and the cash spread get a bootstrap 95% CI over runs (a run is never
paired with its own copy). Entropy and distinct counts are biased low under resampling, so
they're reported without a CI; compare them only at equal n. With (d) present, the sources split:
dice ≈ div(a) − div(d), order ≈ div(b) − div(d), interaction ≈ div(c) − div(a) − div(b) + div(d),
each with a CI from independent resamples of the conditions involved.
"""

import glob
import json
import math
import random
import statistics
import sys
from collections import Counter
from itertools import combinations
from pathlib import Path

BOOT = 2000
SEED = 0


def load(path: Path) -> dict | None:
    recs = [json.loads(l) for l in path.open() if l.strip()]
    start = next((r for r in recs if r["type"] == "run_start"), None)
    end = next((r for r in recs if r["type"] == "run_end"), None)
    if start is None:
        return None
    applied = [r["opening_id"] for r in recs if r["type"] == "referee_decision"]
    weeks = {}
    raised = False
    for r in recs:
        if r["type"] == "week_start" and r.get("broke"):
            weeks.setdefault(r["week"], {})["broke"] = True
        if r["type"] == "week_end":
            st = r["state"]
            raised = raised or (st.get("company_funding") or 0) > 0
            weeks.setdefault(r["week"], {}).update(kind=commitment(st), raised=raised)
    states = [("broke" if w.get("broke") else w.get("kind", "none")) + ("+raised" if w.get("raised") else "")
              for _, w in sorted(weeks.items())]
    final = (end or {}).get("final_state") or {}
    count = Counter(r["type"] for r in recs)
    return {
        "run_id": start["run_id"], "reason": (end or {}).get("reason", "unfinished"),
        "horizon": start.get("horizon"), "first": applied[0] if applied else None, "applied": set(applied),
        "states": states, "final_cash": final.get("cash"), "final_income": final.get("weekly_income"),
        "end_bucket": states[-1] if states else "none",
        "use": {"shell": count["shell"], "repos": count["repo_created"], "launches": count["launch"],
                "messages": count["message"], "spend": count["spend"]},
        "order_seed": start.get("order_seed", start.get("seed")), "dice_seed": start.get("dice_seed", start.get("seed")),
        "code": (start.get("code") or {}).get("git_sha"),
    }


def commitment(state: dict) -> str:
    cs = state.get("commitments") or []
    if not cs:
        return "none"
    if any((c.get("opening_id") or "").startswith("acc-") for c in cs):
        return "accelerator"
    return "job" if max(c.get("time_cost") or 0 for c in cs) >= 5 else "part-time"


def entropy(values: list) -> float:
    n = len(values)
    return -sum(c / n * math.log2(c / n) for c in Counter(values).values()) if n else 0.0


def jaccard(a: set, b: set) -> float:
    return 1 - len(a & b) / len(a | b) if a | b else 0.0


def edit(a: list, b: list) -> int:
    prev = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        cur = [i]
        for j, y in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (x != y)))
        prev = cur
    return prev[-1]


def mean_pairwise(runs: list, f) -> float:
    pairs = [(a, b) for a, b in combinations(range(len(runs)), 2)]
    return statistics.fmean(f(runs[i], runs[j]) for i, j in pairs) if pairs else float("nan")


MEASURES = {
    "first-applied entropy (bits)": lambda rs: entropy([r["first"] for r in rs]),
    "first-applied distinct": lambda rs: float(len({r["first"] for r in rs})),
    "application-set Jaccard distance": lambda rs: mean_pairwise(rs, lambda a, b: jaccard(a["applied"], b["applied"])),
    "path edit distance / horizon": lambda rs: mean_pairwise(
        rs, lambda a, b: edit(a["states"], b["states"]) / max(len(a["states"]), len(b["states"]), 1)),
    "final cash sd ($)": lambda rs: statistics.pstdev([r["final_cash"] or 0 for r in rs]) if len(rs) > 1 else 0.0,
}


def resample(rs: list, rng: random.Random) -> list:
    """A bootstrap sample of runs. Pairwise measures skip a run paired with its own copy, which
    would otherwise count as zero distance and bias diversity down."""
    idx = [rng.randrange(len(rs)) for _ in rs]
    return [dict(rs[i], _i=i) for i in idx]


def boot(rs: list, f, rng: random.Random) -> list[float]:
    out = []
    for _ in range(BOOT):
        sample = resample(rs, rng)
        uniq = list({r["_i"]: r for r in sample}.values()) if f in PAIRWISE else sample
        if len(uniq) > 1 or f not in PAIRWISE:
            out.append(f(uniq))
    return out


PAIRWISE = {MEASURES["application-set Jaccard distance"], MEASURES["path edit distance / horizon"]}
NO_CI = {MEASURES["first-applied entropy (bits)"], MEASURES["first-applied distinct"]}


def ci(xs: list[float]) -> tuple[float, float]:
    xs = sorted(x for x in xs if not math.isnan(x))
    if not xs:
        return float("nan"), float("nan")
    return xs[int(0.025 * (len(xs) - 1))], xs[int(0.975 * (len(xs) - 1))]


def main(argv: list[str]):
    if not argv:
        sys.exit(__doc__)
    rng = random.Random(SEED)
    conds = {}
    for arg in argv:
        name, _, pattern = arg.partition("=")
        paths = sorted(p for p in map(Path, glob.glob(pattern)) if not p.stem.endswith("-referee-baseline"))
        runs = [r for r in map(load, paths) if r]
        kept = [r for r in runs if r["reason"] == "horizon"]
        dropped = [r for r in runs if r["reason"] != "horizon"]
        conds[name] = kept
        codes = sorted({r["code"] or "unrecorded" for r in runs})
        print(f"\n## {name}: {len(kept)} runs kept of {len(runs)} ({pattern})")
        print(f"   code {', '.join(c[:7] for c in codes)} · order seeds {sorted({r['order_seed'] for r in kept})} "
              f"· dice seeds {sorted({r['dice_seed'] for r in kept})}")
        for r in dropped:
            print(f"   left out: {r['run_id']} ({r['reason']})")
    if not any(conds.values()):
        sys.exit("no runs")

    boots = {}
    print(f"\n{'measure':<36}" + "".join(f"{n:>24}" for n in conds))
    for m, f in MEASURES.items():
        row = f"{m:<36}"
        for n, rs in conds.items():
            if len(rs) < 2:
                row += f"{'n<2':>24}"
                continue
            if f in NO_CI:
                row += f"{f(rs):>10.3f} {'(n=' + str(len(rs)) + ')':<13}"
                continue
            b = boot(rs, f, rng)
            boots[(m, n)] = b
            lo, hi = ci(b)
            row += f"{f(rs):>10.3f} [{lo:.3f}, {hi:.3f}]"
        print(row)

    print("\nEnd state")
    for n, rs in conds.items():
        if not rs:
            continue
        cash = [r["final_cash"] or 0 for r in rs]
        buckets = Counter(r["end_bucket"] for r in rs)
        print(f"  {n}: cash mean ${statistics.fmean(cash):,.0f} sd ${statistics.pstdev(cash):,.0f} · income mean "
              f"${statistics.fmean([r['final_income'] or 0 for r in rs]):,.0f}/wk · "
              + ", ".join(f"{k} {v / len(rs):.0%}" for k, v in buckets.most_common()))

    print("\nComputer and world use (mean per run)")
    for n, rs in conds.items():
        if rs:
            print(f"  {n}: " + ", ".join(f"{k} {statistics.fmean(r['use'][k] for r in rs):.1f}" for k in rs[0]["use"]))

    if all(k in conds and len(conds[k]) > 1 for k in "abcd"):
        print("\nSources of diversity (difference of means; 95% CI from independent bootstrap draws)")
        for m in ("application-set Jaccard distance", "path edit distance / horizon"):
            f = MEASURES[m]
            pt = {k: f(conds[k]) for k in "abcd"}
            bs = {k: boots[(m, k)] for k in "abcd"}
            n = min(len(v) for v in bs.values())
            parts = {"sampling floor (d)": ([pt["d"]], [bs["d"][i] for i in range(n)]),
                     "dice (a − d)": ([pt["a"] - pt["d"]], [bs["a"][i] - bs["d"][i] for i in range(n)]),
                     "order (b − d)": ([pt["b"] - pt["d"]], [bs["b"][i] - bs["d"][i] for i in range(n)]),
                     "interaction (c − a − b + d)": ([pt["c"] - pt["a"] - pt["b"] + pt["d"]],
                                                     [bs["c"][i] - bs["a"][i] - bs["b"][i] + bs["d"][i] for i in range(n)])}
            print(f"  {m}")
            for label, (p, b) in parts.items():
                lo, hi = ci(b)
                print(f"    {label:<30} {p[0]:>8.3f}  [{lo:.3f}, {hi:.3f}]")
        print("  A CI that includes 0 means not distinguishable from no effect at this n, not 'no effect'.")


if __name__ == "__main__":
    main(sys.argv[1:])
