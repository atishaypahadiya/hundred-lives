"""Stop an experiment's batches once their logged API cost passes a limit.

    uv run python experiments/budget_guard.py --runs 'runs/maya-exp01?-*.jsonl' \
        --procs 'orchestrator.run .*--label exp01' --limit-usd 20

Every 30 s it sums the logged cost of every matching log (a run's latest cost_usd_total, a
batch baseline's per-call cost_usd). Past the limit, it sends SIGTERM to every process whose
command line matches --procs: each run then ends with run_end.reason = "interrupted" (its
sandbox torn down, its repos exported) and its batch stops. It exits when no matching process
is left. It only reads logs; the logged cost is computed from the API's usage fields, which
were reconciled against the Console after showcase run 1.
"""

import argparse
import glob
import json
import os
import re
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone


def logged_cost(pattern: str) -> tuple[float, int]:
    total, files = 0.0, 0
    for path in glob.glob(pattern):
        files += 1
        run = 0.0
        with open(path) as f:
            for line in f:
                if '"cost_usd' not in line:
                    continue
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue  # a line still being written
                if r.get("type") == "llm_call":
                    run = max(run, r.get("cost_usd_total") or 0.0)
                elif r.get("type") == "referee_baseline":
                    run += r.get("cost_usd") or 0.0
        total += run
    return total, files


def procs(pattern: str) -> list[int]:
    out = subprocess.run(["ps", "-axo", "pid=,command="], capture_output=True, text=True).stdout
    me = os.getpid()
    return [int(pid) for pid, cmd in (l.strip().split(" ", 1) for l in out.splitlines() if l.strip())
            if int(pid) != me and re.search(pattern, cmd) and "budget_guard" not in cmd]


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--runs", required=True, help="glob of the experiment's logs, baselines included")
    ap.add_argument("--procs", required=True, help="regex for the batch processes' command lines")
    ap.add_argument("--limit-usd", type=float, required=True)
    ap.add_argument("--every-s", type=float, default=30)
    args = ap.parse_args()
    stamp = lambda: datetime.now(timezone.utc).strftime("%H:%M:%SZ")
    stopped = False
    while True:
        cost, files = logged_cost(args.runs)
        live = procs(args.procs)
        print(f"{stamp()} logged ${cost:.4f} across {files} logs · {len(live)} batch processes", flush=True)
        if cost > args.limit_usd and not stopped:
            print(f"{stamp()} STOP: logged ${cost:.4f} > ${args.limit_usd:.2f}; SIGTERM to {live}", flush=True)
            for pid in live:
                try:
                    os.kill(pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
            stopped = True
        if not live:
            print(f"{stamp()} no batch processes left; final logged ${cost:.4f}", flush=True)
            return 1 if stopped else 0
        time.sleep(args.every_s)


if __name__ == "__main__":
    sys.exit(main())
