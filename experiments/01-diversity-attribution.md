# 01 · Where does path diversity come from?

Status: **planned, not run.** Run it only after the 3 × 26 Haiku showcase runs, once their cost has been checked against the Console usage page.

## Question

Before any persona experiment, measure how much of the spread between runs comes from each source:

- **dice:** the outcome draws (`--dice-seed`);
- **order:** the shuffled order of openings (`--order-seed`);
- **sampling:** the model's own randomness.

Claude models reject temperature and seed, so the agent, and the Haiku referee too, are random in every condition. Dice and order can be fixed; sampling can't.

## Setup

Fixed across all conditions:

- persona `agents/personas/maya.yaml`;
- world `world/openings.yaml` (illustrative numbers);
- one commit (`run_start.code`; a dirty tree is refused);
- `--sandbox docker`;
- `--agent-model claude-haiku-5-5 --referee-model claude-haiku-5-5 --summary-model claude-haiku-5-5`;
- `--seed 0`.

Horizon and budget: **26 weeks, `--max-tokens 1000000`.** That's the same as the showcase runs, so their measured cost per run is the cost estimate here. A run that hits the cap ends with `run_end.reason = "token_budget"`. Such runs are reported and excluded, because a truncated path looks less diverse.

| | Condition | Order | Dice | Flags |
|---|---|---|---|---|
| a | vary dice only | fixed | seed + i | `--order-seed 1000` |
| b | vary order only | seed + i | fixed | `--dice-seed 2000` |
| c | vary both | seed + i | seed + i | (neither) |
| d | vary neither | fixed | fixed | `--order-seed 1000 --dice-seed 2000` |

Each condition is one batch of `--runs 10`. With the same `--seed 0`, run i in (a) uses the same dice seed as run i in (c), and run i in (b) uses the same order seed as run i in (c).

```bash
B="uv run python -m orchestrator.run --persona agents/personas/maya.yaml --runs 10 --horizon 26 \
   --agent-model claude-haiku-5-5 --referee-model claude-haiku-5-5 --summary-model claude-haiku-5-5 \
   --max-tokens 1000000 --seed 0"
$B --order-seed 1000                     # a
$B --dice-seed 2000                      # b
$B                                       # c
$B --order-seed 1000 --dice-seed 2000    # d
```

Every batch first logs a `referee_baseline`: the average applicant scored against all 30 openings. With 3 or 4 batches, that also measures how much Haiku's estimates move from batch to batch for an identical input.

## Why (d)

Without (d), (a) measures dice plus sampling and (b) measures order plus sampling, so neither source can be measured on its own. (d) gives the floor, the diversity that comes from sampling alone:

- dice share ≈ diversity(a) − diversity(d);
- order share ≈ diversity(b) − diversity(d);
- interaction ≈ diversity(c) − diversity(a) − diversity(b) + diversity(d).

Approved: (d) adds 10 runs, about a third more cost.

## Dice are common random numbers

Each draw is a pure function of its key (`referee.uniform`):

- `[dice_seed, "apply", opening_id, n]` for the success draw of the n-th application to an opening;
- `[dice_seed, "reply", opening_id, n]` for failure vs ignored, a separate key so it never uses up the success draw;
- `[dice_seed, "launch", repo, n]` and `[dice_seed, "launch_upvotes", repo, n]` for launches.

With a fixed dice seed, the same choice gets the same draw however the paths diverged, so (b) and (d) really do hold outcomes fixed for the same choices. Every draw's key is logged (`referee_decision.success_key` / `reply_key`, `launch.success_key` / `upvotes_key`), and `run_start.dice_scheme = "keyed-sha256-v1"`.

Remaining limit: the Haiku referee isn't deterministic, so the same application can get a slightly different estimate, and the same u can land on either side of it. The per-batch baselines measure how much that estimate moves.

## Measures (analysis/diversity.py, to be written before running)

Within each condition, over the 10 runs:

1. **First-applied opening:** number of distinct values and its entropy.
2. **Application sets:** mean pairwise Jaccard distance between the sets of openings applied to.
3. **Outcome paths:** mean pairwise distance between the weekly state sequences. A week's state is one of `{none, job, part-time, accelerator, broke}` plus "raised money yes/no"; the distance is edit distance divided by the horizon.
4. **End state:** spread of final cash, income, commitment type and company funding. Report the share of runs in each end-state bucket.
5. **Computer use:** shell calls, repos created, launches.

Report each measure with a bootstrap 95% CI over runs. Ten runs per condition will only show large effects, so if the CIs overlap the answer is "not distinguishable at n = 10", not "no effect".

## Cost

Estimate = 40 runs × the measured cost of a showcase run, plus 4 baselines × 30 referee calls. I'll fill in the number after the showcase runs and the Console reconciliation, and won't launch until it's approved.
