# 02 · Is Self's quiet solo builder a trait or one life?

Status: **planned, 2026-10-29.** Part A (base × 5) is approved to run once Track A is published. Part B (sensitivity, four conditions since the hidden-streak variant was added below) is held until the Console spend limit resets on 2026-11-01 (UTC), and it launches only after month-to-date spend has been checked against the $50 limit and the user says go.

Part A runs from `9ba72b4` (branch `viewer-restyle`, which stays there until part A ends). Part B runs from the last commit on master that changes this file (`git log -1 --format=%h -- experiments/02-self-lives.md`), checked out in its own worktree; every part B run must record that commit. The two differ in code only by `training.show_streak` (default on), which the hidden-streak variant needs. For the four personas on `world/student.yaml`, every agent-facing prompt is byte-identical between the two commits. The check rendered the system prompt, the tools, and the week 5 and week 40 prompts, with and without the sandbox: 32 renders. The runs use student world v2 (`c6d11d6`: hours-based time, streak holds in zero-action weeks, reflections state the week) with the sensitivity personas and `referee_view` from `09f5ac3`. Every run must record that commit as `run_start.code.git_sha` with `dirty: false`.

## Question

Track A (`self-tracka4-20261029-035616-001`, world v1, seed 1) lived 104 weeks as a quiet solo builder. It wrote to one contact besides its parents (Marcus, 6 times; parents once), made 7 posts, had 2 followers, made 9 commits, and placed 19 big bets with 4 wins. Is that pattern consistent across lives of the same persona (part A)? And which part of the persona text drives it (part B)?

## Setup

Fixed across all runs:

