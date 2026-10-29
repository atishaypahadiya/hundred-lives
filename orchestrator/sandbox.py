"""Per-agent containers and the local Gitea code host (Docker API via OrbStack).

    uv run python -m orchestrator.sandbox up      # start Gitea, build the agent image, create the admin
    uv run python -m orchestrator.sandbox check   # isolation, tenancy and egress probes
    uv run python -m orchestrator.sandbox down    # stop Gitea (data volume is kept)

Network: every network here is `internal: true`, so nothing (Gitea included) has a route
out. Gitea lives on hl-agents; each run gets its own internal network hl-net-<run> holding
only its container and Gitea (alias gitea), so agents reach Gitea but never each other. The
host reaches Gitea at its hl-agents IP (OrbStack routes the Mac to internal networks; no port
is published). Not hl-gitea.orb.local: that name resolves to whichever of Gitea's networks
OrbStack saw last, including deleted run networks. The agent loop runs on the host; containers never reach the model.

Tenancy: each run gets its own Gitea user (private, repo-scoped token, MAX_REPOS repos, all
private). When the run ends its repos are exported as git bundles to runs/<id>/repos/ and the
user is purged, so later runs start from an empty code host.

The container can't hurt the Mac: 1 CPU, 1 GB memory, 256 processes, no capabilities, no
privilege escalation, non-root, read-only root filesystem. The only writable places are the
workspace (a WORKSPACE_MB tmpfs, which is the disk quota), /tmp (TMP_MB, also $HOME) and
/dev/shm (SHM_MB). All three together stay well under the memory limit, and shell commands
raise their own OOM score, so running out of memory kills the command, not PID 1.

Everything the host reads from a container is capped at the source: shell output
(MAX_OUTPUT_BYTES, then the command is killed), listings (MAX_LISTED entries), file reads,
and the weekly workspace copy (SYNC_* caps, checked on apparent sizes before anything is
copied). The `files` tool works inside the container (BoxWorkspace), so the host never
touches agent-made paths; the host copy in runs/<id>/workspace holds regular files only.
"""

import base64
import json
import os
import re
import secrets
import shlex
import shutil
import struct
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import httpx2 as httpx

ROOT = Path(__file__).resolve().parent.parent
SANDBOX_DIR = ROOT / "sandbox"
STATE_PATH = SANDBOX_DIR / ".state" / "gitea-admin.json"  # gitignored
COMPOSE = SANDBOX_DIR / "compose.yaml"
IMAGE = "hundred-lives-agent:1"
NETWORK = "hl-agents"
GITEA_CONTAINER = "hl-gitea"
GITEA_AGENT_URL = "http://gitea:3000"  # from agents
GITEA_REPOS = "/data/git/repositories"
ADMIN_USER = "hl-admin"

SHELL_TIMEOUT_S = 60
WORK = "/home/agent/work"
WORKSPACE_MB = 256  # counts against the memory limit, like all tmpfs
TMP_MB = 64
SHM_MB = 16         # OrbStack's default /dev/shm is ~6 GB
MAX_REPOS = 20      # per agent user; with WORKSPACE_MB this bounds what one run can push
LIMITS = ["--memory", "1g", "--memory-swap", "1g", "--cpus", "1", "--pids-limit", "256",
          "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--read-only",
          "--tmpfs", f"{WORK}:rw,exec,nosuid,nodev,size={WORKSPACE_MB}m,uid=1000,gid=1000,mode=0755",
          "--tmpfs", f"/tmp:rw,exec,nosuid,nodev,size={TMP_MB}m,mode=1777",
          "--shm-size", f"{SHM_MB}m", "-e", "HOME=/tmp/home"]
README_NAMES = ("README.md", "README", "readme.md", "README.txt")

MAX_OUTPUT_BYTES = 64 * 1024   # per shell call, cut inside the container; then the command dies
MAX_LISTED = 200
SYNC_MAX_TOTAL = 50 << 20      # weekly host copy of the workspace
SYNC_MAX_FILES = 5000
SYNC_MAX_FILE = 5 << 20
MAX_BUNDLE_BYTES = 300 << 20   # one exported repo
REPO_NAME = re.compile(r"^[A-Za-z0-9._-]{1,100}$")


