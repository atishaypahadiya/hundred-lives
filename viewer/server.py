"""Watch lives: a read-only viewer over runs/*.jsonl.

    uv run python -m viewer.server              # http://127.0.0.1:8711
    uv run python -m viewer.server --port 9000 --runs-dir runs
    uv run python -m viewer.server --runs-dir runs --runs-dir ../other/runs   # several dirs, one grid

Endpoints:
    GET /                                 the page (viewer/index.html)
    GET /api/runs                         one summary per run log (batch baselines left out)
    GET /api/runs/{run_id}/events         SSE: every normalized event from the start of the log,
                                          then new ones as the run writes them; `end` after run_end.
                                          A finished run and a live one take the same path.
    GET /api/runs/{run_id}/repos          exported repo bundles: commits, files, README
    GET /api/gateway?mode=dry|live        the live gateway's outbox, kill switch, and today's counts
    GET /api/gateway/token                the approval token (same-origin pages only)
    POST /api/gateway/{mode}/{item}/approve|reject, /api/gateway/{mode}/kill|unkill

Nothing here writes under runs/. The gateway endpoints append decisions to the gateway's own
log (gateway/state/<mode>/gateway.jsonl); they need the X-HL-Token header, which a cross-origin
page can't send without a CORS preflight this server never answers, and a loopback Origin. Bundles are read through a bare clone in a temp dir (no
checkout, hooks off), since anything in a repo is agent-controlled. Binds 127.0.0.1 only, and
rejects any Host header that isn't a loopback name, so a web page can't DNS-rebind its way in.
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
from starlette.middleware import Middleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response, StreamingResponse
from starlette.routing import Route

from gateway import policy as gw_policy
from gateway.store import DEFAULT_DIR as GATEWAY_DIR, Store, approve_token

from .normalize import _opening_types, normalize

ROOT = Path(__file__).resolve().parent.parent
PAGE = Path(__file__).resolve().parent / "index.html"
RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,200}$")
POLL_S = 0.5            # how often a live stream checks the log for new lines
SUMMARY_TAIL = 256_000  # bytes of the log's end read for a run summary
GIT_TIMEOUT_S = 10
MAX_COMMITS, MAX_FILES, MAX_README = 200, 500, 20_000

runs_dirs: list[Path] = [ROOT / "runs"]  # searched in order; a run id in two dirs is shown from the first
_summaries: dict[str, tuple[tuple, dict]] = {}  # run_id -> ((size, mtime), summary)


def _log_path(run_id: str) -> Path | None:
    if not RUN_ID.match(run_id) or run_id.endswith("-referee-baseline"):
        return None
    for d in runs_dirs:
        p = d / f"{run_id}.jsonl"
        if p.is_file() and p.resolve().parent == d.resolve():
            return p
    return None


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
    types = {}
    for r in _records(path):
        t = r.get("type")
        if t == "run_start":
            types = _opening_types(r)
            code = r.get("code") or {}
            s.update(persona=(r.get("persona") or {}).get("name"), models=r.get("models"), horizon=r.get("horizon"),
                     sandbox=r.get("sandbox"), order_seed=r.get("order_seed", r.get("seed")),
                     dice_seed=r.get("dice_seed", r.get("seed")), started=r.get("ts"),
                     pilot=not code or bool(code.get("dirty")), git_sha=(code.get("git_sha") or "")[:7])
        elif t == "week_end":
            state = r.get("state") or {}
            s["weeks"].append({"week": r.get("week"), "cash": state.get("cash"), "income": state.get("weekly_income"),
                               "funding": state.get("company_funding"), "commitment": kind(state.get("commitments")),
                               "followers": state.get("followers"),
                               "school": any(c.get("kind") == "academics" for c in state.get("commitments") or []),
                               # largest time cost first, so a tile can lead with the main commitment
                               "labels": [c.get("label") for c in sorted(state.get("commitments") or [], key=lambda c: -(c.get("time_cost") or 0))],
                               "job": any(types.get(c.get("opening_id")) in PAID_WORK for c in state.get("commitments") or []),
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


# Paid work, as world v3's `venture.paid_work_types` defines it; the grid's "in a job" means holding one of these.
PAID_WORK = {"job", "internship", "on_campus", "freelance"}


def kind(commitments: list | None) -> str:
    """The week's main commitment: accelerator, full-time job (5 actions), part-time, or none.
    Classes (kind "academics") aren't a job; a week with only classes is "none" plus school."""
    cs = [c for c in commitments or [] if c.get("kind") != "academics"]
    if any((c.get("opening_id") or "").startswith("acc-") for c in cs):
        return "accelerator"
    if not cs:
        return "none"
    return "full_time" if max(c.get("time_cost") or 0 for c in cs) >= 5 else "part_time"


