"""Watch lives: a read-only viewer over runs/*.jsonl.

    uv run python -m viewer.server              # http://127.0.0.1:8711
    uv run python -m viewer.server --port 9000 --runs-dir runs

Endpoints:
    GET /                                 the page (viewer/index.html)
    GET /api/runs                         one summary per run log (batch baselines left out)
    GET /api/runs/{run_id}/events         SSE: every normalized event from the start of the log,
                                          then new ones as the run writes them; `end` after run_end.
                                          A finished run and a live one take the same path.
    GET /api/runs/{run_id}/repos          exported repo bundles: commits, files, README

Nothing here writes under runs/. Bundles are read through a bare clone in a temp dir (no
checkout, hooks off), since anything in a repo is agent-controlled. Binds 127.0.0.1 only.
"""

import argparse
import asyncio
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response, StreamingResponse
from starlette.routing import Route

from .normalize import normalize

ROOT = Path(__file__).resolve().parent.parent
PAGE = Path(__file__).resolve().parent / "index.html"
RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,200}$")
POLL_S = 0.5            # how often a live stream checks the log for new lines
SUMMARY_TAIL = 256_000  # bytes of the log's end read for a run summary
GIT_TIMEOUT_S = 10
MAX_COMMITS, MAX_FILES, MAX_README = 200, 500, 20_000

runs_dir = ROOT / "runs"
_summaries: dict[str, tuple[tuple, dict]] = {}  # run_id -> ((size, mtime), summary)


def _log_path(run_id: str) -> Path | None:
    if not RUN_ID.match(run_id) or run_id.endswith("-referee-baseline"):
        return None
    p = runs_dir / f"{run_id}.jsonl"
    return p if p.is_file() and p.resolve().parent == runs_dir.resolve() else None


def _records(path: Path):
    with path.open("rb") as f:
        for line in f:
            if line.endswith(b"\n"):
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    pass


def summarize(path: Path) -> dict:
    """What the grid needs: identity, status, and one state per week (from week_end)."""
    st = path.stat()
    key = (st.st_size, st.st_mtime_ns)
    hit = _summaries.get(path.stem)
    if hit and hit[0] == key:
        return hit[1]
    s = {"run_id": path.stem, "status": "live", "weeks": [], "launches": 0, "repos": 0, "applications": 0,
         "shell": 0, "successes": 0, "cost_usd": 0.0, "tokens_used": 0}
    for r in _records(path):
        t = r.get("type")
        if t == "run_start":
            code = r.get("code") or {}
            s.update(persona=(r.get("persona") or {}).get("name"), models=r.get("models"), horizon=r.get("horizon"),
                     sandbox=r.get("sandbox"), order_seed=r.get("order_seed", r.get("seed")),
                     dice_seed=r.get("dice_seed", r.get("seed")), started=r.get("ts"),
                     pilot=not code or bool(code.get("dirty")), git_sha=(code.get("git_sha") or "")[:7])
        elif t == "week_end":
            state = r.get("state") or {}
            s["weeks"].append({"week": r.get("week"), "cash": state.get("cash"), "income": state.get("weekly_income"),
                               "funding": state.get("company_funding"), "commitment": kind(state.get("commitments")),
                               "labels": [c.get("label") for c in state.get("commitments") or []],
                               "gig": "took a gig" in (r.get("digest") or ""), "broke": False})
        elif t == "week_start" and r.get("broke"):
            s.setdefault("_broke", set()).add(r.get("week"))
        elif t == "referee_decision":
            s["applications"] += 1
        elif t == "outcome_applied" and r.get("outcome") == "success":
            s["successes"] += 1
        elif t == "launch":
            s["launches"] += 1
        elif t == "repo_created":
            s["repos"] += 1
        elif t == "shell":
            s["shell"] += 1
        elif t == "llm_call":
            s["cost_usd"], s["tokens_used"] = r.get("cost_usd_total") or 0.0, r.get("tokens_used") or 0
        elif t == "run_end":
            s.update(status="ended", reason=r.get("reason"), cost_usd=r.get("cost_usd"),
                     tokens_used=r.get("tokens_used"), ended=r.get("ts"))
    for w in s["weeks"]:
        w["broke"] = w["week"] in s.get("_broke", ())
    s.pop("_broke", None)
    s["last"] = s["weeks"][-1] if s["weeks"] else None
    _summaries[path.stem] = (key, s)
    return s


def kind(commitments: list | None) -> str:
    """The week's main commitment: accelerator, full-time job (5 actions), part-time, or none."""
    cs = commitments or []
    if any((c.get("opening_id") or "").startswith("acc-") for c in cs):
        return "accelerator"
    if not cs:
        return "none"
    return "full_time" if max(c.get("time_cost") or 0 for c in cs) >= 5 else "part_time"