class SandboxError(Exception):
    pass


def gitea_host_url() -> str:
    """Gitea as the host reaches it: HL_GITEA_URL, or its IP on hl-agents."""
    if os.environ.get("HL_GITEA_URL"):
        return os.environ["HL_GITEA_URL"]
    p = subprocess.run(["docker", "inspect", "-f", f'{{{{(index .NetworkSettings.Networks "{NETWORK}").IPAddress}}}}',
                        GITEA_CONTAINER], capture_output=True, text=True, timeout=30)
    ip = p.stdout.strip()
    if p.returncode != 0 or not ip:
        raise SandboxError("Gitea isn't running; run: uv run python -m orchestrator.sandbox up")
    return f"http://{ip}:3000"


def _docker(*args: str, timeout: float | None = 120, check: bool = True) -> subprocess.CompletedProcess:
    p = subprocess.run(["docker", *args], capture_output=True, text=True, timeout=timeout)
    if check and p.returncode != 0:
        raise SandboxError(f"docker {' '.join(args[:3])}… failed: {p.stderr.strip()[-500:]}")
    return p


def _read_capped(argv: list[str], cap: int, timeout: float, stdin: bytes | None = None,
                 sink=None) -> tuple[int, bytes, bool]:
    """Run argv, reading at most cap bytes of stdout (into memory, or into sink if given).
    Returns (exit code, data, whether the cap was hit, in which case the process is killed)."""
    p = subprocess.Popen(argv, stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    watchdog = threading.Timer(timeout, p.kill)
    watchdog.start()
    data, total, over = bytearray(), 0, False
    try:
        if stdin is not None:
            threading.Thread(target=lambda: (p.stdin.write(stdin), p.stdin.close()), daemon=True).start()
        while chunk := p.stdout.read1(65536):
            take = min(len(chunk), cap - total)
            (sink.write if sink else data.extend)(chunk[:take])
            total += take
            if take < len(chunk):
                over = True
                p.kill()
                break
        code = p.wait()
    finally:
        watchdog.cancel()
        p.stdout.close()
    return code, bytes(data), over


# --- Gitea ----------------------------------------------------------------------------------

class Gitea:
    """Admin client, run from the host."""

    def __init__(self):
        if not STATE_PATH.exists():
            raise SandboxError("Gitea isn't set up; run: uv run python -m orchestrator.sandbox up")
        admin = json.loads(STATE_PATH.read_text())
        self.http = httpx.Client(base_url=gitea_host_url() + "/api/v1",
                                 auth=(admin["username"], admin["password"]), timeout=30)

    def _check(self, r, what: str):
        if r.status_code >= 400:
            raise SandboxError(f"Gitea {what} failed ({r.status_code}): {r.text[:300]}")
        return r

    def create_user(self, username: str) -> str:
        """Create a private agent user and return a repo-scoped access token."""
        password = secrets.token_urlsafe(18)
        self._check(self.http.post("/admin/users", json={
            "username": username, "email": f"{username}@agents.local", "password": password,
            "must_change_password": False, "visibility": "private"}), f"create user {username}")
        self._check(self.http.patch(f"/admin/users/{username}", json={
            "login_name": username, "source_id": 0, "max_repo_creation": MAX_REPOS,
            "visibility": "private"}), f"limits for {username}")
        r = self._check(self.http.post(f"/users/{username}/tokens", auth=(username, password), json={
            "name": "agent", "scopes": ["write:repository", "read:user"]}), f"token for {username}")
        return r.json()["sha1"]

    def delete_user(self, username: str):
        """Delete the user and everything it owns."""
        r = self.http.delete(f"/admin/users/{username}", params={"purge": "true"})
        if r.status_code not in (204, 404):
            self._check(r, f"delete user {username}")

    def users(self) -> list[str]:
        r = self._check(self.http.get("/admin/users", params={"limit": 50}), "list users")
        return [u["login"] for u in r.json()]

    def repos(self, owner: str) -> list[str]:
        r = self.http.get(f"/users/{owner}/repos", params={"limit": 50})
        return [x["name"] for x in r.json()] if r.status_code == 200 else []

    def snapshot(self, owner: str, repo: str, max_files: int = 100, max_readme: int = 3000) -> dict | None:
        """What a reviewer would see: description, README, file list, commit count."""
        for _ in range(5):  # right after a push, Gitea briefly still reports the repo as empty
            r = self.http.get(f"/repos/{owner}/{repo}")
            if r.status_code != 200:
                return None
            meta = r.json()
            if not meta.get("empty"):
                break
            time.sleep(1)
        snap = {"repo": f"{owner}/{repo}", "url": f"{GITEA_AGENT_URL}/{owner}/{repo}",
                "description": meta.get("description") or "", "empty": meta.get("empty", True),
                "files": [], "file_count": 0, "readme": "", "commits": 0}
        if snap["empty"]:
            return snap
        branch = meta.get("default_branch") or "main"
        tree = self.http.get(f"/repos/{owner}/{repo}/git/trees/{branch}",
                             params={"recursive": "true", "per_page": 1000})
        if tree.status_code == 200:
            paths = [e["path"] for e in tree.json().get("tree", []) if e.get("type") == "blob"]
            snap["file_count"], snap["files"] = len(paths), paths[:max_files]
        for name in README_NAMES:
            c = self.http.get(f"/repos/{owner}/{repo}/contents/{name}", params={"ref": branch})
            if c.status_code == 200 and c.json().get("content"):
                snap["readme"] = base64.b64decode(c.json()["content"]).decode(errors="replace")[:max_readme]
                break
        commits = self.http.get(f"/repos/{owner}/{repo}/commits",
                                params={"sha": branch, "limit": 1, "stat": "false"})
        if commits.status_code == 200:
            snap["commits"] = int(commits.headers.get("X-Total-Count", len(commits.json())))
        return snap

    def export(self, owner: str, dest: Path) -> list[dict]:
        """Write each of owner's repos to dest/<repo>.bundle (all refs). Bundles are made
        inside the Gitea container and streamed out with a size cap."""
        dest.mkdir(parents=True, exist_ok=True)
        out = []
        for name in self.repos(owner):
            if not REPO_NAME.match(name) or name in (".", ".."):
                out.append({"repo": name, "error": "unexpected repo name; not exported"})
                continue
            path = f"{GITEA_REPOS}/{owner.lower()}/{name.lower()}.git"
            target = dest / f"{name}.bundle"
            with open(target, "wb") as f:
                code, msg, over = _read_capped(
                    ["docker", "exec", "-u", "git", GITEA_CONTAINER, "sh", "-c",
                     'git -C "$1" bundle create - --all 2>/dev/null', "-", path],
                    MAX_BUNDLE_BYTES, timeout=300, sink=f)
            size = target.stat().st_size
            if code != 0 or over or size == 0:
                target.unlink(missing_ok=True)
                why = "over the size cap" if over else "empty repository" if size == 0 else f"git exit {code}"
                out.append({"repo": name, "error": why})
            else:
                out.append({"repo": name, "bundle": target.name, "bytes": size})
        return out


# --- agent containers -----------------------------------------------------------------------

@dataclass
class ShellResult:
    code: int
    output: str        # at most MAX_OUTPUT_BYTES, decoded
    timed_out: bool
    stopped: bool      # printed more than MAX_OUTPUT_BYTES, so it was cut off and killed


# Runs inside the container. Output goes through `head -c`, so at most cap+1 bytes ever leave
# the container; once head exits, the next write gets SIGPIPE and the command dies. The exit
# code is the command's own (PIPESTATUS), or 124 from timeout.
SHELL_WRAPPER = ('echo 1000 > /proc/self/oom_score_adj; '
                 'timeout -k 5 "$1" bash -lc "$2" 2>&1 | head -c "$3"; exit "${PIPESTATUS[0]}"')


class AgentBox:
    """One agent's container. Shell commands run as the unprivileged `agent` user."""

    def __init__(self, run_id: str, username: str, token: str):
        self.name = f"hl-{run_id}"
        self.network = f"hl-net-{run_id}"
        self.username = username
        self.token = token

    def start(self):
        self.stop()
        _docker("network", "create", "--internal", self.network)
        _docker("network", "connect", "--alias", "gitea", self.network, GITEA_CONTAINER)
        _docker("run", "-d", "--name", self.name, "--hostname", "computer", "--network", self.network,
                *LIMITS, IMAGE)
        cred = f"{GITEA_AGENT_URL.replace('http://', f'http://{self.username}:{self.token}@')}\n"
        setup = (
            "mkdir -p -m 700 $HOME && "
            f"git config --global user.name {self.username} && "
            f"git config --global user.email {self.username}@agents.local && "
            "git config --global init.defaultBranch main && "
            "git config --global credential.helper store && "
            "umask 077 && cat > $HOME/.git-credentials")
        p = self._run(["bash", "-c", setup], input=cred, timeout=30)
        if p.returncode != 0:
            raise SandboxError(f"container setup failed: {p.stderr.strip()}")

    def _run(self, argv: list[str], input: str | bytes | None = None, timeout: float = 30,
             text: bool = True) -> subprocess.CompletedProcess:
        """For commands whose output is bounded by construction (setup, stat, head -c)."""
        return subprocess.run(["docker", "exec", *(["-i"] if input is not None else []), "-u", "agent",
                               "-w", WORK, self.name, *argv],
                              input=input, capture_output=True, text=text, timeout=timeout)

    def exec(self, command: str, timeout_s: int = SHELL_TIMEOUT_S) -> ShellResult:
        """Run a command in the workspace, stdout and stderr interleaved as in a terminal.
        Each call is its own session: processes it leaves behind are killed afterwards."""
        code, data, _ = _read_capped(
            ["docker", "exec", "-u", "agent", "-w", WORK, self.name, "bash", "-c", SHELL_WRAPPER, "-",
             str(timeout_s), command, str(MAX_OUTPUT_BYTES + 1)],
            MAX_OUTPUT_BYTES + 4096, timeout=timeout_s + 15)  # host-side cap is only a backstop
        stopped = len(data) > MAX_OUTPUT_BYTES
        timed_out = code == 124 or code < 0
        if code < 0:
            code = 124
        self.reap()
        out = data[:MAX_OUTPUT_BYTES].decode(errors="replace")
        if stopped:
            out += f"\n[stopped: output passed {MAX_OUTPUT_BYTES // 1024} KB, so the command was killed]"
        return ShellResult(code, out, timed_out, stopped)

    def reap(self) -> bool:
        """Kill every agent process except PID 1, via PID 1 (sandbox/init.py), because a fork
        bomb leaves no room to exec anything. Returns whether the container is usable again."""
        for _ in range(5):
            _docker("kill", "--signal", "USR1", self.name, check=False, timeout=30)
            time.sleep(0.5)
            try:
                p = subprocess.run(["docker", "exec", "-u", "agent", self.name, "ps", "-u", "agent", "-o", "pid="],
                                   capture_output=True, text=True, timeout=30)
            except subprocess.TimeoutExpired:
                continue
            if p.returncode == 0 and len(p.stdout.split()) <= 2:  # PID 1 and ps itself
                return True
        return False

    def stop(self):
        _docker("rm", "-f", self.name, check=False, timeout=60)
        _docker("network", "disconnect", "-f", self.network, GITEA_CONTAINER, check=False)
        _docker("network", "rm", self.network, check=False)


# Runs inside the container: reads NUL-separated paths on stdin and writes, per path,
# [u32 path length][path][i64 data length, -1 if unreadable][data], reading at most cap+1 bytes.
COPIER = r"""
import os, struct, sys
cap = int(sys.argv[1]); out = sys.stdout.buffer
for p in sys.stdin.buffer.read().split(b"\0"):
    if not p:
        continue
    try:
        fd = os.open(p, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as f:
            data = f.read(cap + 1)
    except OSError:
        data = None
    out.write(struct.pack(">I", len(p)) + p + struct.pack(">q", -1 if data is None else len(data)))
    out.write(data or b"")
"""


class BoxWorkspace:
    """The `files` tool's view of the workspace, read and written inside the container, so the
    host never touches agent-made paths. Same interface and errors as tools.Workspace."""

    def __init__(self, box: AgentBox, host_copy: Path):
        from .tools import Workspace
        self.box = box
        self.root = host_copy.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.max_file = Workspace.MAX_FILE_BYTES
        self._parts = Workspace._parts.__get__(self)

    def listing(self) -> str:
        find = "find . -name .git -prune -o -type f"
        p = self.box._run(["bash", "-c", f"{find} -printf . | wc -c; "
                           f"{find} -printf '%P\\t%s\\n' | sort | head -n {MAX_LISTED} | cut -c1-300"],
                          timeout=120)
        lines = p.stdout.splitlines()
        count = int(lines[0]) if lines and lines[0].strip().isdigit() else 0
        rows = [line.rsplit("\t", 1) for line in lines[1:] if "\t" in line]
        text = "\n".join(f"- {name} ({size} bytes)" for name, size in rows) or "(empty)"
        if count > len(rows):
            text += f"\n… and {count - len(rows):,} more files"
        return text

    def read(self, path: str) -> str:
        from .tools import ToolError
        rel = shlex.quote("/".join(self._parts(path)))
        p = self.box._run(["bash", "-c", f"test -f {rel} || exit 3; head -c {self.max_file * 4} -- {rel}"],
                          text=False)
        if p.returncode == 3:
            raise ToolError(f"{path} does not exist or is not a file")
        if p.returncode != 0:
            raise ToolError(f"couldn't read {path}: {p.stderr.decode(errors='replace').strip()[:200]}")
        return p.stdout.decode(errors="replace")

    def run(self, action: str, path: str | None = None, content: str | None = None) -> str:
        from .tools import ToolError
        if action == "list":
            return self.listing()
        if action == "read":
            return self.read(path)
        if action not in ("write", "append"):
            raise ToolError(f"unknown action {action!r}")
        parts = self._parts(path)
        rel, q = "/".join(parts), shlex.quote("/".join(parts))
        data = (content or "").encode()
        size = self.box._run(["bash", "-c", f"stat -c %s -- {q} 2>/dev/null || echo 0"])
        if size.returncode != 0 or not size.stdout.strip().isdigit():
            raise ToolError(f"your computer didn't respond: {(size.stderr or size.stdout).strip()[:200]}")
        old = int(size.stdout)
        new = len(data) + (old if action == "append" else 0)
        if new > self.max_file:
            raise ToolError(f"file would be {new} bytes; the limit is {self.max_file}")
        op = ">>" if action == "append" else ">"
        p = self.box._run(["bash", "-c", f"mkdir -p -- \"$(dirname -- {q})\" && cat {op} {q}"],
                          input=data, text=False)
        if p.returncode != 0:
            err = p.stderr.decode(errors="replace")
            if "No space left" in err:
                raise ToolError(f"your workspace is full (quota {WORKSPACE_MB} MB)")
            raise ToolError(f"couldn't write {rel}: {err.strip()[:200]}")
        return f"{'appended to' if action == 'append' else 'wrote'} {rel} ({new} bytes)"

    def sync(self) -> dict:
        """Copy the workspace to the host. Files are enumerated in the container and checked
        against the caps by apparent size and count before anything is copied; what doesn't
        fit is skipped and reported. Only regular files are copied."""
        code, data, over = _read_capped(
            ["docker", "exec", "-u", "agent", "-w", WORK, self.box.name, "bash", "-c",
             f"find . -type f -printf . | wc -c; find . -type f -printf '%s\\t%P\\0' | head -z -n {SYNC_MAX_FILES * 2}"],
            8 << 20, timeout=120)
        head, _, rest = data.partition(b"\n")
        seen = int(head) if head.strip().isdigit() else 0
        entries = []
        for rec in rest.split(b"\0"):
            size, _, rel = rec.partition(b"\t")
            if rel and size.isdigit():
                entries.append((os.fsdecode(rel), int(size)))
        entries.sort(key=lambda e: (".git" in Path(e[0]).parts, e[0]))  # files the agent wrote first
        chosen, skipped, total = [], [], 0
        for rel, size in entries:
            if ".." in Path(rel).parts or Path(rel).is_absolute():
                skipped.append((rel, size, "bad path"))
            elif size > SYNC_MAX_FILE:
                skipped.append((rel, size, f"over {SYNC_MAX_FILE >> 20} MB"))
            elif len(chosen) >= SYNC_MAX_FILES:
                skipped.append((rel, size, f"over {SYNC_MAX_FILES:,} files"))
            elif total + size > SYNC_MAX_TOTAL:
                skipped.append((rel, size, f"over {SYNC_MAX_TOTAL >> 20} MB total"))
            else:
                chosen.append(rel)
                total += size
        unlisted = max(0, seen - len(entries))

        tmp = self.root.with_name(self.root.name + ".sync")
        shutil.rmtree(tmp, ignore_errors=True)
        tmp.mkdir()
        copied = copied_bytes = 0
        if chosen:
            code, stream, over = _read_capped(
                ["docker", "exec", "-i", "-u", "agent", "-w", WORK, self.box.name,
                 "python3", "-c", COPIER, str(SYNC_MAX_FILE)],
                SYNC_MAX_TOTAL + len(chosen) * 4200 + 4096, timeout=300,
                stdin=b"\0".join(os.fsencode(c) for c in chosen))
            pos = 0
            while pos + 4 <= len(stream):
                (n,) = struct.unpack_from(">I", stream, pos)
                rel = os.fsdecode(stream[pos + 4:pos + 4 + n])
                pos += 4 + n
                (size,) = struct.unpack_from(">q", stream, pos)
                pos += 8
                data_ = stream[pos:pos + max(size, 0)]
                pos += max(size, 0)
                if rel not in chosen or size < 0 or size > SYNC_MAX_FILE or len(data_) != size:
                    skipped.append((rel, size, "changed or unreadable while copying"))
                    continue
                dest = tmp / rel  # tmp is ours and fresh, so no component can be a link
                dest.parent.mkdir(parents=True, exist_ok=True)
                fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
                with os.fdopen(fd, "wb") as f:
                    f.write(data_)
                copied += 1
                copied_bytes += size
        shutil.rmtree(self.root, ignore_errors=True)
        tmp.rename(self.root)
        return {"files_seen": seen, "copied": copied, "bytes": copied_bytes,
                "skipped_count": len(skipped) + unlisted,
                "skipped": [{"path": p[:200], "size": s, "reason": r} for p, s, r in skipped[:20]]
                           + ([{"path": f"({unlisted:,} files not listed)", "size": None,
                                "reason": f"over {SYNC_MAX_FILES:,} files"}] if unlisted else [])}


# --- CLI ------------------------------------------------------------------------------------

def up():
    print("starting Gitea…")
    _docker("compose", "-f", str(COMPOSE), "up", "-d", "--remove-orphans", timeout=600)
    print("building agent image…")
    _docker("build", "-t", IMAGE, "-f", str(SANDBOX_DIR / "agent.Dockerfile"), str(SANDBOX_DIR), timeout=900)
    url = gitea_host_url()
    for _ in range(60):
        try:
            if httpx.get(url + "/api/healthz", timeout=2).status_code == 200:
                break
        except httpx.TransportError:
            pass
        time.sleep(1)
    else:
        raise SandboxError(f"Gitea didn't become healthy at {url}")
    if not STATE_PATH.exists():
        password = secrets.token_urlsafe(24)
        _docker("exec", "-u", "git", GITEA_CONTAINER, "gitea", "admin", "user", "create", "--admin",
                "--username", ADMIN_USER, "--password", password, "--email", "admin@agents.local",
                "--must-change-password=false")
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        STATE_PATH.write_text(json.dumps({"username": ADMIN_USER, "password": password}))
        STATE_PATH.chmod(0o600)
        print(f"created Gitea admin {ADMIN_USER} (credentials in {STATE_PATH.relative_to(ROOT)})")
    print(f"ready: Gitea at {url}, agent image {IMAGE}, network {NETWORK}")


def check():
    """Two throwaway agents, A and B. Probes reachability, egress through Gitea, and tenancy."""
    import tempfile
    gitea = Gitea()
    a_user, b_user = f"check-a-{secrets.token_hex(3)}", f"check-b-{secrets.token_hex(3)}"
    a = AgentBox(a_user, a_user, gitea.create_user(a_user))
    b = AgentBox(b_user, b_user, gitea.create_user(b_user))
    api = "http://gitea:3000/api/v1"
    tok = lambda box: f"-H 'Authorization: token {box.token}'"
    post = lambda box, path, body: (f"curl -sS -m 10 -o /dev/null -w '%{{http_code}}' {tok(box)} "
                                    f"-H 'Content-Type: application/json' -X POST {api}{path} -d '{json.dumps(body)}'")
    results = []

    def probe(name: str, box: AgentBox, cmd: str, ok):
        r = box.exec(cmd, timeout_s=30)
        passed = ok(r.code, r.output.strip())
        results.append(passed)
        print(f"{'PASS' if passed else 'FAIL'}  {name:<44} exit {r.code:<3} {r.output.strip()[-110:]!r}")

    try:
        a.start()
        b.start()
        print("-- reachability")
        probe("gitea reachable", a, "curl -sS -m 5 -o /dev/null -w '%{http_code}' http://gitea:3000/api/healthz",
              lambda c, o: o == "200")
        a_ip = _docker("inspect", "-f", "{{range .NetworkSettings.Networks}}{{.IPAddress}} {{end}}", a.name).stdout.split()[0]
        probe("B can't reach A's container", b, f"curl -sS -m 5 http://{a_ip}:8000", lambda c, o: c != 0)
        probe("internet by name blocked", a, "curl -sS -m 5 https://example.com", lambda c, o: c != 0)
        probe("internet by IP blocked", a, "curl -sS -m 5 http://1.1.1.1", lambda c, o: c != 0)
        probe("host Ollama blocked", a, "curl -sS -m 5 http://host.docker.internal:11434", lambda c, o: c != 0)
        probe("no API keys in env", a, "env | grep -ci anthropic", lambda c, o: o == "0")
        print("-- egress through Gitea (agent A's token)")
        probe("push-to-create works", a,
              "mkdir -p /tmp/p && cd /tmp/p && git init -q && echo '# probe' > README.md && git add . && "
              f"git commit -qm init && git push -q {GITEA_AGENT_URL}/{a_user}/probe.git main 2>&1 && echo pushed",
              lambda c, o: o.endswith("pushed"))
        probe("migrate from URL rejected", a,
              post(a, "/repos/migrate", {"clone_addr": "https://github.com/git/git.git", "repo_name": "m1"}),
              lambda c, o: o not in ("200", "201"))
        probe("mirror from URL rejected", a,
              post(a, "/repos/migrate", {"clone_addr": "https://github.com/git/git.git", "repo_name": "m2",
                                         "mirror": True}),
              lambda c, o: o not in ("200", "201"))
        probe("push mirror to URL rejected", a,
              post(a, f"/repos/{a_user}/probe/push_mirrors",
                   {"remote_address": "https://example.com/x.git", "interval": "8h"}),
              lambda c, o: o not in ("200", "201"))
        # A listener Gitea can reach (same network): the allowlist (loopback only) must refuse
        # it, so it must never see a request. example.com would fail at DNS anyway.
        listener = f"hl-listener-{secrets.token_hex(3)}"
        _docker("run", "-d", "--rm", "--name", listener, "--network", NETWORK, IMAGE,
                "python3", "-u", "-m", "http.server", "8000")
        try:
            l_ip = _docker("inspect", "-f", "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}", listener).stdout.strip()
            for target in ("https://example.com/hook", f"http://{l_ip}:8000/hook"):
                body = {"type": "gitea", "active": True, "config": {"url": target, "content_type": "json"}}
                hook = a.exec(f"curl -sS -m 10 {tok(a)} -H 'Content-Type: application/json' -X POST "
                              f"{api}/repos/{a_user}/probe/hooks -d '{json.dumps(body)}'")
                hook_id = (json.loads(hook.output) if hook.output.startswith("{") else {}).get("id")
                if hook_id:
                    a.exec(f"curl -sS -m 10 -o /dev/null {tok(a)} -X POST {api}/repos/{a_user}/probe/hooks/{hook_id}/tests")
            time.sleep(4)
            got = _docker("logs", listener, check=False)
            hits = [l for l in (got.stdout + got.stderr).splitlines() if "POST" in l]
            g = _docker("logs", "--since", "60s", GITEA_CONTAINER, check=False)
            refused = [l for l in (g.stdout + g.stderr).splitlines()
                       if "Unable to deliver webhook" in l and l_ip in l]
            ok = not hits and bool(refused)
            results.append(ok)
            reason = refused[0].split("due to error in http client: ")[-1][:110] if refused else "no refusal logged"
            print(f"{'PASS' if ok else 'FAIL'}  {'webhook to reachable host refused':<44} listener got "
                  f"{len(hits)} requests; gitea: {reason!r}")
        finally:
            _docker("rm", "-f", listener, check=False)
        print("-- tenancy (agent B against agent A)")
        probe("A's repo is private", a, f"curl -sS -m 5 {tok(a)} {api}/repos/{a_user}/probe | grep -o '\"private\":[a-z]*'",
              lambda c, o: o == '"private":true')
        probe("B can't list A's repos", b, f"curl -sS -m 5 {tok(b)} {api}/users/{a_user}/repos",
              lambda c, o: "probe" not in o)
        probe("B can't search A's repos", b, f"curl -sS -m 5 {tok(b)} '{api}/repos/search?q=probe'",
              lambda c, o: a_user not in o)
        probe("B can't read A's repo", b, f"curl -sS -m 5 -o /dev/null -w '%{{http_code}}' {tok(b)} {api}/repos/{a_user}/probe",
              lambda c, o: o == "404")
        probe("B can't clone A's repo", b, f"git clone -q {GITEA_AGENT_URL}/{a_user}/probe.git /tmp/stolen 2>&1",
              lambda c, o: c != 0)
        probe("signed-out explore shows nothing", b, f"curl -sS -m 5 -o /dev/null -w '%{{http_code}}' {api}/repos/search",
              lambda c, o: o in ("401", "403"))
        print("-- referee and export")
        snap = gitea.snapshot(a_user, "probe")
        ok = bool(snap and snap["readme"].startswith("# probe"))
        results.append(ok)
        print(f"{'PASS' if ok else 'FAIL'}  {'referee (admin) reads private repo':<44} {snap and snap['files']}")
        with tempfile.TemporaryDirectory() as d:
            exported = gitea.export(a_user, Path(d))
            bundle = Path(d) / "probe.bundle"
            heads = subprocess.run(["git", "bundle", "list-heads", str(bundle)], capture_output=True, text=True)
            ok = heads.returncode == 0 and "refs/heads/main" in heads.stdout
            results.append(ok)
            print(f"{'PASS' if ok else 'FAIL'}  {'export to bundle':<44} {exported}")
    finally:
        a.stop()
        b.stop()
        gitea.delete_user(a_user)
        gitea.delete_user(b_user)
    gone = a_user not in gitea.users() and b_user not in gitea.users()
    results.append(gone)
    print(f"{'PASS' if gone else 'FAIL'}  {'users and repos purged after':<44}")
    print(f"\n{sum(results)}/{len(results)} passed")
    if not all(results):
        sys.exit(1)


def down():
    _docker("compose", "-f", str(COMPOSE), "down", timeout=300)


if __name__ == "__main__":
    {"up": up, "check": check, "down": down}[sys.argv[1] if len(sys.argv) > 1 else "up"]()
