# 01 · Where does path diversity come from?

Status: **pre-registered, 2026-10-29.** The commit that adds this text is the pre-registration. Every run of the experiment must record it as `run_start.code.git_sha` with `dirty: false`; a run that records any other commit isn't part of the experiment. Nothing in `orchestrator/ world/ agents/ sandbox/ analysis/diversity.py` changes until all 80 runs have ended. World v2 (`message`, `spend`, logged thinking and `idea`), as introduced in `44b208f`; showcase runs 2–3 ran on it.

Pre-registered files (sha256):

- `analysis/diversity.py` c399d258d18ba2a2a759fdf91207d1761c0afd2b20db11898b9ffc9ea27b7dcc
- `experiments/budget_guard.py` 19c3f6e163f4908387c1611f7006274ffd59fefe4f742c3147c628dd387de28d

## Question

Before any persona experiment, measure how much of the spread between runs comes from each source:

- **dice:** the outcome draws (`--dice-seed`);
- **order:** the shuffled order of openings (`--order-seed`);
- **sampling:** the model's own randomness.

On world v2 the dice also decide whether each message gets a reply (`[dice_seed, "message", contact, n]`), so "dice" covers replies from contacts as well as applications and launches.

Claude models reject temperature and seed, so the agent, and the Haiku referee too, are random in every condition. Dice and order can be fixed; sampling can't.

## Setup

Fixed across all conditions:

- persona `agents/personas/maya.yaml`;
- world `world/openings.yaml` (illustrative numbers);
- one commit (`run_start.code`; a dirty tree is refused);
- `--sandbox docker`;
- `--agent-model claude-haiku-5-5 --referee-model claude-haiku-5-5 --summary-model claude-haiku-5-5`;
- `--seed 0`.

Horizon and budget: **52 weeks, `--max-tokens 2500000`.** Showcase runs 2–3 used about 19k tokens/week in weeks 1–13 and 21–26k in weeks 14–26, so a 52-week run should use about 1.3M; the cap is about twice that. A run that hits the cap ends with `run_end.reason = "token_budget"`. Such runs are reported and excluded, because a truncated path looks less diverse; if more than 2 of a condition's 20 hit it, the cap was wrong and the condition is rerun.

| | Condition | Order | Dice | Flags |
|---|---|---|---|---|
| a | vary dice only | fixed | seed + i | `--order-seed 1000` |
| b | vary order only | seed + i | fixed | `--dice-seed 2000` |
| c | vary both | seed + i | seed + i | (neither) |
| d | vary neither | fixed | fixed | `--order-seed 1000 --dice-seed 2000` |

Each condition is one batch of `--runs 20`, labelled `exp01a` … `exp01d` (`--label`, so run ids are `maya-exp01a-<stamp>-001` …). With the same `--seed 0`, run i in (a) uses the same dice seed as run i in (c), and run i in (b) uses the same order seed as run i in (c).

The four batches run in parallel, one process each, so the experiment takes about as long as one batch of 20 (~3–4 hours at ~10 minutes per 52-week run) instead of four. Each run still has its own container, network and Gitea user. If the spend limit is hit, `api_error` stops each batch after its current run; nothing is retried automatically.