async def page(request: Request):
    return FileResponse(PAGE, headers={"Cache-Control": "no-store"})


async def runs(request: Request):
    paths = sorted((p for p in runs_dir.glob("*.jsonl") if _log_path(p.stem)), key=lambda p: p.stem, reverse=True)
    return JSONResponse([summarize(p) for p in paths])


def _sse(ev: dict, event: str = "ev") -> bytes:
    return f"id: {ev.get('seq', '')}\nevent: {event}\ndata: {json.dumps(ev, separators=(',', ':'))}\n\n".encode()


async def events(request: Request):
    path = _log_path(request.path_params["run_id"])
    if not path:
        return Response("no such run", status_code=404)
    try:
        resume = int(request.headers.get("last-event-id") or request.query_params.get("from") or -1)
    except ValueError:
        resume = -1

    async def stream():
        seq, buf, ended, idle = 0, b"", False, 0
        with path.open("rb") as f:
            while not ended:
                chunk = f.read(1 << 20)
                if chunk:
                    buf += chunk
                    *lines, buf = buf.split(b"\n")
                    out = []
                    for line in lines:
                        try:
                            rec = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        seq += 1
                        if seq > resume:
                            out += [_sse(ev) for ev in normalize(rec, seq)]
                        ended = ended or rec.get("type") == "run_end"
                    if out:
                        yield b"".join(out)
                    continue
                if await request.is_disconnected():
                    return
                idle += 1
                if idle % 30 == 0:
                    yield b": keepalive\n\n"
                await asyncio.sleep(POLL_S)
        yield _sse({"seq": seq}, "end")

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


def _git(repo: Path, *args: str) -> str:
    r = subprocess.run(["git", "-C", str(repo), "-c", "core.hooksPath=/dev/null", *args],
                       capture_output=True, timeout=GIT_TIMEOUT_S)
    return r.stdout.decode("utf-8", "replace") if r.returncode == 0 else ""


def read_bundle(bundle: Path) -> dict:
    tmp = Path(tempfile.mkdtemp(prefix="hl-viewer-"))
    try:
        bare = tmp / "repo.git"
        r = subprocess.run(["git", "-c", "core.hooksPath=/dev/null", "clone", "--bare", "--quiet",
                            str(bundle), str(bare)], capture_output=True, timeout=GIT_TIMEOUT_S)
        if r.returncode != 0:
            return {"repo": bundle.stem, "error": r.stderr.decode("utf-8", "replace")[:500]}
        log = _git(bare, "log", "--all", f"--max-count={MAX_COMMITS}", "--date=iso-strict",
                   "--format=%x1e%H%x1f%an%x1f%ad%x1f%s", "--numstat")
        commits = []
        for block in log.split("\x1e")[1:]:
            head, _, stat = block.partition("\n")
            sha, author, date, subject = (head.split("\x1f") + ["", "", "", ""])[:4]
            ins = dels = files = 0
            for line in stat.strip().splitlines():
                a, d, *_ = line.split("\t") + ["", ""]
                files += 1
                ins += int(a) if a.isdigit() else 0
                dels += int(d) if d.isdigit() else 0
            commits.append({"sha": sha[:10], "author": author[:100], "date": date, "subject": subject[:300],
                            "files": files, "insertions": ins, "deletions": dels})
        tree = _git(bare, "ls-tree", "-r", "--name-only", "HEAD").splitlines()
        readme = next((f for f in tree if f.lower() in ("readme.md", "readme", "readme.txt")), None)
        text = _git(bare, "show", f"HEAD:{readme}")[:MAX_README] if readme else ""
        return {"repo": bundle.stem, "commits": commits, "files": tree[:MAX_FILES], "file_count": len(tree),
                "readme_path": readme, "readme": text, "bundle_bytes": bundle.stat().st_size}
    except subprocess.TimeoutExpired:
        return {"repo": bundle.stem, "error": "git timed out"}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


async def repos(request: Request):
    path = _log_path(request.path_params["run_id"])
    if not path:
        return Response("no such run", status_code=404)
    d = runs_dir / path.stem / "repos"
    bundles = sorted(p for p in d.glob("*.bundle") if p.is_file() and not p.is_symlink()) if d.is_dir() else []
    return JSONResponse(await asyncio.gather(*(asyncio.to_thread(read_bundle, b) for b in bundles)))


app = Starlette(routes=[
    Route("/", page),
    Route("/api/runs", runs),
    Route("/api/runs/{run_id}/events", events),
    Route("/api/runs/{run_id}/repos", repos),
])


def main(argv=None):
    global runs_dir
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--port", type=int, default=8711)
    ap.add_argument("--runs-dir", type=Path, default=runs_dir)
    args = ap.parse_args(argv)
    runs_dir = args.runs_dir.resolve()
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
