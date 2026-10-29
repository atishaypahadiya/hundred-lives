# hundred-lives

Run one LLM persona many times through a simulated life and compare the paths. Design: README.md and docs/index.html (the project page; its persona, tool table, and loop are the spec).

## Status

v0: one agent, one persona, no containers. Tools: `files`, `apply`, `gig`, `leave`, `wait`. No shell, browse, or message yet. `sandbox/` is an empty placeholder.

## Layout

```
agents/personas/*.yaml   persona files; maya.yaml is the reference persona
world/openings.yaml      the world: ~30 fictional openings (jobs, accelerators, grants, angel intros) + the gig
orchestrator/run.py      CLI and the turn loop (Run class); start here
orchestrator/backends.py Anthropic + Ollama behind one interface (conversation(), complete())
orchestrator/tools.py    tool schemas (Anthropic shape), argument validation, sandboxed Workspace
orchestrator/referee.py  world model v0: LLM estimates the odds, `draw` samples the outcome
orchestrator/world.py    loads and validates openings.yaml; renders the agent-facing catalog
orchestrator/memory.py   summary + recent-week digests, compressed every 13 weeks, citation check
analysis/summarize.py    per-run summary of JSONL logs + referee calibration (estimate vs base rate vs draws)
runs/                    <run_id>.jsonl logs and <run_id>/workspace/ (gitignored)
```

## Running

```bash
uv sync
OLLAMA_CONTEXT_LENGTH=16384 ollama serve &
uv run python -m orchestrator.run --persona agents/personas/maya.yaml --runs 3 --turns 20
uv run python analysis/summarize.py runs/maya-*.jsonl
```

