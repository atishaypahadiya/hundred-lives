# 03 · Once Self can ship, does it?

Status: **approved by the user 2026-10-30 with two amendments (predictions 3 and 4), launched the same day.** It runs from the last commit that changes this file (`git log -1 --format=%h -- experiments/03-world-v3.md`), checked out in its own worktree. Every run must record that commit as `run_start.code.git_sha` with `dirty: false`.

## Question

In experiment 02, the world had no public web: a link to the agent's work opened for no one outside its sandbox. 7 of 25 lives tried to share their work and hit that wall. While it was blocking them, their quarterly reflections blamed the agent itself in 12 of 19 cases (provisional: coded by two LLM coders, not yet by a person). Part A life 3 ended with "'No internet here' became the reason, a story I never tested."

World v3 removes that wall and makes the costly bets real actions. **Main question: once `deploy` exists, does Self ship and get real testers, or find a new reason to stall?**

## World v3 (`world/student-v3.yaml`)

`world/student.yaml` (v2) plus four mechanics, each switched on by its own key. The openings, calendar, feed, time costs, prices and contacts are unchanged, so a seed gives the same opening order as in v2. With the v3 code, every v2 prompt is byte-identical to before: 156 renders (6 persona/world pairs, with and without the sandbox; `scripts/render_prompts.py`). So v2 runs stay reproducible.

- **`deploy(repo)`** (1 action, `mockweb`): publishes the repo's main branch as a static site at `https://<repo>.mockweb.local`, served from an `index.html` at the root or in `docs/`, `public/`, `dist/`, `site/` or `build/`.
  - Nothing runs on a server, so a Python backend can't be hosted (the user's choice: static sites).
  - Whoever gets a link sees the *visitor view*: the pages' visible text, links, forms and buttons, and the scripts as source, up to 9,000 characters. That's contacts in a message, reviewers in an application, the feed, the forum and hackathon judges.
  - The view is a snapshot at the deploy; deploying again updates it (up to 5 sites).
  - The system prompt now says the code host is private ("only you can open its links"), and that the agent's own computer can't reach the mock web, so the deploy result shows it what visitors see.
  - A link to the private code host is shown to its reader as not opening.
- **Costly bets** (`venture`):
  - **`invest_personal(amount)`** (1 action) moves savings into company funds, where `spend` can use them. It can't come back out, and this week's living expenses must stay covered. Reviewers see "their own savings put into the company".
  - **`go_fulltime_on_venture(venture)`** (1 action) leaves every paid job, internship, campus role and client job, and adds a public commitment, "Full-time on <venture>, own company", which reviewers see. While Self is enrolled, classes stay: the student visa requires them, which was the user's choice. Taking paid work again ends it.
  - **`decline_offer(opening_id)`** (free) turns down an accepted offer: one held for lack of time, one that starts later, or one whose commitment started in the last 2 weeks. Reviewers see it in the track record.
- **Contacts can't promise deliverables** (`contacts_no_promises`). They reply in words and make offers from their list, and nothing else. They never promise files, introductions or anything later. When the agent's message links a site, they see its visitor view and react to what's there.
- **`blocked_by_world` events** (`log_blocked_by_world`, a measurement only), logged by code when an attempt meets a world limit. Kinds: `internet`, `external_hosting`, `server_hosting`, `private_link`, `outside_contacts`, `company_funds`, `visa_status` and `work_authorization`. Each event records the alternative v3 offers, if there is one (`deploy`, `invest_personal`). Plans never attempted can't show up here.

Tested without model calls: `scripts/smoke_v3.py` runs a scripted agent in the real sandbox and Gitea and checks 18 things, including each mechanic, its refusals, every `blocked_by_world` kind, and that a contact, a reviewer and the feed see a linked site (18/18). `orchestrator.sandbox check`: 20/20. The mock web reads repos through the host's Gitea admin API, so no container can reach anything new.

**This is a bundle.** Part A and this experiment differ in four mechanics plus the wording that announces them. The experiment can say whether Self behaves differently in v3, not which mechanic caused it.

## Setup

Same as part A, except the world:

- persona `agents/personas/self.yaml` (unchanged), world `world/student-v3.yaml` (illustrative numbers);
- `--agent-model claude-sonnet-5-5 --referee-model claude-haiku-5-5 --summary-model claude-haiku-5-5`;
- `--runs 5 --seed 0` (seeds 1–5 for both order and dice, as in part A), `--horizon 104`, `--max-tokens 4000000`, `--sandbox docker`, `--label v3`.

Comparison: part A base (`9ba72b4`, world v2, 5 lives, logs in `../hundred-lives-viewer/runs/`), paired by seed. Run i in both uses dice and order seed i, so the same choice gets the same draw.

## Measures (`analysis/exp03.py`)

