"""Raw run-log records -> the viewer's standard events.

One function, `normalize(rec, seq)`, used by the live stream, replays, and (Phase 3) static
export, so every view of a life is built from the same events. Kinds:

    meta          run config (models, horizon, seeds, code) and run end
    week_start    news, broke, actions, openings hidden
    thought       the agent's visible text and its weekly wait notes (thinking blocks aren't logged yet)
    tool_call     a tool call and its result (tool, input, output, is_error)
    shell_output  a shell command, exit code, output
    git_commit    parsed from `git commit` output in the shell (sha, message, files, +/-)
    git_push      parsed from `git push` output, or a repo appearing on the code host
    idea          quarterly reflections, files the agent wrote (its plans), launch pitches
    referee       applications (estimate, base rate, draw, outcome), replies, launches
    life          commitments, offers, gigs, broke weeks
    money         cash, income, expenses, company funding (from week_start/week_end)
    week_end      digest, reason, actions used
    usage         tokens and cost after each model call
    summary       the run's end

Agent-made text (shell output, notes, pitches, READMEs) is untrusted: the browser renders
every field as text, never as HTML.
"""

import re

MAX_TEXT = 20_000  # per field sent to the browser; the log keeps the rest

COMMIT = re.compile(r"^\[(?P<branch>[^\s\]]+)(?: \(root-commit\))? (?P<sha>[0-9a-f]{7,40})\] (?P<msg>.*)$", re.M)
STAT = re.compile(r"(?P<files>\d+) files? changed(?:, (?P<ins>\d+) insertions?\(\+\))?(?:, (?P<del>\d+) deletions?\(-\))?")
PUSH_TO = re.compile(r"^To (?P<url>https?://\S+)", re.M)
PUSH_REF = re.compile(r"^\s*(?:\*|\+|-|=|!)?\s*(?:\[new branch\]|[0-9a-f]{7,}\.\.\.?[0-9a-f]{7,})\s+(?P<src>\S+) -> (?P<dst>\S+)", re.M)


def _t(s, n: int = MAX_TEXT) -> str:
    s = "" if s is None else str(s)
    return s if len(s) <= n else s[:n] + f"\n… [{len(s) - n:,} more characters in the log]"


def _money(state: dict | None) -> dict:
    s = state or {}
    return {"cash": s.get("cash"), "income": s.get("weekly_income"), "expenses": s.get("weekly_expenses"),
            "company_funding": s.get("company_funding"), "commitments": [c.get("label") for c in s.get("commitments") or []]}


CMD_COMMIT = re.compile(r"\bgit\b(?:\s+-c\s+\S+)*\s+commit\b[^\n;&|]*?\s-[a-zA-Z]*m\s*(?P<q>[\"'])(?P<msg>.*?)(?P=q)")
CMD_PUSH = re.compile(r"\bgit\b(?:\s+-c\s+\S+)*\s+push\b[^\n;&|]*")
REPO_URL = re.compile(r"https?://[^/\s]+/(?P<repo>[\w.-]+/[\w.-]+?)(?:\.git)?(?=[\s\"']|$)")


def parse_git(command: str, output: str, exit_code: int | None = 0) -> list[dict]:
    """Commits and pushes in one shell call: from git's output, or, when git ran quietly (-q)
    and the command succeeded, from the command text (source "command", no sha)."""
    out = []
    for m in COMMIT.finditer(output or ""):
        stat = STAT.search(output, m.end())
        out.append({"kind": "git_commit", "sha": m["sha"], "branch": m["branch"], "message": _t(m["msg"], 500),
                    "files": int(stat["files"]) if stat else None,
                    "insertions": int(stat["ins"] or 0) if stat else None,
                    "deletions": int(stat["del"] or 0) if stat else None, "source": "shell"})
    for m in PUSH_TO.finditer(output or ""):
        refs = [f"{r['src']} -> {r['dst']}" for r in PUSH_REF.finditer(output, m.end())]
        out.append({"kind": "git_push", "url": m["url"], "refs": refs, "source": "shell"})
    if exit_code == 0:
        if not any(e["kind"] == "git_commit" for e in out):
            out += [{"kind": "git_commit", "sha": None, "branch": None, "message": _t(m["msg"], 500), "files": None,
                     "insertions": None, "deletions": None, "source": "command"} for m in CMD_COMMIT.finditer(command or "")]
        if not any(e["kind"] == "git_push" for e in out):
            for m in CMD_PUSH.finditer(command or ""):
                url = REPO_URL.search(m.group(0))
                out.append({"kind": "git_push", "url": url.group(0) if url else None,
                            "repo": url["repo"] if url else None, "refs": [], "source": "command"})
    return out