```bash
B="uv run python -m orchestrator.run --persona agents/personas/maya.yaml --runs 20 --horizon 52 \
   --agent-model claude-haiku-5-5 --referee-model claude-haiku-5-5 --summary-model claude-haiku-5-5 \
   --max-tokens 2500000 --seed 0"
$B --label exp01a --order-seed 1000 > runs/exp01a.out 2>&1 &                    # a
sleep 5; $B --label exp01b --dice-seed 2000 > runs/exp01b.out 2>&1 &            # b
sleep 5; $B --label exp01c > runs/exp01c.out 2>&1 &                             # c
sleep 5; $B --label exp01d --order-seed 1000 --dice-seed 2000 > runs/exp01d.out 2>&1 &   # d
uv run python experiments/budget_guard.py --runs 'runs/maya-exp01?-*.jsonl' \
    --procs 'orchestrator.run .*--label exp01' --limit-usd 20 > runs/exp01-guard.out 2>&1 &
wait
uv run python analysis/diversity.py a='runs/maya-exp01a-*.jsonl' b='runs/maya-exp01b-*.jsonl' \
    c='runs/maya-exp01c-*.jsonl' d='runs/maya-exp01d-*.jsonl'
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

## Measures (`analysis/diversity.py`)

Within each condition, over the 20 runs:

1. **First-applied opening:** number of distinct values and its entropy.
2. **Application sets:** mean pairwise Jaccard distance between the sets of openings applied to.
3. **Outcome paths:** mean pairwise distance between the weekly state sequences. A week's state is one of `{none, job, part-time, accelerator, broke}` plus "raised money yes/no"; the distance is edit distance divided by the horizon.
4. **End state:** spread of final cash, income, commitment type and company funding. Report the share of runs in each end-state bucket.
5. **Computer and world use:** shell calls, repos created, launches, messages, spend.

Pairwise distances and the cash spread get a bootstrap 95% CI over runs (a run is never paired with its own copy). First-applied entropy and distinct counts are biased low under resampling, so they're reported without a CI and compared only at equal n (20 each). The source split (dice, order, interaction) is computed for the two pairwise distances, each with a CI. Twenty runs per condition still only show fairly large effects: if a CI includes 0, the answer is "not distinguishable at n = 20", not "no effect".

Also from the same runs, not part of the attribution: how often Maya messages her parents, and whether and when the paid pilot happens; what she spends company money on.

## Stopping rules

- **Cost:** `experiments/budget_guard.py` sums the logged cost of every `maya-exp01?-*` log (runs and baselines) every 30 s and sends SIGTERM to all four batches once it passes **$20**. Interrupted runs end with `run_end.reason = "interrupted"` and are left out like any run that didn't reach the horizon.
- **API errors:** a spend-limit or other API failure ends that run with `api_error` and stops its batch (no retries).
- **Token cap:** if more than 2 of a condition's 20 runs end with `token_budget`, that condition is rerun with a higher cap; its first attempt is reported but not analysed.
- Nothing is rerun to get a different answer. Conditions with fewer than 15 runs at the horizon are reported as underpowered.

## Known issue in world v2 (seen in showcase runs 2–3, before this pre-registration)

Contacts can only *give* what the offer list allows (a referral, a paid pilot), but the contact's voice also promises things the world can't deliver: owner names and numbers, invoice photos. Maya then follows up week after week. Each showcase run sent 27 messages to her parents, mostly chasing these. This is part of world v2 and is held fixed across all four conditions. It affects how actions are spent, not the measures' definitions. A fix (contacts put anything they can share in the reply itself, or deliverables become world objects) would be world v3 and isn't part of this experiment.

## Predictions

Written by Claude before any experiment-01 run, from the pilot and showcase runs. The request asked for "my predictions" but didn't include them; if the user adds theirs, it'll be in a separate commit (git dates it) before `analysis/diversity.py` is run on these logs.

1. Sampling alone carries most of the diversity: in (d), path edit distance / horizon is at least half of (c)'s.
2. Application sets: order share > dice share (b − d > a − d). Which openings she applies to depends on what she reads first.
3. Paths: dice share > order share (a − d > b − d). Which job accepts her, and when, is drawn by the dice.
4. The interaction term's 95% CI includes 0 for both distances.
5. In at least 90% of runs she messages her parents 10 or more times (the follow-up loop above persists).
6. A paid pilot happens in 20–60% of runs.
7. A contractor is hired in at most 20% of runs; launch ads are bought in at most 10%.
8. At least 80% of runs end week 52 in a full-time job.
9. No run hits the token cap; cost per run is $0.08–0.14, total under $12.

## Cost

Estimate = 80 runs × the measured cost of a 52-week run, plus 4 baselines × 30 referee calls (~$0.007 each).

- Showcase v1 (run 1, world v1, 26 weeks): $0.0314 for the run, 276,600 tokens. Console check pending.
- Showcase v2 (runs 2–3, world v2, 26 weeks): $0.0489 and $0.0469 per run (585,148 and 530,143 tokens), plus a $0.007 baseline.
- 52-week estimate: about 1.3M tokens and **~$0.11 per run**; per-week tokens grow slowly over a run (19k → 25k), so this is a bit more than twice the 26-week cost.
- **Projected total: 80 × $0.11 + 4 × $0.007 ≈ $9.** Stop at $20 logged (guard above). Logged Anthropic spend before the experiment: $0.14. The account's remaining spend limit isn't readable with an API key, so it wasn't checked; the $20 guard and the `api_error` stop are the protection.

Approved to run at this scale (2026-10-29).