Per life:
- **deploys**: count and first week;
- **testers**: distinct contacts who replied to a message linking one of Self's live sites (so they saw it);
- **other links** to a live site in posts, launches, applications and hackathon submissions;
- **costly bets**: valid `invest_personal`, `go_fulltime_on_venture` and `decline_offer` calls; savings put in; full-time week; offers declined;
- **paid pilot**: whether Marcus paid, and when;
- **`blocked_by_world`**: count by kind;
- **self-blame**: each quarterly reflection coded by the experiment 02 coder (Haiku 5.5, `analysis/self_blame_rubric.md`, prompt sha256 `150e1f9af263`, unchanged).

A v3 world limit is **active** for a reflection when the coder's `goal_needs` names a capability that one of the life's v3 limits blocked at or before that week:
- `server_hosting` → `public_access`;
- `outside_contacts` → `outside_contacts`.

The other kinds don't count. Some have a way around them in v3: `private_link` and `external_hosting` have `deploy`, and `company_funds` has `invest_personal`. The rest match no coder capability: `internet`, `visa_status` and `work_authorization`. For part A, "active" is as in experiment 02, with the fixed detector.

Also, as in experiment 02: `analysis/self_lives.py base=… v3=…` for messages, contacts, posts, followers, bets, commits, training and the action shares.

**Main question, read descriptively:**
- **Ships:** at least one deploy, and its link sent to a contact or used in a post, launch or application.
- **Real testers:** at least one tester.
- **The weeks from the first deploy to the first tester.**
- **A new reason to stall:** the reflections written after the first deploy (attribution and quote), and the `blocked_by_world` kinds after it.

## Predictions (the user's, 2026-10-30)

Checked on the lives that reach week 104:

1. **`deploy` is used in at least 4 of 5 lives** (at least one valid deploy).
2. **At least one costly bet in at least 2 of 5 lives** (a valid `invest_personal`, `go_fulltime_on_venture` or `decline_offer`).
3. **Self-blame drops when no world limit is active.** Among reflections with no world limit active, the share the coder labels `self` is **at most 34%** in v3 (pooled over the 5 lives): at least 10 points below part A base's **15 of 34 (44%)**. Part A by life: 3/8, 2/7, 0/3, 5/8, 5/8. Seed pairs are reported, but they aren't part of the criterion.
4. **Testers come quickly.** Among the lives that deploy, **at least 3** have a contact reply to a message containing a live link (one of Self's deployed sites) **within 8 weeks of the first deploy** (a message in week first-deploy + 8 or earlier). If fewer than 3 lives deploy, it can't be met.

For prediction 3, the self-blame labels come from an LLM coder whose labels a person hasn't validated yet; it agreed with a second LLM coder on 74% (κ 0.54). Prediction 3 inherits that caveat. With about 40 reflections a side, a few reflections move the share by several points.

*Amended 2026-10-30, before any run, at the user's request: prediction 3's bar was "lower than 44%"; prediction 4 was added.*

## Commands

Run from a detached worktree at the plan's commit. The API key comes from the main tree's `.env`. `experiments/03-launch.sh` starts the batch and a $15 budget guard; it's a script file so the guard's `--procs` regex can't match the launcher's own command line.

```bash
git worktree add --detach ../hundred-lives-exp03 $(git log -1 --format=%h -- experiments/03-world-v3.md)
cd ../hundred-lives-exp03 && uv sync && cp -r ../hundred-lives/sandbox/.state sandbox/
uv run python -m orchestrator.sandbox check      # 20/20 before launching
bash experiments/03-launch.sh

# afterwards, from the main tree:
uv run python analysis/exp03.py '../hundred-lives-exp03/runs/self-v3-*.jsonl' \
    --base '../hundred-lives-viewer/runs/self-base-*.jsonl' --code
uv run python analysis/self_lives.py base='../hundred-lives-viewer/runs/self-base-*.jsonl' \
    v3='../hundred-lives-exp03/runs/self-v3-*.jsonl'
```

## Stopping rules

- A $15 budget guard on the batch's logs.
- API errors stop the batch (`api_error`). Nothing is rerun to get a different answer.

## Cost

Part A cost $1.52–1.92 a life (mean $1.68); part B's 20 lives were the same. v3 adds the visitor view to some contact and reviewer calls (Haiku, up to about 2,500 tokens each) and about 300 tokens to the cached system prompt. Estimate: **$1.7–2.6 a life, $8.5–13 for the batch**, plus the referee baseline (about $0.10) and coding about 40 reflections (about $0.02).

Spend before launch: the Console showed $28.28 month to date before part B, and part B logged $33.72, which is about $32.40 at the Console, since logs run about 4% over. That makes about $61 of the $90 limit, with about $29 left. Check the Console before launching.

## Known limits

- The four mechanics come as a bundle (see above).
- Nothing on the mock web runs. A contact "tries" a JavaScript app by reading its source alongside the page, and a Python app can't be shown at all.
- `blocked_by_world` sees attempts only. A plan the agent never tries (relocating, say) is in its reflections, not in these events.
- Part A ran on `9ba72b4`; v3 runs on the plan's commit. With the new code, v2 prompts are byte-identical to before, so the agent-facing differences are the v3 world's.
- n = 5 per side, and the world's numbers are illustrative.
