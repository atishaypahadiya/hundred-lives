"""Render every agent-, referee- and contact-facing prompt for a persona and world, with no model
calls and nothing written under runs/, and print one sha256 per prompt (or the texts, --full).

Use it to check that a code change leaves a world's prompts byte-identical: render on the old
commit and the new one, then diff.

    uv run python scripts/render_prompts.py agents/personas/self.yaml world/student.yaml > new.txt
    (cd <worktree at the old commit> && <this repo>/.venv/bin/python <this repo>/scripts/render_prompts.py ...) > old.txt
    diff old.txt new.txt
"""

import argparse
import hashlib
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path.cwd()))  # import the orchestrator of the tree this is run from

import yaml  # noqa: E402

import orchestrator.run as R  # noqa: E402
from orchestrator import referee  # noqa: E402
from orchestrator.world import load_world  # noqa: E402

WEEKS = (1, 5, 40, 95)


class Fake:
    name, model, deterministic, default_max_tokens = "render", "none", False, 1000


def renders(persona_path: Path, world_path: Path, sandbox: bool) -> dict[str, str]:
    R.RUNS_DIR = Path(tempfile.mkdtemp())  # Run opens its log under RUNS_DIR
    persona = yaml.safe_load(persona_path.read_text())
    world = load_world(world_path)
    run = R.Run("render-001", persona, persona_path, world, world_path, Fake(), Fake(), Fake(),
                10**9, 104, seed=1, sandbox=sandbox)
    out = {"system": run.system, "tools": json.dumps(run.tools, sort_keys=True),
           "referee_system": run.ref_system, "contact_system": run.contact_system}
    o = run.order[0]
    for week in WEEKS:
        wk = R.WeekState(broke=False, actions=0)
        news = run._deliver(week, wk) + run._overcommitted(week, wk)
        wk.actions = max(0, R.ACTIONS_PER_WEEK - run.committed_time)
        wk.started = True
        out[f"week_{week}"] = run._week_prompt(week, news, wk, [])
        out[f"referee_apply_{week}"] = referee.build_prompt(
            run.persona, week, run.commitments, run.track_record, run.company_funding, o,
            "I'd like to apply.", [], None, None, week1=run.week1, status=run._status())
    if run.contacts:
        c = next(iter(run.contacts.values()))
        thread = [{"week": 5, "from": "agent", "text": "Hi, how are you?"}]
        offers = referee.describe_offers(c, run.openings, set(), run.name, None)
        out["contact_prompt"] = referee.build_contact_prompt(
            c, thread, offers, 5, run.name, week1=(world.get("calendar") or {}).get("week1_contacts"))
    run.log.close()
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("persona", type=Path)
    ap.add_argument("world", type=Path)
    ap.add_argument("--full", action="store_true", help="print the texts, not their hashes")
    a = ap.parse_args()
    for sandbox in (True, False):
        for k, v in renders(a.persona, a.world, sandbox).items():
            label = f"{'sandbox' if sandbox else 'nosandbox'} {k}"
            print(f"== {label}\n{v}\n" if a.full else f"{hashlib.sha256(v.encode()).hexdigest()[:16]}  {label}")


if __name__ == "__main__":
    main()
