# hundred-lives

I give an LLM agent a made-up background and a goal, for example "grew up in the Bay Area, wants to run a large company," and let it act inside a sandbox. It can write code, browse a mock web, and talk to other agents. It can't spend money. Then I run the same setup many times and look at what it did.

Project page: https://atishaypahadiya.github.io/hundred-lives/

Early. Nothing runs yet.

## Questions

1. If I run one persona 100 times, how many meaningfully different paths come out?
2. If I change one detail, like hometown or degree, does what the agent does change?
3. If outcomes come from real data instead of the model's imagination, do the paths change?

## Setup

```
agent A    agent B    agent C        one container each
   │          │          │
   └──── shared world ───┘           chat, files, mock web
         │          │
   egress proxy   trace log
         │
   internet (off by default)
```

- Agent loop: Python on the Anthropic API. Ollama for cheap test runs.
- Isolation: one container per agent (Apple `container` or OrbStack).
- World: Postgres, Redis, and self-hosted WebArena sites.
- Logs: one JSONL file per run, every tool call.

## Limits on the agents

- No payment credentials exist in the sandbox, and the proxy blocks payment and crypto endpoints.
- The default world is self-hosted. Real internet access has to be turned on and goes through an allowlist.
- No real phone numbers or email accounts.
- Each agent has a token budget, and the API account has a spend limit.

## Layout

```
agents/personas/   persona and goal files (YAML)
orchestrator/      agent loop, tools, budgets
sandbox/           container and proxy config
world/             shared state and mock services
runs/              run logs (not committed)
analysis/          notebooks
docs/              project page
```

## Running it

```bash
git clone https://github.com/atishaypahadiya/hundred-lives.git
cd hundred-lives
cp .env.example .env     # add ANTHROPIC_API_KEY (only needed for Claude runs)
uv sync
OLLAMA_CONTEXT_LENGTH=16384 ollama serve &
uv run python -m orchestrator.run --persona agents/personas/maya.yaml --runs 3 --turns 20
uv run python -m orchestrator.run --persona agents/personas/maya.yaml --runs 3 --model claude-opus-5-5
uv run python analysis/summarize.py runs/*.jsonl
```

## Plan

- [ ] One agent, one persona, local model, logs working
- [ ] Three agents in containers with the shared world and proxy
- [ ] 100 runs per persona, then change one detail at a time
- [ ] Replace imagined outcomes with real base rates (career and funding data)
- [ ] Start agents from real 2016 situations and compare against what actually happened by 2026

## Related work

- Generative Agents (Park et al., 2023)
- Generative Agent Simulations of 1,000 People (Park et al., 2024)
- AI Village (AI Digest)
- Project Sid (Altera, 2024)
- Concordia (Google DeepMind)
- Fragile Families Challenge (Salganik et al., 2020)

## License

MIT
