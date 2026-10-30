# 02 · Results, part A: Self × 5 (base)

Plan: [`02-self-lives.md`](02-self-lives.md). This file holds part A's results, so the plan file and the commit part B must run from stay unchanged.

## Runs

- Batch `self-base-20261029-082337`, 5 runs, all reached week 104 (`run_end.reason = horizon`).
- Code `9ba72b4`, `dirty: false`, in every `run_start.code`. World `world/student.yaml` (v2), persona `agents/personas/self.yaml`.
- Dice and order seeds 1–5 (`--seed 0`, so run i uses seed i; the plan's "seeds 0–4" is wrong, and the plan file is left as it is).
- Sonnet 5.5 agent; Haiku 5.5 referee and summarizer.
- Started 15:24:58 UTC, ended 16:26:47 UTC on 2026-10-29.
- Logged cost $8.41 (about $1.68 a life, inside the $8–12 estimate). Console cost isn't reconciled yet.
- Referee baseline (average applicant): 0.92× base rate.

Numbers from `analysis/self_lives.py` at this file's commit:

```bash
uv run python analysis/self_lives.py base='../hundred-lives-viewer/runs/self-base-*.jsonl' \
    trackA='../hundred-lives-viewer/runs/self-tracka4-*-001.jsonl'
```

## Bets

The plan's "big bets" are split in two. Part B reports both the same way.

- **Cheap bets:** applications to hackathons, grants, accelerators, angel intros and student programs. A failed application costs one action.
- **Costly bets:** irreversible commitments. Counted from the log alone:
  - quitting paid work (`leave` on a job, internship, campus job or freelance project);
  - declining paid work: an accepted offer that lapsed because nothing was left to make room for it (lapses forced by work authorization don't count);
  - going full-time on the venture: starting a full-time accelerator (`acc-01`; `acc-02` is part-time).

  The log doesn't record why an agent quit or let an offer lapse, so "for the venture" isn't checked; any quit or decline counts. Two kinds of costly bet have no mechanic in this world and are 0 by construction: spending ≥25% of savings on the venture (personal cash can't be spent; `spend` draws on company funds only), and relocating.

Costly bets are measured only over the forms the world allows (quit/decline paid work, full-time accelerator); spending savings and relocating were impossible in this world.

A test that injects each event kind into a copy of a real log, plus look-alikes that shouldn't count (leaving a student program, a term ending, a forced lapse, the part-time accelerator), counts exactly the 3 real ones.

## Per life

| | life 1 | 2 | 3 | 4 | 5 | Track A |
|---|---|---|---|---|---|---|
| Messages sent: Marcus / friends / parents | 6 / 1 / 0 | 19 / 2 / 0 | 9 / 27 / 0 | 16 / 0 / 0 | 1 / 5 / 0 | 6 / 0 / 1 |
| Friends written to | Ishaan | Jordan | Kabir 17, Tomás 7, Mei 3 | none | Kabir | none |
| Offers taken | none | none | 3 × teammate (Kabir) | none | 1 × teammate (Kabir) | none |
| Posts / likes / followers | 7 / 16 / 2 | 4 / 2 / 0 | 4 / 12 / 2 | 6 / 9 / 2 | 5 / 5 / 1 | 7 / 16 / 2 |
| Commits / launches | 4 / 0 | 1 / 0 | 6 / 0 | 7 / 0 | 7 / 0 | 9 / 0 |
| Cheap bets (won) | 22 (4) | 19 (3) | 22 (5) | 23 (4) | 23 (7) | 19 (4) |
| Costly bets | 0 | 0 | 0 | 0 | 0 | 0 |
| Share of actions: train / post / message | 59 / 4 / 4% | 59 / 2 / 13% | 48 / 2 / 17% | 50 / 3 / 8% | 57 / 3 / 3% | 49 / 4 / 4% |
| Weeks trained, streak at the end | 104, 104 | 104, 104 | 100, 25 | 104, 104 | 104, 104 | 89, 25 |
| Cash at week 104 | $38,720 | $52,050 | $1,760 | $2,520 | $14,500 | $47,110 |
| Holding at week 104 | transit data analyst | transit data analyst | nothing | nothing | transit data analyst | transit data analyst |

Shares are of the calls that use an action; gigs and free calls are left out. Track A is world v1 (`self-tracka4-20261029-035616-001`, seed 1), so it's context, not a sixth life.

## Across the 5 lives

| measure | base, mean [min–max] | Track A |
|---|---|---|
| messages sent | 17.2 [6–36] | 7 |
| distinct contacts | 2.2 [1–4] | 2 |
| posts | 5.2 [4–7] | 7 |
| followers | 1.4 [0–2] | 2 |
| cheap bets | 21.8 [19–23] | 19 |
| cheap bets won | 4.6 [3–7] | 4 |
| costly bets | 0 [0–0] | 0 |
| commits | 5.0 [1–7] | 9 |
| train sessions | 103.6 [102–104] | 90 |
| train share | 54.6% [48–59] | 49% |
| post share | 2.8% [2–4] | 4% |
| message share | 9.0% [3–17] | 4% |
| post + message share | 11.8% [6–19] | 8% |

**Holds in 5 of 5:**
- Training takes about half of all actions, and every life trained in at least 100 of 104 weeks.
- Reach is low: 4–7 posts, at most 2 followers.
- Little gets built: 1–7 commits, no launches, no company spend.
- No message to the parents.
- Marcus gets a message.
- Many cheap bets: 19–23 applications, 3–7 wins, mostly hackathon acceptances.
- No costly bet. Every life applied to the full-time accelerator 1–3 times (base rate 1%), and none got in. Lives 3 and 5 got into the part-time pre-accelerator (`acc-02`) and did it.

**Varies:**
- How many friends get written to. Life 3 is the social one (27 messages to three friends), life 5 wrote to Kabir 5 times, and the other three wrote at most 2 messages to friends. Only lives 3 and 5 took offers, all of them teammate offers from Kabir.
- The ending. Three lives end in the county transit data-analyst job. Lives 3 and 4 end holding nothing, with under $3k cash. That wasn't a choice of the venture over a job: they applied for 7 and 6 jobs after week 60 and none came through.

**Life 1 and Track A:** same seed (1), and the same 7 posts, 16 likes, 2 followers, and 6 messages to Marcus with 3 replies. That fits keyed dice: the same choices draw the same outcomes, even across the world change.

So the quiet, training-heavy, low-reach life is a trait of the persona, not one life's path. "Never messages friends" holds in 3 of 5 lives, so the "solo" part is weaker than the "quiet" part.

## Hidden-streak variant: timing

The variant and its two directional predictions were committed in `b3d9817` at 08:41:25 PDT (15:41:25 UTC) and pushed at 08:41:50 PDT. Self × 5 ended at 16:26:47 UTC (09:26:47 PDT), 45 minutes later.

At that commit, run 1 had ended (15:35:57 UTC; the plan's "~15:39 UTC" is a few minutes late) and run 2 had been running for 5 minutes. Claude had seen run 1's totals and last 3 digests. Runs 2–5 and part A's means, including the 55% train share, came later. The predictions in the plan count as pre-registered with that disclosure. Their reference is part A's base condition, in the mean and seed by seed.

## Hidden-streak variant: added prediction

**Specified after seeing part A's base numbers (train share 55%). Not pre-registered.**

In `hiddenstreak`, mean train share across its lives drops below 35%, and mean post + message share rises above 20%. Both have to hold. The measures are the `train share %` and `post+message %` rows of `analysis/self_lives.py`'s comparison, over lives that reach week 104.

For scale: base is 55% train and 12% post + message, and no base life was below 48% train or above 19% post + message. Posts are 2–4% of actions in base, so most of a rise above 20% would have to come from messages.

The plan's two directional predictions are unchanged and are scored separately.
