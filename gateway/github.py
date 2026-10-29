"""Mirror one of a run's repos to GitHub, only after a person approves it in the outbox.

The source is the run's exported bundle (`runs/<run>/repos/<repo>.bundle`), never the live
Gitea. It's cloned bare into a temp dir (no checkout, hooks off), every blob in its history is
scanned for secrets and email addresses, and only a clean repo is pushed, to a new PRIVATE
repo in the separate org GITHUB_ORG (flip it public by hand). The fine-grained token
(GITHUB_TOKEN in .env, scoped to that org) reaches git only through an environment config
header: never in a URL, a remote, or a file. With live=False nothing leaves the machine.
"""

import base64
import os
import re
import subprocess
import tempfile
from pathlib import Path

import httpx2 as httpx

API = "https://api.github.com"
MAX_BLOB = 2_000_000

SECRETS = [
    ("anthropic key", re.compile(rb"sk-ant-[A-Za-z0-9_\-]{10,}")),
    ("openai-style key", re.compile(rb"\bsk-[A-Za-z0-9]{20,}")),
    ("github token", re.compile(rb"\b(ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}")),
    ("aws key", re.compile(rb"\bAKIA[0-9A-Z]{16}\b")),
    ("private key", re.compile(rb"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("slack token", re.compile(rb"\bxox[abpors]-[A-Za-z0-9-]{10,}")),
    ("bearer token", re.compile(rb"(?i)bearer\s+[A-Za-z0-9\-_.=]{24,}")),
    ("assigned secret", re.compile(rb"(?i)(api[_-]?key|secret|token|password)\s*[:=]\s*['\"][^'\"\s]{12,}['\"]")),
    ("email address", re.compile(rb"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")),
]
ALLOWED_EMAILS = re.compile(rb"[^@\s]+@(example\.(com|org)|users\.noreply\.github\.com|hl\.local|gitea\.local)")


def allowed_email(e: bytes) -> bool:
    """The whole address must be on an allowed domain (x@example.com.evil.org isn't)."""
    return ALLOWED_EMAILS.fullmatch(e) is not None


def _git(*args: str, cwd: Path | None = None, env: dict | None = None, input_: bytes | None = None) -> bytes:
    base = ["git", "-c", "core.hooksPath=/dev/null", "-c", "protocol.file.allow=always"]
    r = subprocess.run(base + list(args), cwd=cwd, env=env, input=input_, capture_output=True, timeout=300)
    if r.returncode:
        raise RuntimeError(f"git {args[0]} failed: {r.stderr.decode(errors='replace')[:500]}")
    return r.stdout


def scan(bare: Path) -> list[dict]:
    """Every secret-looking string and email in every object of every type: blobs (file
    contents), trees (file names), commits (messages, author and committer names and emails),
    and tags (messages, tagger). Returns [{what, object, sample}] with the sample redacted."""
    hits = []
    for line in _git("log", "--all", "--format=%H %ae %ce", cwd=bare).decode().splitlines():
        sha, *emails = line.split()
        for e in emails:
            if not allowed_email(e.encode()):
                hits.append({"what": "commit email", "object": sha[:10], "sample": e[:2] + "…"})
    objs = [l.split() for l in _git("cat-file", "--batch-all-objects",
                                    "--batch-check=%(objectname) %(objecttype) %(objectsize)", cwd=bare).decode().splitlines()]
    hits += [{"what": f"{t} too large to scan", "object": sha[:10], "sample": ""} for sha, t, n in objs if int(n) > MAX_BLOB]
    for sha, kind, n in objs:
        if int(n) > MAX_BLOB:
            continue
        data = _git("cat-file", kind, sha, cwd=bare)
        for what, rx in SECRETS:
            for m in rx.finditer(data):
                if what == "email address" and allowed_email(m.group(0)):
                    continue
                hits.append({"what": what, "object": f"{kind} {sha[:10]}", "sample": m.group(0)[:4].decode(errors="replace") + "…"})
    return hits


def publish(bundle: Path, name: str, description: str = "", live: bool = False,
            transport: httpx.BaseTransport | None = None) -> dict:
    """Clone, scan, and (if clean and live) create GITHUB_ORG/<name> private and push to it."""
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,100}", name):
        raise ValueError(f"bad repo name {name!r}")
    with tempfile.TemporaryDirectory(prefix="hl-publish-") as tmp:
        bare = Path(tmp) / "repo.git"
        _git("clone", "--bare", "--quiet", str(bundle), str(bare))
        hits = scan(bare)
        refs = _git("for-each-ref", "--format=%(refname)", cwd=bare).decode().split()
        result = {"repo": name, "refs": refs, "scan_hits": hits, "pushed": False, "dry_run": not live}
        if hits:
            result["refused"] = f"{len(hits)} scan hit(s); nothing was pushed"
            return result
        if not live:
            return result
        org, token = os.environ.get("GITHUB_ORG"), os.environ.get("GITHUB_TOKEN")
        if not org or not token:
            raise RuntimeError("GITHUB_ORG and GITHUB_TOKEN must be set in .env")
        with httpx.Client(base_url=API, timeout=30, transport=transport, headers={
                "Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28"}) as gh:
            r = gh.post(f"/orgs/{org}/repos", json={"name": name, "description": description[:300], "private": True,
                                                     "has_issues": False, "has_wiki": False, "has_projects": False})
            if r.status_code >= 400:
                raise RuntimeError(f"GitHub create repo {r.status_code}: {r.text[:300]}")
        header = "Authorization: Basic " + base64.b64encode(f"x-access-token:{token}".encode()).decode()
        env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_CONFIG_COUNT": "1",
               "GIT_CONFIG_KEY_0": "http.https://github.com/.extraheader", "GIT_CONFIG_VALUE_0": header}
        _git("push", "--quiet", f"https://github.com/{org}/{name}.git",
             "refs/heads/*:refs/heads/*", "refs/tags/*:refs/tags/*", cwd=bare, env=env)
        result["pushed"], result["url"] = True, f"https://github.com/{org}/{name}"
        return result
