# 03 · Results: once Self can ship, does it?

Plan: `experiments/03-world-v3.md` (committed before the run, last changed at `5d282a4`). Five lives of Self on world v3 (`world/student-v3.yaml`), paired by seed with part A base (world v2, `9ba72b4`). Every run recorded `code.git_sha = 5d282a4`, `dirty: false`. All five reached week 104; none hit an API error. Logged cost $8.73 ($1.64–1.90 a life), plus $0.05 to code the reflections; the $15 guard never fired. Logs: `../hundred-lives-exp03/runs/self-v3-20261030-033852-*.jsonl`. Every number here comes from:

```bash
uv run python analysis/exp03.py '../hundred-lives-exp03/runs/self-v3-*.jsonl' \
    --base '../hundred-lives-viewer/runs/self-base-*.jsonl' --code
uv run python analysis/self_lives.py base='../hundred-lives-viewer/runs/self-base-*.jsonl' \
    v3='../hundred-lives-exp03/runs/self-v3-*.jsonl'
```

World numbers are illustrative, n = 5 a side, and the four v3 mechanics came as one bundle, so this says how Self behaved in v3, not which mechanic did it.

## Answer to the main question

**Self shipped, and contacts did try it. It then found new reasons to stall, and it still blamed itself for most of them.**

- Every life deployed a site (weeks 4, 6, 14, 14, 18), and four of five got at least one contact to reply to a message linking it. Marcus was in all four.
- None of that turned into anything paid. There were no paid pilots and no launches. Only one life made a costly bet, and that one cost it nothing.
- After the first deploy, a new reason showed up: the static site can't do what the product needs (shared data, sync, persistence beyond the browser). 6 of 36 post-deploy reflections, in 3 of 5 lives, named it. No part A base reflection had (0 of 40). It's a real limit of v3. But no life tried anything server-side (`backend_needed` = 0 in all five), and none of the reflections that named it put the blame on the world alone (self in 3, mixed in 3).
- Self-blame went up, not down: 54% of v3 reflections, against 44% in part A base.

## Predictions

| | prediction | result | |
|---|---|---|---|
| 1 | `deploy` used in at least 4 of 5 lives | 5 of 5 | **met** |
| 2 | at least one costly bet in at least 2 of 5 lives | 1 of 5 | not met |
| 3 | self-blame at most 34% in reflections with no world limit active (base 15/34, 44%) | 21/39, 54% | not met; moved the other way |
| 4 | among lives that deploy, a contact replies to a live link within 8 weeks of the first deploy in at least 3 | 2 of 5 | not met |

**Prediction 2.** The one bet was life 3 going full-time on Conscious in week 97, after graduation. It held no paid work then, so it gave up no income (`left: []`). No life invested savings or declined an offer. (`self_lives.py`'s "costly bets" row is experiment 02's measure, which counts accelerator and angel applications: 0 in both conditions.)

**Prediction 3.** Coded by the experiment 02 self-blame coder (Haiku, rubric sha `150e1f9af263`, unchanged). Like every use of that coder, this is **provisional**: a person hasn't validated its labels. No v3 life had a single `blocked_by_world` event of any kind, so no v3 reflection had a world limit active. By seed pair (v3 vs base): 4/8 vs 3/8, 5/7 vs 2/7, 3/8 vs 0/3, 6/8 vs 5/8, 3/8 vs 5/8. Four of five pairs moved toward more self-blame.

**Prediction 4.** These are the replies to a message that linked a live site:

| life | first deploy | first link sent to a contact | first reply to a live link | contacts who replied |
|---|---|---|---|---|
| 001 | w6 | w14 | w27 (Tomás) | Tomás, Marcus |
| 002 | w14 | w14 | w14 (Marcus) | Marcus |
| 003 | w14 | w80 | none | none |
| 004 | w4 | w14 | w14 (Kabir), 10 weeks after the deploy | Kabir, Marcus |
| 005 | w18 | w18 | w19 (Marcus) | Marcus, Jordan, Tomás |

