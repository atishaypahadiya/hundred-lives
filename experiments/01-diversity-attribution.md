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
| d | *recommended:* vary neither | fixed | fixed | `--order-seed 1000 --dice-seed 2000` |

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

Without (d), (a) measures dice plus sampling and (b) measures order plus sampling, so the difference between them can't be pinned on either source. (d) gives the floor, the diversity that comes from sampling alone:

- dice share ≈ diversity(a) − diversity(d);
- order share ≈ diversity(b) − diversity(d);
- interaction ≈ diversity(c) − diversity(a) − diversity(b) + diversity(d).

It adds 10 runs (about +33% cost).

## Caveat to decide before running: fixed dice is a fixed stream

`Run.rng` is one sequential stream. Each application or launch takes the next draws, so once two runs differ in what they do, the same draw goes to a different application. "Fixed dice" then only fixes outcomes for as long as the runs behave identically. That weakens (b) and (d).

The fix, which needs a yes before it's built: key each draw by `(dice_seed, opening_id, n-th application to it)` (and by repo for launches). The same application to the same opening then gets the same draw whatever else the run did. Old runs can't be replayed under the keyed scheme, which is acceptable since the world changed anyway.

## Measures (analysis/diversity.py, to be written before running)

Within each condition, over the 10 runs:

1. **First-applied opening:** number of distinct values and its entropy.
2. **Application sets:** mean pairwise Jaccard distance between the sets of openings applied to.
3. **Outcome paths:** mean pairwise distance between the weekly state sequences. A week's state is one of `{none, job, part-time, accelerator, broke}` plus "raised money yes/no"; the distance is edit distance divided by the horizon.
4. **End state:** spread of final cash, income, commitment type and company funding. Report the share of runs in each end-state bucket.
5. **Computer use:** shell calls, repos created, launches.

Report each measure with a bootstrap 95% CI over runs. Ten runs per condition will only show large effects, so if the CIs overlap the answer is "not distinguishable at n = 10", not "no effect".

## Cost

Estimate = (40 runs if (d) is included, otherwise 30) × the measured cost of a showcase run, plus 4 baselines × 30 referee calls. I'll fill in the number after the showcase runs and the Console reconciliation, and won't launch until it's approved.
