"""Path types and the Vendi score per condition. EXPLORATORY: not in experiment 01's pre-registration.

    uv run python analysis/vendi.py a='runs/maya-exp01a-*.jsonl' b=... c=... d=...

Uses analysis/diversity.py's path (one state per week: commitment or broke, plus whether the company has
raised money) and its run filter (horizon only), so the paths are the pre-registered ones; only these
two summaries are new.

- Path type: the weekly states with consecutive repeats collapsed ("none → job"). A run's path type
  ignores when changes happen, only their order. Reported as distinct count plus the most common types.
- Vendi score (Friedman & Dieng 2023): the effective number of distinct paths, exp of the entropy of the
  eigenvalues of K/n, with K[i][j] = 1 − edit distance / horizon (diversity.py's distance). It runs from 1
  (all paths identical) to n. The edit-distance kernel isn't guaranteed positive semidefinite; negative
  eigenvalues are clipped to 0 and their total is reported. 95% CI by jackknife (leave one run out),
  since bootstrap resamples repeat runs, which reads as less diversity. Compare only at equal n.
"""

import glob
import importlib.util
import math
import statistics
import sys
from collections import Counter
from pathlib import Path

spec = importlib.util.spec_from_file_location("diversity", Path(__file__).with_name("diversity.py"))
diversity = importlib.util.module_from_spec(spec)
spec.loader.exec_module(diversity)


def eigenvalues(a: list[list[float]], sweeps: int = 100) -> list[float]:
    """Symmetric eigenvalues by cyclic Jacobi rotations (n is at most a few dozen)."""
    n = len(a)
    a = [row[:] for row in a]
    for _ in range(sweeps):
        off = sum(a[i][j] ** 2 for i in range(n) for j in range(n) if i != j)
        if off < 1e-18:
            break
        for p in range(n):
            for q in range(p + 1, n):
                if abs(a[p][q]) < 1e-15:
                    continue
                theta = (a[q][q] - a[p][p]) / (2 * a[p][q])
                t = (1 if theta >= 0 else -1) / (abs(theta) + math.sqrt(theta * theta + 1))
                c = 1 / math.sqrt(t * t + 1)
                s = t * c
                for k in range(n):
                    akp, akq = a[k][p], a[k][q]
                    a[k][p], a[k][q] = c * akp - s * akq, s * akp + c * akq
                for k in range(n):
                    apk, aqk = a[p][k], a[q][k]
                    a[p][k], a[q][k] = c * apk - s * aqk, s * apk + c * aqk
    return [a[i][i] for i in range(n)]


def vendi(paths: list[list[str]]) -> tuple[float, float]:
    """(Vendi score, total clipped negative eigenvalue mass)."""
    n = len(paths)
    k = [[1 - diversity.edit(a, b) / max(len(a), len(b), 1) for b in paths] for a in paths]
    lam = [x / n for x in eigenvalues(k)]
    neg = -sum(x for x in lam if x < 0)
    pos = [x for x in lam if x > 1e-12]
    total = sum(pos)
    return math.exp(-sum(x / total * math.log(x / total) for x in pos)), neg


def jackknife(paths: list[list[str]]) -> tuple[float, float]:
    n = len(paths)
    loo = [vendi(paths[:i] + paths[i + 1:])[0] for i in range(n)]
    mean = statistics.fmean(loo)
    se = math.sqrt((n - 1) / n * sum((x - mean) ** 2 for x in loo))
    return se, mean


def collapse(states: list[str]) -> str:
    out = [s for i, s in enumerate(states) if i == 0 or s != states[i - 1]]
    return " → ".join(out)


def main(args: list[str]):
    for arg in args:
        name, pattern = arg.split("=", 1)
        runs = [r for p in sorted(glob.glob(pattern)) if not p.endswith("-referee-baseline.jsonl")
                for r in [diversity.load(Path(p))] if r]
        done = [r for r in runs if r["reason"] == "horizon"]
        paths = [r["states"] for r in done]
        types = Counter(collapse(p) for p in paths)
        v, neg = vendi(paths) if len(paths) > 1 else (float("nan"), 0.0)
        se, _ = jackknife(paths) if len(paths) > 2 else (float("nan"), 0)
        print(f"{name}: n={len(done)} of {len(runs)}  path types {len(types)}  "
              f"Vendi {v:.2f} [{v - 1.96 * se:.2f}, {v + 1.96 * se:.2f}]  (clipped negative mass {neg:.3f})")
        for t, c in types.most_common(5):
            print(f"    {c:>2}  {t}")


if __name__ == "__main__":
    main(sys.argv[1:])
