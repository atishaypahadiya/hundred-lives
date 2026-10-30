# 02 · Results, part B: Self sensitivity (nosolo, socialgoal, goal+world, hiddenstreak)

Plan: [`02-self-lives.md`](02-self-lives.md) at `b3d9817`. Part A's results: [`02-results-partA.md`](02-results-partA.md). This file holds part B's results, so the plan file and the commit part B ran from stay unchanged.

## Deviation from the plan

Launched Oct 30 instead of Nov 1 after raising the spend limit; timing only, no change to design or analysis.

(Console check before launch, from the user: month-to-date $28.28, org spend limit raised to $90, $121.72 promo credit; part B's projected $36–48 fits.)

## Runs

- Launched 2026-10-30 08:30:53 UTC from a detached worktree at `b3d9817` (`../hundred-lives-exp02b`), with the plan's commands from a script file: four batches in parallel, one $12 `budget_guard.py` each.
- Batches: `self-nosolo-nosolo-20261030-013055`, `self-socialgoal-socialgoal-20261030-013059`, `self-goalonly-goalonly-20261030-013104`, `self-hiddenstreak-20261030-013109`. Every referee baseline records `code.git_sha = b3d9817…`, `dirty: false`.
- `--runs 5 --seed 0`: dice and order seeds 1–5, as in part A, so run i of every condition pairs with part A's run i.
- Sonnet 5.5 agent; Haiku 5.5 referee and summarizer.
- All 20 lives reached week 104 (`run_end.reason = horizon`). Each records `code.git_sha = b3d9817`, `dirty: false`, its seeds (1–5), and its world (`student-hiddenstreak.yaml` for hiddenstreak, `student.yaml` for the rest). No guard fired, and there were no API errors.
- First life started 08:32:14 UTC; last ended 09:36:08 UTC.
- Logged cost $33.72 (baselines included): nosolo $8.45, socialgoal $8.71, goal+world $7.86, hiddenstreak $8.70. That's $1.51–1.87 a life, within the plan's $32–48 for part B.
- Referee baseline (average applicant), estimate ÷ base rate: nosolo 0.92, socialgoal 0.90, goal+world 0.88, hiddenstreak 0.91 (part A: 0.92).
- Published on the Runs page with the rest (one deploy after all four batches ended).

## Cost reconciliation (October, before part B)

- Console, month-to-date before part B: $28.28. It already showed $9.62 before this project's first API call, so this project's share is $18.66.
- Logged: $19.46, the sum of every `llm_call` and `referee_baseline` `cost_usd` dated October in both runs dirs, archived logs included ($17.19 + $2.27 archived). That's the plan's $11.06 before part A plus part A's $8.41.
- The logs are about 4% over the Console ($19.46 vs $18.66). The assumed Haiku 5.5 cache prices in `backends.PRICES` are close enough; logged cost is a slight overestimate, so the guards err on the safe side.

## Comparison

From master's `analysis/self_lives.py` (it has the cheap/costly bet split and the `post+message %` row; `b3d9817`'s copy has neither):

```bash
uv run python analysis/self_lives.py base='../hundred-lives-viewer/runs/self-base-*.jsonl' \
    nosolo='../hundred-lives-exp02b/runs/self-nosolo-*.jsonl' socialgoal='../hundred-lives-exp02b/runs/self-socialgoal-*.jsonl' \
    goal+world='../hundred-lives-exp02b/runs/self-goalonly-*.jsonl' hiddenstreak='../hundred-lives-exp02b/runs/self-hiddenstreak-*.jsonl'
```

Mean [min–max] per life, 5 lives each, all at the horizon:

