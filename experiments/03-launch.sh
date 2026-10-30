#!/bin/bash
# Experiment 03: Self x 5 on world v3, with a $15 budget guard. Run from the worktree at the plan's
# commit (experiments/03-world-v3.md, Commands). A script file, so the guard's --procs regex can't
# match this launcher's own command line.
set -euo pipefail
cd "$(dirname "$0")/.."
set -a; . ../hundred-lives/.env; set +a
mkdir -p runs
uv run python -m orchestrator.run --persona agents/personas/self.yaml --world world/student-v3.yaml \
    --runs 5 --horizon 104 --seed 0 --agent-model claude-sonnet-5-5 --referee-model claude-haiku-5-5 \
    --summary-model claude-haiku-5-5 --max-tokens 4000000 --label v3 > runs/self-v3.out 2>&1 &
sleep 5
uv run python experiments/budget_guard.py --runs 'runs/self-v3-*.jsonl' \
    --procs 'orchestrator.run .*--label v3( |$)' --limit-usd 15 > runs/self-v3-guard.out 2>&1 &
echo "experiment 03 launched: runs/self-v3.out, guard runs/self-v3-guard.out"
