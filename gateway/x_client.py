"""The official X API v2, pay-per-use, with an OAuth 2.0 user-context token from .env.

Only three calls exist: who am I (to check the bio), read mentions, and create a post
(optionally a reply). There is no code for DMs, follows, likes, reposts, search, or timelines.
Built with live=False (the default everywhere), it makes no network call at all: `post`
returns a fake id and `mentions` returns nothing, and both say so in the result.

Endpoints and token handling follow the X API v2 docs as of 2026; check them against the
developer console before the first live run. Prices are per call and set in your X developer
account; put them in .env (X_COST_PER_POST_USD, X_COST_PER_LINK_POST_USD, X_COST_PER_READ_USD)
so the log can total them. They're unknown here, so nothing is assumed.
"""

import os
import secrets
import time

import httpx2 as httpx

API = "https://api.x.com/2"
TIMEOUT = 20.0


class XError(Exception):
    def __init__(self, status: int | None, message: str):
        self.status, self.message = status, message
        super().__init__(f"X API {status}: {message}")


def cost(kind: str, n: int = 1, link: bool = False) -> float | None:
    """The configured price of `n` calls, or None when it isn't configured."""
    var = {"read": "X_COST_PER_READ_USD", "post": "X_COST_PER_LINK_POST_USD" if link else "X_COST_PER_POST_USD"}[kind]
    v = os.environ.get(var)
    return round(float(v) * n, 6) if v else None


class XClient:
    def __init__(self, live: bool = False, transport: httpx.BaseTransport | None = None):
        self.live = live
        self.token = os.environ.get("X_USER_ACCESS_TOKEN")
        if live and not self.token:
            raise XError(None, "X_USER_ACCESS_TOKEN isn't set in .env")
        self.http = httpx.Client(base_url=API, timeout=TIMEOUT, transport=transport,
                                 headers={"Authorization": f"Bearer {self.token}"} if self.token else {}) if live else None
        self.user_id: str | None = os.environ.get("X_USER_ID")

    def _req(self, method: str, path: str, **kw) -> dict:
        r = self.http.request(method, path, **kw)
        if r.status_code == 429:
            raise XError(429, f"rate limited; resets at {r.headers.get('x-rate-limit-reset')}")
        if r.status_code >= 400:
            raise XError(r.status_code, r.text[:500])
        return r.json()

    def me(self) -> dict:
        if not self.live:
            return {"id": "dry-run", "username": "dry_run", "description": None, "dry_run": True}
        d = self._req("GET", "/users/me", params={"user.fields": "description"})["data"]
        self.user_id = d["id"]
        return d

    def mentions(self, since_id: str | None, max_results: int) -> list[dict]:
        """Posts that mention the account (replies to it included), newest first, as
        {id, text, author_id, author_username, created_at, conversation_id, kind}."""
        if not self.live or max_results <= 0:
            return []
        params = {"max_results": max(5, min(100, max_results)), "expansions": "author_id",
                  "user.fields": "username", "tweet.fields": "created_at,conversation_id,referenced_tweets"}
        if since_id:
            params["since_id"] = since_id
        d = self._req("GET", f"/users/{self.user_id or self.me()['id']}/mentions", params=params)
        users = {u["id"]: u["username"] for u in (d.get("includes") or {}).get("users", [])}
        out = []
        for t in (d.get("data") or [])[:max_results]:
            refs = {r["type"] for r in t.get("referenced_tweets") or []}
            out.append({"id": t["id"], "text": t["text"], "author_id": t["author_id"],
                        "author_username": users.get(t["author_id"], ""), "created_at": t.get("created_at"),
                        "conversation_id": t.get("conversation_id"),
                        "kind": "reply" if "replied_to" in refs else "quote" if "quoted" in refs else "mention"})
        return out

    def post(self, text: str, in_reply_to: str | None = None) -> dict:
        if not self.live:
            return {"id": "dry-" + secrets.token_hex(4), "dry_run": True, "at": time.time()}
        body = {"text": text}
        if in_reply_to:
            body["reply"] = {"in_reply_to_tweet_id": in_reply_to}
        return self._req("POST", "/tweets", json=body)["data"]