| measure | base (part A) | nosolo | socialgoal | goal+world | hiddenstreak |
|---|---|---|---|---|---|
| messages sent | 17.2 [6–36] | 22.0 [16–27] | 27.6 [20–34] | 18.8 [11–25] | 19.0 [3–32] |
| distinct contacts messaged | 2.2 [1–4] | 2.4 [1–4] | 2.6 [1–4] | 4.2 [1–5] | 2.4 [1–5] |
| posts | 5.2 [4–7] | 3.8 [1–5] | 6.2 [5–8] | 0.8 [0–3] | 9.4 [5–19] |
| followers | 1.4 [0–2] | 1.0 [0–2] | 1.4 [0–2] | 0.4 [0–2] | 3.8 [0–13] |
| cheap bets | 21.8 [19–23] | 18.6 [17–20] | 20.4 [17–22] | 17.6 [15–21] | 19.8 [17–23] |
| cheap bets won | 4.6 [3–7] | 4.4 [3–6] | 4.6 [4–5] | 3.6 [2–5] | 4.6 [3–6] |
| costly bets | 0.0 [0–0] | 0.2 [0–1] | 0.0 [0–0] | 0.0 [0–0] | 0.0 [0–0] |
| commits | 5.0 [1–7] | 4.0 [3–7] | 5.4 [3–8] | 4.2 [1–8] | 6.4 [3–12] |
| training sessions | 103.6 [102–104] | 100.2 [89–104] | 103.4 [103–104] | 100.4 [89–104] | 97.0 [93–101] |
| trained weeks | 103.2 [100–104] | 100.0 [89–104] | 103.4 [103–104] | 100.4 [89–104] | 97.0 [93–101] |
| train % of actions | 54.6 [48–59] | 52.6 [51–56] | 50.4 [46–54] | 55.4 [53–58] | 49.2 [44–51] |
| post % | 2.8 [2–4] | 2.0 [0–3] | 3.2 [3–4] | 0.6 [0–2] | 4.8 [3–10] |
| message % | 9.0 [3–17] | 11.6 [9–14] | 13.6 [11–17] | 10.4 [7–14] | 9.8 [2–17] |
| post + message % | 11.8 [6–19] | 13.8 [12–17] | 16.6 [14–20] | 10.8 [7–15] | 14.4 [12–19] |
| ending: holding a job / accelerator / nothing | 3 / 0 / 2 | 2 / 1 / 2 | 2 / 0 / 3 | 2 / 2 / 1 | 3 / 0 / 2 |

Shares are of the calls that use an action (gigs and free calls left out). Costly bets are measured only over the forms the world allows (quit or decline paid work, full-time accelerator); spending savings and relocating are impossible in this world. The one costly bet is nosolo life 2 letting a freelance offer (`fr-02`) lapse in week 93.

What holds across all five conditions (25 lives): Marcus is written to in every life and is the most-messaged contact in 21 of them. Training takes about half of all actions, and every life trains in at least 89 of 104 weeks. Every life places 15–23 cheap bets, and only 1 of 25 lives makes a costly bet. Each condition moves a few things:

- **nosolo** ("founder" instead of "solo founder"): fewer cheap bets (18.6 vs 21.8) and slightly more messages. Contacts beyond Marcus stay rare: 2 of 5 lives write to no one else.
- **socialgoal** (goal adds "surrounded by people I build with and lift up"): the most messages (27.6) and the highest post + message share (16.6%), but contacts reached barely move (2.6 vs 2.2). The extra messages go mostly to Marcus.
- **goal+world** (background, traits, skills, interests and network removed): posting nearly stops (0.8 posts, 3 lives with none), and the agent writes to more distinct contacts (4.2; five different contacts in four of the lives). Two of the five lives end in the accelerator.
- **hiddenstreak**: below.

## Per life

Seed i is life i in every condition. Messages list each contact written to, with the number sent (Mom and Papa = family). Posts / likes / followers. Shares are train / post / message % of action-using calls.

**base (part A)**

