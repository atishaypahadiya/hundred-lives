"""Per-agent containers and the local Gitea code host (Docker API via OrbStack).

    uv run python -m orchestrator.sandbox up      # start Gitea, build the agent image, create the admin
    uv run python -m orchestrator.sandbox check   # verify isolation from a throwaway agent container
    uv run python -m orchestrator.sandbox down    # stop Gitea (data volume is kept)

Each run gets a Gitea user with a repo-scoped token and one container on the internal
hl-agents network, whose only reachable host is gitea:3000. The agent loop runs on the host;
containers never reach the model endpoint.

The container can't hurt the Mac: 1 CPU, 1 GB memory, 256 processes, no capabilities, no
privilege escalation, non-root, read-only root filesystem. The only writable places are the
workspace (a WORKSPACE_MB tmpfs, which is the disk quota), /tmp (TMP_MB, also $HOME) and
/dev/shm (SHM_MB). All three together stay well under the memory limit, and shell commands
raise their own OOM score, so running out of memory kills the command, not PID 1.
Because the workspace lives inside the container, the `files` tool works through the
container too (BoxWorkspace), and the host keeps a copy, regular files only, synced weekly
into runs/<id>/workspace.
"""

import base64
import io
import json
import secrets
import shlex
import shutil
import subprocess
import sys
import tarfile
import time
from pathlib import Path

import httpx2 as httpx

ROOT = Path(__file__).resolve().parent.parent
SANDBOX_DIR = ROOT / "sandbox"
STATE_PATH = SANDBOX_DIR / ".state" / "gitea-admin.json"  # gitignored
COMPOSE = SANDBOX_DIR / "compose.yaml"
IMAGE = "hundred-lives-agent:1"
NETWORK = "hl-agents"
GITEA_CONTAINER = "hl-gitea"
GITEA_HOST_URL = "http://127.0.0.1:3000"  # from the host
GITEA_AGENT_URL = "http://gitea:3000"     # from agent containers
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


class SandboxError(Exception):
    pass


def _docker(*args: str, timeout: float | None = 120, check: bool = True) -> subprocess.CompletedProcess:
    p = subprocess.run(["docker", *args], capture_output=True, text=True, timeout=timeout)
    if check and p.returncode != 0:
        raise SandboxError(f"docker {' '.join(args[:3])}… failed: {p.stderr.strip()[-500:]}")
    return p


# --- Gitea ----------------------------------------------------------------------------------

class Gitea:
    """Admin client, run from the host."""

    def __init__(self):
        if not STATE_PATH.exists():
            raise SandboxError("Gitea isn't set up; run: uv run python -m orchestrator.sandbox up")
        admin = json.loads(STATE_PATH.read_text())
        self.http = httpx.Client(base_url=GITEA_HOST_URL + "/api/v1",
                                 auth=(admin["username"], admin["password"]), timeout=30)

    def _check(self, r, what: str):
        if r.status_code >= 400:
            raise SandboxError(f"Gitea {what} failed ({r.status_code}): {r.text[:300]}")
        return r

    def create_user(self, username: str) -> str:
        """Create an agent user and return a repo-scoped access token."""
        password = secrets.token_urlsafe(18)
        self._check(self.http.post("/admin/users", json={
            "username": username, "email": f"{username}@agents.local", "password": password,
            "must_change_password": False}), f"create user {username}")
        self._check(self.http.patch(f"/admin/users/{username}", json={
            "login_name": username, "source_id": 0, "max_repo_creation": MAX_REPOS}),
            f"repo limit for {username}")
        r = self._check(self.http.post(f"/users/{username}/tokens", auth=(username, password), json={
            "name": "agent", "scopes": ["write:repository", "read:user"]}), f"token for {username}")
        return r.json()["sha1"]

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


# --- agent containers -----------------------------------------------------------------------

