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

## Results

Ran 2026-10-29, 09:54–13:15 UTC. All 80 runs record `eda3a9c` with `dirty: false`. The pre-registered files still match the hashes above. 80 of 80 runs reached week 52. None ended on the token budget, an API error or an interrupt, so nothing was excluded or rerun. Logged cost was $6.96: $6.93 for the runs and $0.03 for the four referee baselines. A run used 0.98M tokens at the median and 1.27M at most. Numbers below are from `analysis/diversity.py` unless marked exploratory.

The world's odds are illustrative, so this describes the model in this world, not real careers.

### Measures (20 of 20 runs kept per condition; 95% bootstrap CI)

| measure | a: vary dice | b: vary order | c: vary both | d: vary neither |
|---|---|---|---|---|
| path edit distance / horizon | 0.617 [0.549, 0.672] | 0.372 [0.219, 0.492] | 0.623 [0.542, 0.692] | 0.280 [0.116, 0.398] |
| application-set Jaccard distance | 0.304 [0.250, 0.347] | 0.264 [0.223, 0.300] | 0.308 [0.263, 0.345] | 0.247 [0.212, 0.277] |
| first-applied entropy (bits), no CI | 1.369 | 1.619 | 1.858 | 1.739 |
| first-applied distinct, no CI | 3 | 4 | 4 | 4 |
| final cash sd | $27,521 [19,477, 33,301] | $12,483 [4,482, 17,188] | $24,431 [13,772, 32,271] | $25,598 [19,115, 31,241] |
| final cash mean | $56,168 | $62,605 | $48,452 | $46,390 |
| income at week 52, mean per week | $2,140 | $2,145 | $1,958 | $1,772 |
| end state | job+raised 50%, job 40%, accelerator+raised 10% | job 70%, job+raised 30% | job+raised 55%, job 35%, part-time 5%, accelerator+raised 5% | job 75%, job+raised 15%, part-time 5%, accelerator 5% |
| shell calls / repos / launches, mean | 11.5 / 1.2 / 0.3 | 12.8 / 1.2 / 0.5 | 13.6 / 1.1 / 0.3 | 11.7 / 1.6 / 0.6 |
| messages / company spend, mean | 19.2 / 0.5 | 26.4 / 0.1 | 20.6 / 0.3 | 25.0 / 0.1 |

The end-state bucket counts an accelerator ahead of a job. Counting full-time jobs held at week 52 directly, 77 of 80 runs had one, and 3 of those also had an accelerator. Two runs ended on part-time work only and one in an accelerator only.

### Sources of diversity (difference of means; 95% CI from independent bootstrap draws)

| | path edit distance / horizon | application-set Jaccard |
|---|---|---|
| floor (d) | 0.280 [0.116, 0.398] | 0.247 [0.212, 0.277] |
| dice (a − d) | **0.338 [0.203, 0.505]** | 0.057 [−0.009, 0.113] |
| order (b − d) | 0.093 [−0.095, 0.290] | 0.016 [−0.037, 0.068] |
| interaction (c − a − b + d) | −0.087 [−0.306, 0.134] | −0.012 [−0.090, 0.079] |

The floor is what's left with order and dice both fixed: the randomness of every model in the run. That covers the agent, the referee (Claude models take no temperature or seed, so its odds estimates vary), and the model that writes contacts' replies. Whether a contact replies is dice; what the reply says is the model. All three are Haiku, so this design can't split the floor between them.

- **Paths:** the floor is 0.280 of c's 0.623 (45%) and the dice add 0.338 (54%). Roughly half the difference between paths comes from luck, and roughly half from the models. The order term's CI includes 0, as does the interaction's.
- **Application sets:** which openings she applies to is mostly the floor (0.247 of 0.308). Neither the dice nor the order is distinguishable from 0 at n = 20.

All shares are differences of means with wide CIs, so "roughly half" is as precise as they allow.

### Referee baseline

Each batch's referee scored a fixed average applicant against all 30 openings. The mean estimate was 0.86, 0.91, 0.91 and 0.90 × the base rate across the four batches.

| opening type | estimate / base rate |
|---|---|
| job | 0.92–0.98 |
| accelerator | 0.89–0.92 |
| grant | 0.78–0.93 |
| angel intro | 0.77–0.81 |

The referee is a little harsh, most of all on angel intros. That holds in every condition, so it doesn't bias the comparison.

### Vendi score (exploratory, not pre-registered)

These numbers are from `analysis/vendi.py` (branch `viewer-restyle`, `60539d4`), written after this pre-registration. It reuses `diversity.py`'s paths and edit distance.

- **Vendi:** the effective number of distinct lives among the 20, from the kernel K = 1 − d. Its 95% CI is by jackknife.
- **Positive-semidefinite check:** K's smallest eigenvalue never went below −1e-9 (the lowest was −8.5e-16), so the exp(−d/τ) fallback wasn't needed.
- **Commitment path:** sees only commitments, broke weeks and money raised, so read its Vendi as a lower bound on how different the lives are.
- **Event path:** adds each week's events (applications by type, gigs, launches, posts, new repos, messages, spend).

| | a | b | c | d |
|---|---|---|---|---|
| commitment-path types (repeats collapsed) | 11 | 6 | 14 | 7 |
| commitment-path Vendi | 5.16 [3.49, 6.84] | 2.81 [1.61, 4.02] | 5.76 [3.87, 7.65] | 2.40 [1.49, 3.31] |
| most common type | none → job → job+raised (5) | none → job (11) | 2-way tie at 3 | none → job (10) |
| event-path Vendi | 13.62 [11.31, 15.93] | 11.38 [9.97, 12.79] | 14.96 [12.77, 17.15] | 11.98 [10.72, 13.25] |

In c, two types tie at 3 runs each: none → job, and none → job → job+raised → none+raised → job+raised.

On commitments, the conditions with varying dice have about twice the effective number of lives (5–6 of 20) as those with fixed dice (2.4–2.8). That agrees with the edit-distance split. Counted with their weekly events, the lives are far more varied, 11–15 of 20 in every condition: they differ more in what they did week to week than in what they held.

### Predictions

| # | prediction | result | |
|---|---|---|---|
| 1 | floor ≥ half of c's path distance | 0.280 / 0.623 = 0.45 | missed (narrowly) |
| 2 | application sets: order share > dice share | order 0.016, dice 0.057; both CIs include 0 | missed |
| 3 | paths: dice share > order share | dice 0.338, order 0.093 | held |
| 4 | interaction CI includes 0 for both distances | [−0.306, 0.134] and [−0.090, 0.079] | held |
| 5 | parents messaged 10+ times in ≥ 90% of runs | 66 of 80 (83%); median 16 | missed |
| 6 | a paid pilot in 20–60% of runs | 40 of 80 (50%) | held |
| 7 | contractor hired in ≤ 20%, launch ads bought in ≤ 10% | contractor 19 of 80 (24%); ads 0 of 80 | partly: ads held, contractor missed |
| 8 | ≥ 80% end week 52 in a full-time job | 77 of 80 (96%) | held |
| 9 | no token-cap runs; $0.08–0.14 per run; total < $12 | no cap; total $6.93; per run $0.052–0.106, 20 of 80 under $0.08 | partly: cap and total held, per-run range missed |

That's 4 held, 2 partly held and 3 missed. Counting the partial ones as half, 5 of 9.

The follow-up loop from the known issue persists. Parents got 1,268 of the 1,827 contact messages across the 80 runs, a median of 16 per run.