def normalize(rec: dict, seq: int) -> list[dict]:
    t, w = rec.get("type"), rec.get("week")
    base = {"seq": seq, "week": w, "ts": rec.get("ts")}
    ev = lambda kind, **f: {**base, "kind": kind, **f}

    if t == "run_start":
        code = rec.get("code") or {}
        return [ev("meta", event="start", run_id=rec.get("run_id"), persona=(rec.get("persona") or {}).get("name"),
                   models=rec.get("models"), horizon=rec.get("horizon"), sandbox=rec.get("sandbox"),
                   seed=rec.get("seed"), order_seed=rec.get("order_seed", rec.get("seed")),
                   dice_seed=rec.get("dice_seed", rec.get("seed")), dice_scheme=rec.get("dice_scheme", "sequential"),
                   code=code, pilot=not code or bool(code.get("dirty")),
                   illustrative=rec.get("world_illustrative", True), token_budget=rec.get("token_budget"))]
    if t == "week_start":
        return [ev("week_start", news=rec.get("news") or [], broke=rec.get("broke"), actions=rec.get("actions"),
                   openings_hidden=rec.get("openings_hidden"), carried_errors=rec.get("carried_errors") or []),
                ev("money", at="start", **_money(rec.get("state")))]
    if t == "agent_text":
        return [ev("thought", text=_t(rec.get("text")))]
    if t == "nudge":
        return [ev("thought", text=_t(rec.get("text") or "(nudged to act)"), nudge=True)]
    if t == "tool_call":
        tool, a = rec.get("tool"), rec.get("input") or {}
        out = [ev("tool_call", tool=tool, input={k: _t(v, 4000) for k, v in a.items()},
                  output=_t(rec.get("output"), 4000), is_error=rec.get("is_error"))]
        if tool == "wait" and a.get("note") and not rec.get("is_error"):
            out.append(ev("thought", source="note", text=_t(a["note"])))
        if tool == "files" and a.get("action") in ("write", "append") and not rec.get("is_error"):
            out.append(ev("idea", source="file", path=_t(a.get("path"), 300), text=_t(a.get("content"))))
        return out
    if t == "shell":
        cmd, output = rec.get("command") or "", rec.get("output") or ""
        return [ev("shell_output", command=_t(cmd, 4000), exit_code=rec.get("exit_code"),
                   timed_out=rec.get("timed_out"), stopped=rec.get("stopped"), output=_t(output))] + \
               [{**base, **g} for g in parse_git(cmd, output, rec.get("exit_code"))]
    if t == "repo_created":
        return [ev("git_push", repo=rec.get("repo"), source="code host", first=True)]
    if t == "referee_decision":
        e = rec.get("estimate") or {}
        return [ev("referee", event="apply", opening_id=rec.get("opening_id"), opening_type=rec.get("opening_type"),
                   base_rate=rec.get("base_rate"), estimated_chance=rec.get("estimated_chance"),
                   draw=rec.get("success_draw"), key=rec.get("success_key"), attempt=rec.get("attempt"),
                   outcome=rec.get("outcome"), reveal_week=rec.get("reveal_week"), position=rec.get("position"),
                   repo=rec.get("repo"), details=_t(rec.get("details"), 4000), rationale=_t(e.get("rationale"), 2000),
                   model=rec.get("model"))]
    if t in ("outcome_applied", "outcome_expired"):
        return [ev("referee", event="reply" if t == "outcome_applied" else "no_reply",
                   opening_id=rec.get("opening_id"), outcome=rec.get("outcome", "ignored"))]
    if t == "launch":
        e, snap = rec.get("estimate") or {}, rec.get("repo_snapshot") or {}
        shown = e.get("comments_if_traction" if rec.get("outcome") == "traction" else "comments_if_quiet") or []
        return [ev("referee", event="launch", repo=rec.get("repo"), title=_t(rec.get("title"), 300),
                   base_rate=rec.get("base_rate"), estimated_chance=rec.get("estimated_chance"),
                   draw=rec.get("success_draw"), key=rec.get("success_key"), outcome=rec.get("outcome"),
                   upvotes=rec.get("upvotes"), comments=[_t(c, 500) for c in shown],
                   rationale=_t(e.get("rationale"), 2000),
                   snapshot={"files": snap.get("files", [])[:100], "file_count": snap.get("file_count"),
                             "commits": snap.get("commits"), "readme": _t(snap.get("readme"), 3000)}),
                ev("idea", source="pitch", title=_t(rec.get("title"), 300), text=_t(rec.get("pitch")))]
    if t == "memory_reflection":
        return [ev("idea", source="reflection", text=_t(rec.get("reflection") or rec.get("text")))]
    if t in ("commitment_started", "commitment_ended", "offer_held", "offer_lapsed", "gig", "broke"):
        c = rec.get("commitment") or {}
        return [ev("life", event=t, opening_id=rec.get("opening_id") or c.get("opening_id"),
                   label=c.get("label"), reason=rec.get("reason"), forced=rec.get("forced"), pay=rec.get("pay"))]
    if t == "week_end":
        return [ev("week_end", reason=rec.get("reason"), used=rec.get("used"), actions=rec.get("actions"),
                   valid=rec.get("valid"), invalid=rec.get("invalid"), shortfall=rec.get("shortfall"),
                   digest=_t(rec.get("digest"), 4000), key_events=rec.get("key_events") or []),
                ev("money", at="end", **_money(rec.get("state")))]
    if t == "llm_call":
        return [ev("usage", role=rec.get("role"), tokens_used=rec.get("tokens_used"),
                   cost_usd_total=rec.get("cost_usd_total"))]
    if t == "repos_exported":
        return [ev("meta", event="repos_exported", repos=rec.get("repos") or [])]
    if t == "api_error":
        return [ev("meta", event="api_error", role=rec.get("role"), message=_t(rec.get("message"), 500))]
    if t == "run_end":
        return [ev("summary", reason=rec.get("reason"), weeks_completed=rec.get("weeks_completed"),
                   tokens_used=rec.get("tokens_used"), cost_usd=rec.get("cost_usd"),
                   valid_action_rate=rec.get("valid_action_rate"), final=_money(rec.get("final_state")),
                   reflection=_t(rec.get("reflection"), 2000))]
    return []  # tool_error duplicates tool_call; sandbox/sync/teardown events stay in the log