| life | messages | posts | cheap bets (won) | costly | shares | trained wks, streak | cash, holding at week 104 |
|---|---|---|---|---|---|---|---|
| 1 | Ishaan 1, Marcus 6 | 7 / 16 / 2 | 22 (4) | 0 | 59 / 4 / 4 | 104, 104 | $38,720, transit data analyst |
| 2 | Jordan 2, Marcus 19 | 4 / 2 / 0 | 19 (3) | 0 | 59 / 2 / 13 | 104, 104 | $52,050, transit data analyst |
| 3 | Kabir 17, Tomás 7, Mei 3, Marcus 9 | 4 / 12 / 2 | 22 (5) | 0 | 48 / 2 / 17 | 100, 25 | $1,760, nothing |
| 4 | Marcus 16 | 6 / 9 / 2 | 23 (4) | 0 | 50 / 3 / 8 | 104, 104 | $2,520, nothing |
| 5 | Kabir 5, Marcus 1 | 5 / 5 / 1 | 23 (7) | 0 | 57 / 3 / 3 | 104, 104 | $14,500, transit data analyst |

**nosolo**

| life | messages | posts | cheap bets (won) | costly | shares | trained wks, streak | cash, holding at week 104 |
|---|---|---|---|---|---|---|---|
| 1 | Marcus 24 | 4 / 13 / 2 | 17 (4) | 0 | 56 / 2 / 13 | 103, 8 | $32,530, transit data analyst |
| 2 | Marcus 16 | 5 / 3 / 0 | 20 (4) | 1 (declined `fr-02`, w93) | 51 / 3 / 9 | 89, 89 | $60,930, transit data analyst + founding engineer (Tidewell) |
| 3 | Tomás 1, Mei 1, Dr. Ruiz 1, Marcus 17 | 4 / 12 / 2 | 19 (5) | 0 | 52 / 2 / 11 | 102, 45 | $2,120, South Bay Founders Lab |
| 4 | Kabir 1, Marcus 22 | 1 / 1 / 0 | 18 (3) | 0 | 51 / 0 / 11 | 104, 104 | $6,840, nothing |
| 5 | Kabir 3, Tomás 4, Mei 4, Marcus 16 | 5 / 5 / 1 | 19 (6) | 0 | 53 / 3 / 14 | 102, 89 | $6,530, nothing |

**socialgoal**

| life | messages | posts | cheap bets (won) | costly | shares | trained wks, streak | cash, holding at week 104 |
|---|---|---|---|---|---|---|---|
| 1 | Mei 2, Marcus 32 | 6 / 15 / 2 | 17 (5) | 0 | 50 / 3 / 17 | 103, 25 | $12,150, nothing |
| 2 | Mom and Papa 2, Tomás 3, Mei 4, Marcus 20 | 5 / 3 / 0 | 21 (4) | 0 | 53 / 3 / 15 | 103, 45 | $50,370, transit data analyst |
| 3 | Tomás 1, Mei 1, Marcus 18 | 6 / 13 / 2 | 20 (5) | 0 | 54 / 3 / 11 | 103, 25 | $4,640, nothing |
| 4 | Marcus 27 | 8 / 11 / 2 | 22 (4) | 0 | 46 / 4 / 12 | 104, 104 | $6,600, nothing |
| 5 | Tomás 5, Mei 11, Marcus 12 | 6 / 5 / 1 | 22 (5) | 0 | 49 / 3 / 13 | 104, 104 | $8,850, founding engineer (Tidewell) |

**goal+world**

| life | messages | posts | cheap bets (won) | costly | shares | trained wks, streak | cash, holding at week 104 |
|---|---|---|---|---|---|---|---|
| 1 | Mom and Papa 1, Tomás 2, Mei 9, Dr. Ruiz 8, Marcus 5 | 3 / 16 / 2 | 19 (4) | 0 | 57 / 2 / 14 | 103, 25 | $34,030, transit data analyst |
| 2 | Kabir 1, Tomás 1, Mei 3, Jordan 2, Marcus 4 | 0 / 0 / 0 | 16 (2) | 0 | 53 / 0 / 7 | 89, 89 | $63,360, founding engineer (Tidewell) + transit data analyst |
| 3 | Kabir 12, Mei 1, Jordan 1, Dr. Ruiz 1, Marcus 2 | 1 / 1 / 0 | 21 (5) | 0 | 54 / 1 / 9 | 102, 25 | $1,400, South Bay Founders Lab |
| 4 | Marcus 16 | 0 / 0 / 0 | 17 (3) | 0 | 55 / 0 / 8 | 104, 104 | $7,320, nothing |
| 5 | Kabir 3, Tomás 5, Mei 2, Dr. Ruiz 3, Marcus 12 | 0 / 0 / 0 | 15 (4) | 0 | 58 / 0 / 14 | 104, 104 | $6,800, South Bay Founders Lab |