class AgentBox:
    """One agent's container. Shell commands run as the unprivileged `agent` user."""

    def __init__(self, run_id: str, username: str, token: str):
        self.name = f"hl-{run_id}"
        self.username = username
        self.token = token

    def start(self):
        _docker("rm", "-f", self.name, check=False)
        _docker("run", "-d", "--name", self.name, "--hostname", "computer", "--network", NETWORK,
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
        return subprocess.run(["docker", "exec", *(["-i"] if input is not None else []), "-u", "agent",
                               "-w", WORK, self.name, *argv],
                              input=input, capture_output=True, text=text, timeout=timeout)

    def exec(self, command: str, timeout_s: int = SHELL_TIMEOUT_S) -> tuple[int, str, bool]:
        """Run a command in the workspace. Returns (exit code, combined output, timed out).
        Each call is its own session: processes it leaves behind are killed afterwards."""
        try:
            p = self._run(["bash", "-c", 'echo 1000 > /proc/self/oom_score_adj; exec "$@"', "-",
                           "timeout", "-k", "5", str(timeout_s), "bash", "-lc", command],
                          timeout=timeout_s + 15)
            code, out = p.returncode, p.stdout + (("\n" if p.stdout and p.stderr else "") + p.stderr if p.stderr else "")
        except subprocess.TimeoutExpired:
            code, out = 124, ""
        self.reap()
        return code, out, code == 124

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
        p = self.box._run(["bash", "-c", "find . -name .git -prune -o -type f -printf '%P\\t%s\\n' | sort"])
        rows = [line.split("\t") for line in p.stdout.splitlines() if "\t" in line]
        return "\n".join(f"- {name} ({size} bytes)" for name, size in rows) or "(empty)"

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
            err = p.stderr.decode(errors="replace").strip()
            if "No space left" in err:
                raise ToolError(f"your workspace is full (quota {WORKSPACE_MB} MB)")
            raise ToolError(f"couldn't write {rel}: {err[:200]}")
        return f"{'appended to' if action == 'append' else 'wrote'} {rel} ({new} bytes)"

    def sync(self):
        """Copy the workspace to the host. Only regular files and directories are extracted;
        links, devices, absolute paths and .. are dropped."""
        p = self.box._run(["tar", "-C", WORK, "-cf", "-", "."], text=False, timeout=120)
        if p.returncode != 0:
            raise SandboxError(f"workspace copy failed: {p.stderr.decode(errors='replace')[-300:]}")
        tmp = self.root.with_name(self.root.name + ".sync")
        shutil.rmtree(tmp, ignore_errors=True)
        tmp.mkdir()

        def regular_only(member: tarfile.TarInfo, dest: str):
            if not (member.isfile() or member.isdir()):
                return None
            return tarfile.data_filter(member, dest)

        with tarfile.open(fileobj=io.BytesIO(p.stdout)) as tar:
            tar.extractall(tmp, filter=regular_only)
        shutil.rmtree(self.root, ignore_errors=True)
        tmp.rename(self.root)


# --- CLI ------------------------------------------------------------------------------------

def up():
    print("starting Gitea…")
    _docker("compose", "-f", str(COMPOSE), "up", "-d", timeout=600)
    print("building agent image…")
    _docker("build", "-t", IMAGE, "-f", str(SANDBOX_DIR / "agent.Dockerfile"), str(SANDBOX_DIR), timeout=900)
    for _ in range(60):
        try:
            if httpx.get(GITEA_HOST_URL + "/api/healthz", timeout=2).status_code == 200:
                break
        except httpx.TransportError:
            pass
        time.sleep(1)
    else:
        raise SandboxError("Gitea didn't become healthy")
    if not STATE_PATH.exists():
        password = secrets.token_urlsafe(24)
        _docker("exec", "-u", "git", GITEA_CONTAINER, "gitea", "admin", "user", "create", "--admin",
                "--username", ADMIN_USER, "--password", password, "--email", "admin@agents.local",
                "--must-change-password=false")
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        STATE_PATH.write_text(json.dumps({"username": ADMIN_USER, "password": password}))
        STATE_PATH.chmod(0o600)
        print(f"created Gitea admin {ADMIN_USER} (credentials in {STATE_PATH.relative_to(ROOT)})")
    print(f"ready: Gitea at {GITEA_HOST_URL}, agent image {IMAGE}, network {NETWORK}")


def check():
    """Start a throwaway agent box and probe what it can reach."""
    user = f"check-{secrets.token_hex(3)}"
    token = Gitea().create_user(user)
    box = AgentBox(user, user, token)
    box.start()
    probes = {
        "gitea reachable": "curl -sS -m 5 -o /dev/null -w '%{http_code}' http://gitea:3000/api/healthz",
        "internet blocked": "curl -sS -m 5 -o /dev/null -w '%{http_code}' https://example.com",
        "internet by IP blocked": "curl -sS -m 5 -o /dev/null -w '%{http_code}' http://1.1.1.1",
        "host model endpoint blocked": "curl -sS -m 5 -o /dev/null -w '%{http_code}' http://host.docker.internal:11434",
        "push-to-create works": "mkdir -p /tmp/p && cd /tmp/p && git init -q && echo hi > README.md && "
                                "git add . && git commit -qm init && "
                                f"git push -q http://gitea:3000/{user}/probe.git main 2>&1 && echo pushed",
        "runs as non-root": "id -un",
        "workspace writable": "touch probe && ls probe",
    }
    try:
        for name, cmd in probes.items():
            code, out, _ = box.exec(cmd, timeout_s=20)
            print(f"{name:<28} exit {code:<3} {out.strip()[-120:]}")
        snap = Gitea().snapshot(user, "probe")
        print(f"{'referee can read repo':<28} {bool(snap and snap['readme'])} {snap and snap['files']}")
    finally:
        box.stop()


def down():
    _docker("compose", "-f", str(COMPOSE), "down", timeout=300)


if __name__ == "__main__":
    {"up": up, "check": check, "down": down}[sys.argv[1] if len(sys.argv) > 1 else "up"]()