async def page(request: Request):
    return FileResponse(PAGE, headers={"Cache-Control": "no-store"})


async def runs(request: Request):
    stems = {p.stem for d in runs_dirs for p in d.glob("*.jsonl")}
    paths = sorted(filter(None, map(_log_path, stems)), key=lambda p: p.stem, reverse=True)
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
    d = path.parent / path.stem / "repos"
    bundles = sorted(p for p in d.glob("*.bundle") if p.is_file() and not p.is_symlink()) if d.is_dir() else []
    return JSONResponse(await asyncio.gather(*(asyncio.to_thread(read_bundle, b) for b in bundles)))


LOOPBACK_HOSTS = ["127.0.0.1", "localhost", "[::1]"]  # TrustedHostMiddleware ignores the port
gateway_dir = GATEWAY_DIR
GATEWAY_MODES = ("dry", "live")
LOOPBACK_ORIGIN = re.compile(r"^http://(127\.0\.0\.1|localhost|\[::1\])(:\d+)?$")


def _gateway_store(mode: str) -> Store | None:
    return Store(gateway_dir / mode) if mode in GATEWAY_MODES else None


async def gateway(request: Request):
    store = _gateway_store(request.query_params.get("mode", "dry"))
    if store is None:
        return JSONResponse({"error": "mode is dry or live"}, status_code=400)
    items = sorted(store.outbox().values(), key=lambda i: i["created"], reverse=True)
    inbound = store.inbound()
    for i in items:  # the post a reply answers, for the reviewer
        if i.get("in_reply_to") in inbound:
            src = inbound[i["in_reply_to"]]
            i["replying_to"] = {"author": src.get("author_username"), "text": src.get("text", "")[:1000]}
    return JSONResponse({"killed": store.killed(), "items": items[:200], "sent_today": len(store.sent_today()),
                         "reads_today": store.reads_today(), "limits": {"posts": gw_policy.POSTS_PER_DAY,
                         "links": gw_policy.LINK_POSTS_PER_DAY, "reads": gw_policy.READS_PER_DAY}})


async def gateway_token(request: Request):
    return JSONResponse({"token": approve_token(gateway_dir)}, headers={"Cache-Control": "no-store"})


async def gateway_act(request: Request):
    origin = request.headers.get("origin")
    if origin and not LOOPBACK_ORIGIN.match(origin):
        return JSONResponse({"error": "cross-origin request"}, status_code=403)
    if request.headers.get("x-hl-token") != approve_token(gateway_dir):
        return JSONResponse({"error": "missing or wrong X-HL-Token"}, status_code=403)
    store = _gateway_store(request.path_params["mode"])
    if store is None:
        return JSONResponse({"error": "mode is dry or live"}, status_code=400)
    action, item_id = request.path_params["action"], request.path_params.get("item")
    if action not in (("approve", "reject") if item_id else ("kill", "unkill")):
        return JSONResponse({"error": f"unknown action {action!r}"}, status_code=404)
    if action == "kill":
        store.kill("from the viewer")
    elif action == "unkill":
        store.unkill()
    else:
        item = store.outbox().get(item_id or "")
        if item is None:
            return JSONResponse({"error": f"no item {item_id!r}"}, status_code=404)
        allowed = {"approve": ("pending",), "reject": ("pending", "approved")}[action]
        if item["status"] not in allowed:
            return JSONResponse({"error": f"{item_id} is {item['status']}; can't {action}"}, status_code=409)
        store.log("approved" if action == "approve" else "rejected", item_id=item_id, by="viewer")
    return JSONResponse({"ok": True})

app = Starlette(middleware=[Middleware(TrustedHostMiddleware, allowed_hosts=LOOPBACK_HOSTS)], routes=[
    Route("/", page),
    Route("/api/runs", runs),
    Route("/api/runs/{run_id}/events", events),
    Route("/api/runs/{run_id}/repos", repos),
    Route("/api/gateway", gateway),
    Route("/api/gateway/token", gateway_token),
    Route("/api/gateway/{mode}/{action:str}", gateway_act, methods=["POST"]),
    Route("/api/gateway/{mode}/{item}/{action:str}", gateway_act, methods=["POST"]),
])


def main(argv=None):
    global runs_dirs
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--port", type=int, default=8711)
    ap.add_argument("--runs-dir", type=Path, action="append",
                    help="a runs directory; repeat to show several in one grid (default: runs/)")
    ap.add_argument("--gateway-dir", type=Path, default=GATEWAY_DIR)
    args = ap.parse_args(argv)
    runs_dirs = [d.resolve() for d in args.runs_dir or runs_dirs]
    global gateway_dir
    gateway_dir = args.gateway_dir.resolve()
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