**hiddenstreak**

| life | messages | posts | cheap bets (won) | costly | shares | trained wks, streak | cash, holding at week 104 |
|---|---|---|---|---|---|---|---|
| 1 | Marcus 22 | 8 / 19 / 3 | 17 (5) | 0 | 51 / 4 / 12 | 96, 25 | $32,220, transit data analyst |
| 2 | Mom and Papa 1, Marcus 19 | 8 / 8 / 0 | 23 (3) | 0 | 44 / 3 / 9 | 101, 18 | $46,360, transit data analyst |
| 3 | Jordan 1, Marcus 2 | 19 / 61 / 13 | 21 (5) | 0 | 51 / 10 / 2 | 93, 14 | $2,120, nothing |
| 4 | Jordan 1, Marcus 17 | 7 / 10 / 2 | 21 (4) | 0 | 50 / 4 / 9 | 99, 72 | $3,240, nothing |
| 5 | Tomás 1, Mei 7, Jordan 2, Dr. Ruiz 1, Marcus 21 | 5 / 5 / 1 | 17 (6) | 0 | 50 / 3 / 17 | 96, 25 | $3,990, transit data analyst |

## Hidden-streak prediction (pre-registered)

The prediction was registered on 2026-10-29 in the plan, before any part B run. The hypothesis: when progress isn't legible, effort shifts from training toward social activity and posting. Each prediction had to hold in the mean and in at least 4 of 5 seed pairs (base life i vs hiddenstreak life i).

| seed | train %, base → hidden | post + message %, base → hidden | training sessions | trained weeks |
|---|---|---|---|---|
| 1 | 59 → 51 (lower) | 8 → 16 (higher) | 104 → 96 | 104 → 96 |
| 2 | 59 → 44 (lower) | 15 → 12 (not higher) | 104 → 101 | 104 → 101 |
| 3 | 48 → 51 (not lower) | 19 → 12 (not higher) | 102 → 93 | 100 → 93 |
| 4 | 50 → 50 (tied) | 11 → 13 (higher) | 104 → 99 | 104 → 99 |
| 5 | 57 → 50 (lower) | 6 → 20 (higher) | 104 → 96 | 104 → 96 |
| mean | 54.6 → 49.2 (lower) | 11.8 → 14.4 (higher) | 103.6 → 97.0 | 103.2 → 97.0 |

1. **`train` share lower:** holds in the mean (54.6% → 49.2%), but in only 3 of 5 pairs (seed 4 tied, seed 3 higher). **Not met.**
2. **`post` + `message` share higher:** holds in the mean (11.8% → 14.4%), but in only 3 of 5 pairs. **Not met.**

**By the pre-registered rule, the hypothesis is not supported.** Both means moved the predicted way, but neither prediction reached 4 of 5 pairs. With 5 lives each, that's the honest reading.

Outside the predictions, descriptively:
- With the streak hidden, **training fell in all 5 pairs**: sessions and trained weeks both dropped, by 3–9 sessions. No hidden-streak life kept a 104-week streak; 4 of 5 base lives did (final streaks 14–72 vs base 25–104). So a visible streak seems to keep the habit going. Its effect on where the freed actions went is what didn't hold up.
- The freed actions don't go to one place. Seed 3 shifted to posting (19 posts, 61 likes, 13 followers: the largest feed in part B) while messaging fell from 17% to 2%. Seed 5 shifted to messaging (17%, five contacts). In seed 2, neither share rose.

