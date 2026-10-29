"""Path types and the Vendi score per condition. EXPLORATORY: not in experiment 01's pre-registration.

    uv run python analysis/vendi.py a='runs/maya-exp01a-*.jsonl' b=... c=... d=...

Two paths per run, both one item per week:

- Commitment path: analysis/diversity.py's path (each week's commitment: none, part-time, job or
  accelerator, or broke; plus whether the company has raised money), with diversity.py's run filter
  (horizon only). It doesn't see gigs, applications, projects, launches, posts or messages, so two
  lives that differ only in those count as the same path: read its Vendi as a lower bound on how
  different the lives are (in spirit; Vendi isn't strictly monotone in similarity).
  Its path type is the weekly states with repeats collapsed ("none → job"): the order of changes,
  not their timing.
- Event path (second feature): each week's commitment state plus the set of events that week
  (applications by opening type, gig, launch, post, hackathon result, new repo, message, spend).
  Two weeks match only if both the state and the events match.

Vendi score (Friedman & Dieng 2023): the effective number of distinct paths, exp of the entropy of the
eigenvalues of K/n; 1 if all paths are identical, n if all are completely different. Distance d is
diversity.py's edit distance divided by the longer path's length. The kernel starts as K = 1 − d. The
minimum eigenvalue of K is printed; if it's below −1e-9, K isn't positive semidefinite, and the kernel
becomes exp(−d/τ) with τ the median pairwise distance (or the smallest positive one if the median is 0).
That's checked again, and only eigenvalues with |λ| < 1e-9 are clipped to 0; if it still isn't PSD, no
Vendi is reported. 95% CI by jackknife (leave one run out, same kernel and τ), since bootstrap
resamples repeat runs, which reads as less diversity. Compare only at equal n.
"""

import glob
import importlib.util
import json
import math
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

spec = importlib.util.spec_from_file_location("diversity", Path(__file__).with_name("diversity.py"))
diversity = importlib.util.module_from_spec(spec)
spec.loader.exec_module(diversity)

PSD_TOL = 1e-9


def eigenvalues(a: list[list[float]], sweeps: int = 100) -> list[float]:
    """Symmetric eigenvalues by cyclic Jacobi rotations (n is at most a few dozen)."""
    n = len(a)
    a = [row[:] for row in a]
    for _ in range(sweeps):
        off = sum(a[i][j] ** 2 for i in range(n) for j in range(n) if i != j)
        if off < 1e-22:
            break
        for p in range(n):
            for q in range(p + 1, n):
                if abs(a[p][q]) < 1e-18:
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


def distances(paths: list[list[str]]) -> list[list[float]]:
    return [[diversity.edit(a, b) / max(len(a), len(b), 1) for b in paths] for a in paths]


def kernel(d: list[list[float]], tau: float | None) -> list[list[float]]:
    return [[1 - x if tau is None else math.exp(-x / tau) for x in row] for row in d]


def vendi_from(k: list[list[float]]) -> tuple[float | None, float]:
    """(Vendi, min eigenvalue of K). None if K has an eigenvalue below −PSD_TOL."""
    n = len(k)
    lam = eigenvalues(k)
    low = min(lam)
    if low < -PSD_TOL:
        return None, low
    pos = [x / n for x in lam if x >= PSD_TOL]  # |λ| < 1e-9 clipped to 0
    total = sum(pos)
    return math.exp(-sum(x / total * math.log(x / total) for x in pos)), low


def analyze(paths: list[list[str]]) -> str:
    n = len(paths)
    if n < 3:
        return f"n={n}: too few for a Vendi CI"
    d = distances(paths)
    tau, notes = None, []
    v, low = vendi_from(kernel(d, None))
    notes.append(f"min eigenvalue of 1−d: {low:.2e}")
    if v is None:
        pair = sorted(d[i][j] for i in range(n) for j in range(i + 1, n))
        tau = statistics.median(pair) or min((x for x in pair if x > 0), default=1.0)
        v, low = vendi_from(kernel(d, tau))
        notes.append(f"not PSD → exp(−d/τ), τ = {tau:.3f}, min eigenvalue {low:.2e}")
        if v is None:
            return f"n={n}  Vendi not reported: exp(−d/τ) isn't PSD either  ({'; '.join(notes)})"
    loo = []
    for i in range(n):
        sub = [row[:i] + row[i + 1:] for j, row in enumerate(d) if j != i]
        x, _ = vendi_from(kernel(sub, tau))
        if x is not None:
            loo.append(x)
    mean = statistics.fmean(loo)
    se = math.sqrt((len(loo) - 1) / len(loo) * sum((x - mean) ** 2 for x in loo))
    return f"n={n}  Vendi {v:.2f} [{v - 1.96 * se:.2f}, {v + 1.96 * se:.2f}]  ({'; '.join(notes)})"


def collapse(states: list[str]) -> str:
    return " → ".join(s for i, s in enumerate(states) if i == 0 or s != states[i - 1])


def event_path(path: Path, states: list[str]) -> list[str]:
    events = defaultdict(set)
    for line in path.open():
        r = json.loads(line)
        t, w = r["type"], r.get("week")
        if t == "referee_decision":
            events[w].add("apply:" + (r.get("opening_type") or "?"))
        elif t in ("gig", "launch", "post", "repo_created", "message", "spend"):
            events[w].add(t)
        elif t == "hackathon_result":
            events[w].add("hackathon:" + r.get("outcome", "?"))
    return [s + "|" + ",".join(sorted(events[w])) for w, s in enumerate(states, 1)]


def main(args: list[str]):
    for arg in args:
        name, pattern = arg.split("=", 1)
        files = [Path(p) for p in sorted(glob.glob(pattern)) if not p.endswith("-referee-baseline.jsonl")]
        runs = [(f, r) for f in files for r in [diversity.load(f)] if r]
        done = [(f, r) for f, r in runs if r["reason"] == "horizon"]
        paths = [r["states"] for _, r in done]
        types = Counter(collapse(p) for p in paths)
        print(f"{name}: {len(done)} of {len(runs)} runs reached the horizon")
        print(f"  commitment path (jobs, internships, accelerators; broke weeks; money raised): "
              f"{len(types)} path types; {analyze(paths)}")
        for t, c in types.most_common(5):
            print(f"      {c:>2}  {t}")
        print(f"  event path (commitments + weekly applications, gigs, launches, posts, hackathons, "
              f"repos, messages, spend): {analyze([event_path(f, r['states']) for f, r in done])}")


if __name__ == "__main__":
    main(sys.argv[1:])