The delay was mostly Self's, not its contacts'. Lives 1 and 4 deployed early but didn't send the link until week 14. Life 3 deployed in week 14 and first sent the link in week 80.

## Stall reasons after the first deploy

*Added after launch, at the user's request, before any analysis.* This measure is descriptive, not a prediction. The rubric is `analysis/stall_reasons_rubric.md`. A second Haiku coder lists the reasons each reflection gives and puts each in a category. Attribution comes from the self-blame coder above, applied to the same reflection. Code decides from the category whether a reason matches a real v3 limit, and checks whether the life ran into it (a `blocked_by_world` event of a matching kind by that week). One more caveat: I wrote the definition of "new" below after first seeing the output. The first version compared only with each life's own pre-deploy reflections, and most lives had none, since they deployed before week 13.

A reason is **new** if its category appears neither in this life's reflections before its first deploy nor in its seed pair's part A base life. That pair lived in a world without deploy.

| life | new after the first deploy | real v3 limit? | ran into it? | how that reflection attributes it |
|---|---|---|---|---|
| 001 | **backend** in 4 of 8 (w52, 65, 78, 104): "Conscious is still a static page with localStorage"; "Static pages can't hold shared data, and Marcus won't put students on it until they can check in without copy-pasting links" | yes: static only | no: never tried server-side | self (w52, w65), mixed (w78, w104) |
| 001 | hosting in 1 of 8 (w104): "a static site can't be the test" | removed in v3 (the reason is really about backend) | no | mixed |
| 002 | **backend** in 1 of 6 (w26): "make the data persist beyond the browser" | yes: static only | no | self |
| 003 | money in 3 of 7 (w26, 91, 104): "cash sets how long I can wait" | yes, with a way around (invest_personal, never used) | no | self |
| 004 | skills in 2 of 8 (w13, 39): "Conscious is still thin, and I've been polishing its files more than testing it" | no | – | self |
| 004 | **backend** in 1 of 8 (w104): "live sync can't run on static hosting" | yes: static only | no | mixed |
| 005 | none: every category it named, its base pair had named too | | | |

Share of reflections naming each category (a reflection can name several):

| category | v3, after the first deploy (36) | part A base, all (40) | real v3 limit? |
|---|---|---|---|
| self | 72% | 70% | no |
| time | 44% | 35% | world rule (8 actions a week) |
| contacts | 33% | 38% | chance (reply rates) |
| money | 31% | 20% | yes, with a way around (invest_personal) |
| odds | 31% | 30% | chance |
| skills | 25% | 8% | no |
| **backend** | **17%** | **0%** | **yes: static only** |
| outside_people | 11% | 20% | yes: messages reach only contacts |
| hosting | 3% | 8% | removed in v3 |
| internet | 0% | 2% | yes: no route out |
| visa_rules | 0% | 5% | yes |
| other | 0% | 5% | no |

Deploy replaced one wall with another. "Can't put it online" fell from 8% to 3%, and "the site can't do what the product needs" rose from 0% to 17%. Skills rose too, from 8% to 25%, because once the product was in front of people Self called it thin. The new wall is real. But Self never once tried to cross it: no shell command started a server, no file held server code, and no deployed page called a backend.

`backend_needed` was 0 in every life. This experiment's code predates the kind, so `exp03.py` derived those counts from the logs with the detector in `orchestrator/limits.py`.

## Other measures (`self_lives.py`, mean per life, base → v3)

- Messages: 17.2 → 16.4. Distinct contacts written to: 2.2 → 3.0.
- Posts: 5.2 → 2.8. Followers: 1.4 → 0.6.
- Commits: 5.0 → 4.0.
- Cheap bets: 21.8 → 19.2.
- Training is unchanged: 103 weeks in both.
- Marcus got 6–17 messages in every v3 life. No other contact got more than 4.

## Limits

- n = 5 a side. A difference of a few reflections moves a share by several points.
- Both coders are one LLM each, and no person has checked their labels.
- The four mechanics are a bundle.
- The stall-reasons measure and its "new" definition came after the plan. The "new" definition came after a first look at the output.
- World numbers are illustrative.