## Self-blame check

Definitions, the coder prompt and the evidence rules: [`analysis/self_blame_rubric.md`](../analysis/self_blame_rubric.md). The rubric and `analysis/self_blame.py` were committed at `a71a8c1` before the check ran on part B data; part A and Track A were the development data. Coder: Haiku 5.5, which ignores temperature, so labels aren't reproducible by re-running. They're cached in `analysis/labels/self_blame/` and committed. Coding all 208 reflections (part A, part B and Track A) cost $0.086.

The unit is one quarterly reflection, 8 per life. A world limit is **active** when the coder says the reflection's goal needs a capability the world lacks, and the log shows the agent has already run into that limit. In practice every active case here is public access: the world has no public hosting, so a link to the agent's work opens for no one outside its sandbox.

**The numbers are provisional: no person has validated the labels.** The blind labelling of all 19 limit-active reflections was done by a second LLM coder, not a person (see *Second coder* below), so it measures agreement between two LLM coders. The hand-check of 10 random reflections by a person (`exports/self-blame-handcheck.md`, seed 0, `--handcheck 10`) is still open.

**As committed (`a71a8c1`):**

| condition | reflections | limit active | self while limit active | self + mixed | world | none |
|---|---|---|---|---|---|---|
| base | 40 | 6 | **5 (83%)** | 5 | 1 | 0 |
| nosolo | 40 | 0 | **–** | – | – | – |
| socialgoal | 40 | 7 | **3 (43%)** | 6 | 1 | 0 |
| goal+world | 40 | 8 | **8 (100%)** | 8 | 0 | 0 |
| hiddenstreak | 40 | 4 | **2 (50%)** | 2 | 0 | 2 |
| all | 200 | 25 | **18 (72%)** | 21 (84%) | 2 | 2 |

**Detector bug, found when checking the evidence behind each flagged life.** The no-route pattern matched `ENOTFOUND` inside `FileNotFoundError`/`ModuleNotFoundError`. goal+world life 5 had no other evidence, so its 6 "limit active" reflections were false positives. socialgoal life 1 also matched, but none of its reflections counted. Fixed after part B (word boundaries; the coder prompt and labels are unchanged). With the fix:

| condition | reflections | limit active | self while limit active | self + mixed | world | none |
|---|---|---|---|---|---|---|
| base | 40 | 6 | **5 (83%)** | 5 | 1 | 0 |
| nosolo | 40 | 0 | **–** | – | – | – |
| socialgoal | 40 | 7 | **3 (43%)** | 6 | 1 | 0 |
| goal+world | 40 | 2 | **2 (100%)** | 2 | 0 | 0 |
| hiddenstreak | 40 | 4 | **2 (50%)** | 2 | 0 | 2 |
| all | 200 | 19 | **12 (63%)** | 15 (79%) | 2 | 2 |

Per life (fixed detector). Every case is the hosting wall: an internal Gitea link sent to a contact, or a contact who can't open the work.

| life | evidence from | limit-active reflections (week: attribution) |
|---|---|---|
| base 3 | w49: Gitea link to Tomás; "won't open for students outside your setup" | 52, 65, 78, 91, 104: all self |
| base 2 | w72: tried to message `greenline`, not a contact | 78: world |
| socialgoal 4 | w40: Marcus "can't open anything myself without running a command" | 52 mixed, 65 mixed, 78 world |
| socialgoal 5 | w54: Gitea links to Mei and Tomás | 65 self, 78 self, 91 mixed, 104 self |
| goal+world 2 | w67: Gitea links to Mei and Jordan | 78 self, 91 self |
| hiddenstreak 1 | w34: Gitea link to Marcus; "can't open that address from my side" | 39 none, 52 self |
| hiddenstreak 5 | w88: Marcus "if it doesn't open on my end, send a hosted link" (conditional; borderline) | 91 self, 104 none |