- world `world/student.yaml` (illustrative numbers), except hiddenstreak, which uses `world/student-hiddenstreak.yaml`;
- `--agent-model claude-sonnet-5-5 --referee-model claude-haiku-5-5 --summary-model claude-haiku-5-5`;
- `--runs 5 --seed 0` (run i, counting from 1, uses order and dice seed 0 + i, so seeds 1–5; part A's runs record 1–5), `--horizon 104`, `--max-tokens 4000000`;
- `--sandbox docker`;
- contacts identical to `agents/personas/self.yaml`;
- the referee sees the same person in every condition: variants carry `self.yaml`'s public fields in `referee_view` (hidden from the agent), so a changed trait reaches outcomes only through the agent's choices.

| Part | Condition (report name) | Persona | Label | Change |
|---|---|---|---|---|
| A | base | `self.yaml` | `base` | none |
| B | nosolo | `self-nosolo.yaml` | `nosolo` | "founder of Conscious" instead of "solo founder" |
| B | socialgoal | `self-socialgoal.yaml` | `socialgoal` | goal ends "…; and surrounded by people I build with and lift up." |
| B | goal+world | `self-goalonly.yaml` | `goalonly` | background, traits, skills, interests and network removed; name, goal, money, contacts kept |
| B | hiddenstreak | `self.yaml` (same as base) | `hiddenstreak` | world `student-hiddenstreak.yaml`: `train` still available, but the streak is never shown |

"goal+world" is not a blank persona. The contact list still names De Anza and the ISA, and the world still makes Self an SJSU student. It's the baseline for what the goal plus the world produce.

Same seeds across conditions: run i of every condition uses dice seed i and order seed i, so lives can be compared pairwise. Two runs that make the same choice get the same draw.

## Hidden streak (pre-registered 2026-10-29)

**Timing.** The user proposed this variant and its hypothesis before any part A life had ended. This text was committed after part A's run 1 ended (about 15:39 UTC). Before writing it, Claude saw three things from part A: run 1's totals (104 weeks, 283 valid actions, $1.52), its last three weekly digests (a strength workout each week, the transit job), and run 2's progress lines. No part A analysis was run. The motivation is from Track A (world v1, before part A): 49% of its action-using calls were `train` (90 sessions), 4% `post` and 4% `message`.

**Treatment.** In the base world the agent sees the streak in three places: the `train` tool's description, the world's tool list ("your streak of weeks in a row with a session is shown each week"), and each week's time line and `train` result ("Training streak: N weeks"). `world/student-hiddenstreak.yaml` sets `training.show_streak: false`, which removes all three. The tool says only "It's logged", and the result is "Logged." The streak is still counted and logged (`train.streak`, `week_end.state.train_streak`), so it can be analysed. Everything else, the persona included, is identical to base.

**Hypothesis (the user's).** When progress isn't legible, effort shifts away from training toward social activity and posting.

**Measures.** Compare hiddenstreak with base, per life:
- the share of action-using calls that are `train`, `post` and `message` (`analysis/self_lives.py`);
- training sessions and trained weeks;
- posts, messages and distinct contacts messaged.

**Predictions.** Each is checked on the means over the lives that reach week 104, and on seed pairs (run i in both conditions uses dice and order seed i):
1. `train` share is lower in hiddenstreak than in base, in the mean and in at least 4 of 5 seed pairs.
2. `post` + `message` share is higher in hiddenstreak than in base, in the mean and in at least 4 of 5 seed pairs.

The hypothesis is supported if both hold. If 1 holds and 2 doesn't, effort moved elsewhere (applications, building) rather than to social activity. With 5 lives each, this is descriptive, not a test.

## Commands

Run from the worktree. The API key comes from the main tree's `.env`.

```bash
set -a; . ../hundred-lives/.env; set +a
B="uv run python -m orchestrator.run --world world/student.yaml --runs 5 --horizon 104 --seed 0 \
   --agent-model claude-sonnet-5-5 --referee-model claude-haiku-5-5 --summary-model claude-haiku-5-5 \
   --max-tokens 4000000"

# Part A
$B --persona agents/personas/self.yaml --label base > runs/self-base.out 2>&1 &
uv run python experiments/budget_guard.py --runs 'runs/self-base-*.jsonl' \
    --procs 'orchestrator.run .*--label base' --limit-usd 15 > runs/self-base-guard.out 2>&1 &

# Part B: from its own worktree at the part B commit (see above), four conditions in parallel,
# one $12 guard each. Launch from a script file, so the guards' --procs regexes can't match the
# launcher's own command line.
#   git worktree add --detach ../hundred-lives-exp02b $(git log -1 --format=%h -- experiments/02-self-lives.md)
#   cd ../hundred-lives-exp02b && uv sync
for c in nosolo socialgoal goalonly; do
  $B --persona agents/personas/self-$c.yaml --label $c > runs/self-$c.out 2>&1 &
  uv run python experiments/budget_guard.py --runs "runs/self-$c-*.jsonl" \
      --procs "orchestrator.run .*--label $c( |\$)" --limit-usd 12 > runs/self-$c-guard.out 2>&1 &
  sleep 5
done
# the second --world replaces the one in $B
$B --persona agents/personas/self.yaml --world world/student-hiddenstreak.yaml --label hiddenstreak \
   > runs/self-hiddenstreak.out 2>&1 &
uv run python experiments/budget_guard.py --runs 'runs/self-hiddenstreak-*.jsonl' \
    --procs 'orchestrator.run .*--label hiddenstreak( |$)' --limit-usd 12 > runs/self-hiddenstreak-guard.out 2>&1 &

uv run python analysis/self_lives.py base='../hundred-lives-viewer/runs/self-base-*.jsonl' \
    nosolo='runs/self-nosolo-*.jsonl' socialgoal='runs/self-socialgoal-*.jsonl' \
    goal+world='runs/self-goalonly-*.jsonl' hiddenstreak='runs/self-hiddenstreak-*.jsonl'
```

## Measures (`analysis/self_lives.py`)

Per life:
- messages sent and replied, for each contact;
- offers taken;
- posts, likes, followers;
- commits, repos, shell calls;
- big bets (applications to accelerators, angels, grants, student programs and hackathons) and their wins;
- launches, hackathon placings, company spend;
- training sessions, training weeks and the final streak;
- the share of action-using calls that were `train`, `post` and `message`;
- cash and commitments at the end.

Per condition: each measure's spread. Across conditions, a comparison table gives mean [min–max] per life for messages, distinct contacts messaged, posts, followers, big bets, big-bet wins, commits, training sessions and weeks, and the three action shares. Lives that don't reach week 104 are listed and left out.

With 5 lives per condition, this is descriptive: it shows whether a pattern holds in 5 of 5 lives or varies, not an effect size.

## Stopping rules

- Part A: a $15 guard on its logs.
- Part B: one $12 guard per condition (four), so one condition's cost can't stop the others.
- API errors stop that batch with `api_error`. Nothing is rerun to get a different answer.

## Cost

Track A cost $1.63 for 104 weeks (1.69M tokens, Sonnet agent). World v2 frees about 2 more actions in each semester week, so a run may cost more: estimate **$1.6–2.4 per life**.

| | Lives | Estimate | Guard |
|---|---|---|---|
| A | 5 | $8–12 | $15 |
| B | 20 | $32–48 | 4 × $12 |

Logged Anthropic spend in October 2026, before part A, across both runs directories: $11.06 (experiment 01 finished, $6.96 of it). Console spend may differ, since cache prices are unreconciled. Before part B: check month-to-date spend in the Console against the $50 monthly limit. Launch only if the projected total (B's estimate, plus anything still running) fits in what's left, and the user approves. With four conditions, B's upper estimate ($48) is close to the whole $50 monthly limit. If it doesn't fit, the user picks which conditions run first.
