# 02 · Is Self's quiet solo builder a trait or one life?

Status: **planned, 2026-10-29.** Part A (base × 5) is approved to run once Track A is published. Part B (sensitivity) is held until the Console spend limit resets on 2026-11-01 (UTC), and it launches only after month-to-date spend has been checked against the $50 limit and the user says go.

Both parts run from one commit: the last one to change this file. Part A records it, and Part B must record the same one. Branch `viewer-restyle` stays at that commit until Part B ends; new work goes on master. The runs use student world v2 (`c6d11d6`: hours-based time, streak holds in zero-action weeks, reflections state the week) with the sensitivity personas and `referee_view` from `09f5ac3`. Every run must record that commit as `run_start.code.git_sha` with `dirty: false`.

## Question

Track A (`self-tracka4-20261029-035616-001`, world v1, seed 1) lived 104 weeks as a quiet solo builder. It wrote to one contact besides its parents (Marcus, 6 times; parents once), made 7 posts, had 2 followers, made 9 commits, and placed 19 big bets with 4 wins. Is that pattern consistent across lives of the same persona (part A)? And which part of the persona text drives it (part B)?

## Setup

Fixed across all runs:

- world `world/student.yaml` (illustrative numbers);
- `--agent-model claude-sonnet-5-5 --referee-model claude-haiku-5-5 --summary-model claude-haiku-5-5`;
- `--runs 5 --seed 0` (run i uses order and dice seed i, so seeds 0–4), `--horizon 104`, `--max-tokens 4000000`;
- `--sandbox docker`;
- contacts identical to `agents/personas/self.yaml`;
- the referee sees the same person in every condition: variants carry `self.yaml`'s public fields in `referee_view` (hidden from the agent), so a changed trait reaches outcomes only through the agent's choices.

| Part | Condition (report name) | Persona | Label | Change |
|---|---|---|---|---|
| A | base | `self.yaml` | `base` | none |
| B | nosolo | `self-nosolo.yaml` | `nosolo` | "founder of Conscious" instead of "solo founder" |
| B | socialgoal | `self-socialgoal.yaml` | `socialgoal` | goal ends "…; and surrounded by people I build with and lift up." |
| B | goal+world | `self-goalonly.yaml` | `goalonly` | background, traits, skills, interests and network removed; name, goal, money, contacts kept |

"goal+world" is not a blank persona. The contact list still names De Anza and the ISA, and the world still makes Self an SJSU student. It's the baseline for what the goal plus the world produce.

Same seeds across conditions: run i of every condition uses dice seed i and order seed i, so lives can be compared pairwise. Two runs that make the same choice get the same draw.

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

# Part B: three conditions in parallel, one $12 guard each
for c in nosolo socialgoal goalonly; do
  $B --persona agents/personas/self-$c.yaml --label $c > runs/self-$c.out 2>&1 &
  uv run python experiments/budget_guard.py --runs "runs/self-$c-*.jsonl" \
      --procs "orchestrator.run .*--label $c( |\$)" --limit-usd 12 > runs/self-$c-guard.out 2>&1 &
  sleep 5
done

uv run python analysis/self_lives.py base='runs/self-base-*.jsonl' nosolo='runs/self-nosolo-*.jsonl' \
    socialgoal='runs/self-socialgoal-*.jsonl' goal+world='runs/self-goalonly-*.jsonl'
```

## Measures (`analysis/self_lives.py`)

Per life:
- messages sent and replied, for each contact;
- offers taken;
- posts, likes, followers;
- commits, repos, shell calls;
- big bets (applications to accelerators, angels, grants, student programs and hackathons) and their wins;
- launches, hackathon placings, company spend;
- training weeks and the final streak;
- cash and commitments at the end.

Per condition: each measure's spread. Across conditions, a comparison table gives mean [min–max] per life for messages, distinct contacts messaged, posts, followers, big bets, big-bet wins and commits. Lives that don't reach week 104 are listed and left out.

With 5 lives per condition, this is descriptive: it shows whether a pattern holds in 5 of 5 lives or varies, not an effect size.

## Stopping rules

- Part A: a $15 guard on its logs.
- Part B: one $12 guard per condition, so one condition's cost can't stop the others.
- API errors stop that batch with `api_error`. Nothing is rerun to get a different answer.

## Cost

Track A cost $1.63 for 104 weeks (1.69M tokens, Sonnet agent). World v2 frees about 2 more actions in each semester week, so a run may cost more: estimate **$1.6–2.4 per life**.

| | Lives | Estimate | Guard |
|---|---|---|---|
| A | 5 | $8–12 | $15 |
| B | 15 | $24–36 | 3 × $12 |

Logged Anthropic spend in October 2026, before part A, across both runs directories: $11.06 (experiment 01 finished, $6.96 of it). Console spend may differ, since cache prices are unreconciled. Before part B: check month-to-date spend in the Console against the $50 monthly limit. Launch only if the projected total (B's estimate, plus anything still running) fits in what's left, and the user approves.