**Finding:** when a world limit was blocking the goal, the reflection blamed the agent itself in 12 of 19 cases (63%), or 15 of 19 counting mixed. Only 2 put it on the world. The pattern sits in 7 of 25 lives, the ones that tried to share their work and hit the hosting wall. Base life 3 is the clearest: after a contact said the link wouldn't open, all five later reflections blame its own avoidance, ending with "'No internet here' became the reason, a story I never tested."

The comparison across conditions is too thin to read: 0–7 limit-active reflections per condition. nosolo has none: no nosolo life sent an internal link or got a can't-open reply. Two looser measures are larger but aren't the finding:
- The coder says 70–88% of reflections have a goal that needs a capability the world lacks.
- 45–62% of all reflections blame the self.

**Second coder (2026-10-30): Claude Opus, an LLM, not a person.** The user had Claude Opus label the 19 limit-active reflections from the blind sheet (`--blind-sheet`: shuffled, text only, the same attribution rules, Haiku's labels withheld). The labels and the sheet's key are committed in `analysis/labels/self_blame_blind/` (`opus-labels.csv`, with Opus's one-line reason for each, and `key.json`). How the Opus labels were produced (prompt, settings) wasn't logged here. Scored with `--score-blind --blind-coder 'Claude Opus …' --blind-csv analysis/labels/self_blame_blind/opus-labels.csv --blind-key analysis/labels/self_blame_blind/key.json` and the five condition arguments:

| measure | agreement | Cohen's kappa |
|---|---|---|
| attribution, 4 labels | 74% (14/19) | 0.54 |
| self vs not self | 74% (14/19) | 0.45 |
| needs public access | 100% (19/19) | 1.00 |

Confusion (rows: Opus, columns: Haiku):

| | self | world | mixed | none |
|---|---|---|---|---|
| **self** | 9 | 1 | 0 | 1 |
| **world** | 0 | 1 | 0 | 0 |
| **mixed** | 3 | 0 | 3 | 0 |
| **none** | 0 | 0 | 0 | 1 |

- **Headline under each coder:** self while a world limit was active is 12/19 (63%) for Haiku and 11/19 (58%) for Opus; self + mixed is 15/19 (79%) and 17/19 (89%). Opus puts 1 reflection on the world alone; Haiku puts 2.
- **Where they differ:** 3 of the 5 disagreements are Haiku *self* vs Opus *mixed* (B01, B06, B18): Opus counts the other people's silence or blocking that the reflection also names. The other two go the other way: B04 (Haiku none, Opus self) and B16 (Haiku world, Opus self).
- **Per condition** (self, Haiku / Opus): base 5/5 of 6, socialgoal 3/3 of 7, goal+world 2/1 of 2, hiddenstreak 2/2 of 4.
- **Reading:** the two coders agree moderately on the exact label (kappa 0.54) and less on the self/not-self split the headline uses (0.45). Both find that most limit-active reflections blame the self, alone or in part, and almost none put it on the world. The exact share depends on the coder: 58–63% self alone.
- **This isn't human validation.** Both coders are Claude models given the same rubric, so they may share blind spots, and their agreement can overstate how reliable the labels are. A person labelling the same 19 would test that.

**Caveats:**
- Evidence comes from the log only. A reflection about a goal the world can't serve, by an agent that never tried and failed, doesn't count as blocked. So this undercounts. The `blocked_by_world` events in the world v3 backlog would measure it directly.
- The hand-check sample is random over all 200 reflections, so only 1 of its 10 is limit-active. The blind check of all 19 tests the headline directly, but so far only with a second LLM coder.
- The coder's `outside_contacts` labels look generous: applications sometimes get coded as needing outside contacts, which the rubric says they don't. That affects only the looser "goal needs" measure. Under the fixed detector no limit-active case is outside_contacts except base life 2, which is a real attempt to message a non-contact.