- `--model ollama:<model>` (default `ollama:qwen3:8b`) or `claude-*` / `anthropic:<model>`. `--referee-model` and `--summary-model` default to `--model`. `--world` defaults to `world/openings.yaml`. `--seed` (default 0) is the base seed for outcome draws; run *i* uses `seed + i`, so by default the seed is the run index.
- `--max-tokens` is the **per-run token budget** (every call's input + output + cache tokens, including referee and summary calls), not a per-response limit. Before each call the run checks a conservative estimate (prompt chars / 3 + max output) against what's left and ends with `run_end.reason = "token_budget"` instead of overshooting.
- `--turns` caps weeks; it can't exceed the persona's `horizon`.
- Ollama's default context is small and truncates silently, so always start it with `OLLAMA_CONTEXT_LENGTH=16384`. `OLLAMA_BASE_URL` overrides `http://localhost:11434/v1`.
- Anthropic needs `ANTHROPIC_API_KEY` in `.env` (or an `ant auth login` profile). Ollama runs are free; Anthropic runs cost money, so ask before launching them.

## How a week works

1. Commitments past their `ends_week` end. Pending decisions whose `reveal_week` has come are applied and shown as news.
2. If cash + income < expenses, the agent is **broke**: a `broke` event is logged, `wait` is refused until it takes a gig or applies to a `job` opening, and if the week ends without one a gig is forced (`gig.forced = true`). Cash is floored at $0; any uncovered amount is logged as `week_end.shortfall`.
3. The agent gets a **fresh conversation**: the fixed system prompt (persona + rules + openings catalog, cached) plus a week prompt rendered from state, memory, and last week's unseen errors. Earlier weeks are never replayed as raw messages, so no thinking blocks are ever edited or dropped.
4. The agent makes up to `MAX_CALLS_PER_WEEK` (8) actions. A gig uses `gig.time_cost` (6) of them. A text-only reply gets one nudge, then the week ends.
5. `apply(opening_id, details, attachments)` → the referee sees the opening, its `base_rate`, the submission, and the attached workspace files. It returns `estimated_chance`, `decision_in_weeks`, `message_if_success`, `message_if_failure`, `rationale`; **it never picks the outcome**. `referee.draw` samples it from the run's RNG: success with probability `estimated_chance`, otherwise ignored with probability `1 − reply_rate` (per opening), else failure. The seed, chance, and draws are in `referee_decision`. What a success *gives* (pay, stipend, grant, investment) is fixed by the opening, never by the referee. Ignored applications drop off the pending list after 12 weeks. The same opening can't be reapplied to while pending or for 26 weeks after a decision.
6. Cash += income − expenses. A digest line `[wN] …` goes into memory. On weeks divisible by 13 the summarizer rewrites the notes citing weeks as `[wN]`. `Memory.check` verifies every cited week had recorded events in this window, or was cited by the previous summary, and flags the summary otherwise (`memory_summary.grounding`). That catches invented weeks, not a real week that is misdescribed.

## Conventions

- **The referee sees only public facts**: `referee.PUBLIC_FIELDS` plus `starting_state.network` (no goal, no traits, no agent conversation). Keep it that way. Sensitivity runs change one trait and must not leak it into outcomes directly.
- **The agent never sees `horizon` or any `base_rate`.** Persona YAML is re-dumped with yaml, so comments in the file never reach the model.
- **Referee determinism:** the referee is a probability estimator at `temperature=0, seed=0` on Ollama, so the same application always gets the same odds, and replaying `referee_decision.referee_input` reproduces `raw_output` exactly. All outcome variance comes from the per-run RNG (`Run.rng`, used only for outcome draws). Current Claude models reject sampling parameters, so Anthropic referee estimates are *not* deterministic; the log then records `temperature: null`.
- **Calibration:** `estimated_chance` vs `base_rate` measures the referee's bias. Realized vs estimated only checks the dice; it matches in expectation by construction.
- **Base rates and reply rates in openings.yaml are illustrative guesses, not data.** Don't cite results as grounded until they're replaced with sourced rates (question 3).
- **Prompt and world wording are part of the experiment.** Any change to `SYSTEM`, the week prompt, the referee or summary prompts, or openings.yaml changes results. Note it in the commit message and don't compare runs across such changes.
- **Every event goes in the JSONL log**: one line per event, with `ts`, `run_id`, `week`, `type`. Types: `run_start` (full config, system prompt, tools), `week_start` (state, news, broke, carried errors, full week prompt), `llm_call` (role, model, usage, latency, running total), `agent_text`, `nudge`, `tool_call` (input, output truncated to 4000 chars), `tool_error` (tool, full input, error), `tool_errors_carried`, `referee_decision` (full referee input + raw output, estimate, base/reply rate, run seed, success/reply draws, outcome, referee temperature/seed), `outcome_applied`, `outcome_expired`, `commitment_ended`, `broke`, `gig`, `week_end` (reason, shortfall, digest, state), `memory_summary` (summary, window, grounding), `run_end` (reason, weeks, tokens, final state). When you add behavior, add a log event; analysis depends on logs alone.
- Tool argument errors come from `tools.validate` and must say what was wrong and what was expected; the agent sees them as tool results, or in next week's "Problems last week" if the week ended first.
- Money is in dollars. `weekly_expenses` defaults to $1,000 (a persona can override it), and starting cash is `savings_weeks × weekly_expenses`. Company investment is tracked separately (`company_funding`) and does not pay living expenses.
- Tool schemas are written once in Anthropic shape in `tools.py`. `OllamaConversation` converts them.
- Anthropic backend: thinking is always on for current models, so `max_tokens` is 16000 and `effort` is set explicitly (`backends.EFFORT`). Models in `FALLBACK_MODELS` use the beta endpoint with `fallbacks="default"`. Don't use forced `tool_choice` (it returns 400 on current models).
- Ollama backend: sends `reasoning_effort: "none"`. That is the only switch that turns qwen3 thinking off through the OpenAI-compatible endpoint (`/no_think` in a prompt does not); it takes calls from ~25s to ~1s.
- Python 3.12, uv, no package build (`[tool.uv] package = false`). Run modules with `uv run python -m orchestrator.<name>`. HTTP uses `httpx2` (what `anthropic` 2.x uses), not `httpx`.
