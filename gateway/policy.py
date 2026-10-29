"""The gateway's rules. They're constants in code, not configuration: nothing the agent says,
no inbound content, and no config file can change them. Every check runs twice, when a draft
enters the outbox and again right before it's sent, against the state at that moment.

- at most POSTS_PER_DAY posts a day (UTC), replies included, and at most LINK_POSTS_PER_DAY of
  them with a link (link posts cost more on the pay-per-use API, so they're counted apart);
- replies only to someone who engaged with the account first (mentioned it, replied to it, or
  quoted it), only to that post, and at most one reply per inbound post;
- no mentions of anyone else: a post mentions nobody, a reply mentions only the person it
  answers; no DMs and no follows (the gateway has no code for either);
- reads capped at READS_PER_DAY items a day;
- the profile bio must disclose that this is an AI agent running a simulated life, with a link
  to the project, before anything is posted.
"""

import re
import unicodedata
from datetime import datetime, timezone

POSTS_PER_DAY = 3
LINK_POSTS_PER_DAY = 1
READS_PER_DAY = 100
MAX_CHARS = 280
PROJECT_URL = "https://github.com/atishaypahadiya/hundred-lives"   # PLACEHOLDER until the repo is public
BIO = f"An AI agent living a simulated life, posting about it. Not a person. Run by a human researcher: {PROJECT_URL}"
BIO_MUST_CONTAIN = ("AI agent", "simulated life")

# Both match on the NFKC form, so fullwidth ＠ or ． count like @ and ., and both err toward
# matching: any word.word with a 2+ letter last label counts as a link, whatever the TLD.
MENTION = re.compile(r"(?<![\w@])@(\w{1,15})")
URL = re.compile(r"https?://\S+|\b[\w-]+(?:\.[\w-]+)*\.[^\W\d_]{2,}\b", re.I)
CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f​-‏‪-‮⁦-⁩]")


class PolicyError(Exception):
    """A draft breaks a rule. Its message says which and is shown to the agent and logged."""


def day(ts: str | None = None) -> str:
    return (datetime.fromisoformat(ts) if ts else datetime.now(timezone.utc)).astimezone(timezone.utc).date().isoformat()


def norm(text: str) -> str:
    return unicodedata.normalize("NFKC", text or "")


def has_link(text: str) -> bool:
    return bool(URL.search(norm(text)))


def check_draft(item: dict, sent_today: list[dict], inbound: dict[str, dict], replied_to: set[str]) -> None:
    """Raise PolicyError if `item` can't be posted now. `sent_today`: items sent this UTC day.
    `inbound`: engagement with the account, by post id. `replied_to`: inbound ids already answered
    (sent or waiting in the outbox)."""
    text = item.get("text") or ""
    if not text.strip():
        raise PolicyError("the post is empty")
    if len(text) > MAX_CHARS:
        raise PolicyError(f"a post is at most {MAX_CHARS} characters; this is {len(text)}")
    if CONTROL.search(text):
        raise PolicyError("the post contains control or invisible formatting characters")
    if len(sent_today) >= POSTS_PER_DAY:
        raise PolicyError(f"already {len(sent_today)} posts today (UTC); the limit is {POSTS_PER_DAY}")
    if has_link(text) and sum(has_link(s.get("text") or "") for s in sent_today) >= LINK_POSTS_PER_DAY:
        raise PolicyError(f"already {LINK_POSTS_PER_DAY} post with a link today; link posts are capped separately")
    mentions = {m.lower() for m in MENTION.findall(norm(text))}
    if item["kind"] == "post":
        if mentions:
            raise PolicyError("a post can't mention anyone (@" + ", @".join(sorted(mentions)) + ")")
        if item.get("in_reply_to"):
            raise PolicyError("a post can't be a reply; use a reply draft")
    elif item["kind"] == "reply":
        src = inbound.get(item.get("in_reply_to") or "")
        if src is None:
            raise PolicyError("replies only go to someone who engaged with the account first; "
                              f"{item.get('in_reply_to')!r} isn't a mention, reply, or quote of it")
        if item["in_reply_to"] in replied_to:
            raise PolicyError(f"post {item['in_reply_to']} already has a reply")
        author = (src.get("author_username") or "").lower()
        others = mentions - {author}
        if others:
            raise PolicyError("a reply may mention only the person it answers (@" + author + "), not @"
                              + ", @".join(sorted(others)))
    else:
        raise PolicyError(f"unknown kind {item['kind']!r}: the gateway only posts and replies")


def check_bio(description: str | None) -> None:
    d = (description or "").lower()
    missing = [s for s in BIO_MUST_CONTAIN if s.lower() not in d]
    if missing or PROJECT_URL.split("//")[1].lower() not in d:
        raise PolicyError("the profile bio must say it's an AI agent running a simulated life and link the "
                          f"project; set it to: {BIO}")


def check_reads(read_today: int, n: int) -> int:
    """How many of `n` items may still be read today."""
    return max(0, min(n, READS_PER_DAY - read_today))


def untrusted(text: str, limit: int = 1000) -> str:
    """Inbound text as data: control and bidi characters removed, length capped, and the
    wrapper tags neutralized so the content can't close its own <untrusted> block."""
    t = CONTROL.sub("", text or "")[:limit]
    return t.replace("<untrusted", "<_untrusted").replace("</untrusted", "</_untrusted")
