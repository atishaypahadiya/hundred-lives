"""The gateway's state: one append-only JSONL log under the state directory (default
`gateway/state/`, gitignored), and a KILL file. Every inbound item, draft, decision, send,
refusal, and error is an event in `gateway.jsonl`; the outbox is rebuilt by replaying it, so
the log is the only state there is.

Outbox items: {id, kind: post | reply | publish_repo, text, in_reply_to, repo, created,
status: pending | approved | rejected | sent | failed | blocked, reason, remote_id}.
"""

import json
import os
import secrets
from datetime import datetime, timezone
from pathlib import Path

from . import policy

DEFAULT_DIR = Path(__file__).resolve().parent / "state"


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, root: Path = DEFAULT_DIR):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.log_path = self.root / "gateway.jsonl"
        self.kill_path = self.root / "KILL"

    # --- the log ---
    def log(self, type_: str, **fields) -> dict:
        rec = {"ts": now(), "type": type_, **fields}
        with open(self.log_path, "a") as f:
            f.write(json.dumps(rec, default=str) + "\n")
        return rec

    def events(self) -> list[dict]:
        if not self.log_path.exists():
            return []
        out = []
        with open(self.log_path) as f:
            for line in f:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue  # a line still being written
        return out

    # --- the kill switch: a file, so it works with the gateway stopped, hung, or mid-turn ---
    def killed(self) -> bool:
        return self.kill_path.exists()

    def kill(self, why: str = "") -> None:
        self.kill_path.write_text(f"{now()} {why}\n")
        self.log("kill", why=why)

    def unkill(self) -> None:
        if self.kill_path.exists():
            self.kill_path.unlink()
        self.log("unkill")

    # --- the outbox, rebuilt from the log ---
    def outbox(self) -> dict[str, dict]:
        items: dict[str, dict] = {}
        for e in self.events():
            t, i = e["type"], e.get("item_id")
            if t == "draft":
                items[i] = {**e["item"], "status": "pending", "created": e["ts"], "history": []}
            elif i in items and t in ("approved", "rejected", "sent", "failed", "blocked"):
                items[i]["status"] = t
                items[i]["history"].append({"ts": e["ts"], "status": t, "reason": e.get("reason")})
                if t == "sent":
                    items[i]["remote_id"], items[i]["sent_ts"] = e.get("remote_id"), e["ts"]
        return items

    def inbound(self) -> dict[str, dict]:
        """Engagement with the account, by post id (mentions, replies, quotes)."""
        return {e["post"]["id"]: e["post"] for e in self.events() if e["type"] == "inbound"}

    def sent_today(self, day: str | None = None) -> list[dict]:
        d = day or policy.day()
        return [i for i in self.outbox().values()
                if i["status"] == "sent" and i["kind"] in ("post", "reply") and policy.day(i["sent_ts"]) == d]

    def reads_today(self, day: str | None = None) -> int:
        d = day or policy.day()
        return sum(e.get("count", 0) for e in self.events() if e["type"] == "read" and policy.day(e["ts"]) == d)

    def replied_to(self, exclude: str | None = None) -> set[str]:
        return {i["in_reply_to"] for i in self.outbox().values()
                if i["kind"] == "reply" and i["status"] in ("pending", "approved", "sent") and i["id"] != exclude}

    def new_id(self) -> str:
        return "d-" + secrets.token_hex(4)


def approve_token(root: Path = DEFAULT_DIR) -> str:
    """A random token the approval endpoints require in a header. Created once, readable only
    by the user (0600). A cross-origin page can't set the header without a CORS preflight,
    which the viewer never grants."""
    p = Path(root) / "approve.token"
    if not p.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(secrets.token_urlsafe(24))
    return p.read_text().strip()
