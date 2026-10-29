"""Agent memory: a running summary plus digests of the weeks since the last summary.

Every COMPRESS_EVERY weeks the summary is rewritten (in the agent's own voice) from the old
summary and the recent digests, and the digests are dropped. Context, and cost, stay bounded.

Summaries must cite the week each fact comes from, as [w7]. `check` verifies every cited
week: it must be a week in this window where something happened, or a week the previous
summary already cited. A summary with bad or no citations is kept but flagged in the log.
That catches invented weeks, not every invented claim: a citation can point at a real week
and still misstate it.
"""

import re

COMPRESS_EVERY = 13

SUMMARY_SYSTEM = """\
You are {name}. Every quarter you rewrite your private notes about your own life, so that \
later you can pick up where you left off. Older details you leave out are forgotten.\
"""

SUMMARY_USER = """\
Your notes from before:
{summary}

The record of weeks {first} to {last}. Each line starts with its week:
{digests}

Write your updated notes in the first person, under 350 words. Keep what you will need later: \
money, commitments, people, applications still open, what you tried and how it went, and what \
you plan to do next.

Rules:
- Only state things that appear in the record above or in your notes from before. Do not add \
events, people, replies, or feedback that are not there.
- After each fact, cite the week it comes from in square brackets, like [w7]. Facts carried \
over from your old notes keep their citations.
- Plans for the future need no citation.
Plain text only.\
"""

_BRACKETS = re.compile(r"\[([^\]]*)\]")
_WEEK = re.compile(r"w(\d+)", re.IGNORECASE)


def cited_weeks(text: str) -> list[int]:
    return [int(n) for group in _BRACKETS.findall(text) for n in _WEEK.findall(group)]


def empty_digest(week: int) -> str:
    return f"[w{week}] nothing"


class Memory:
    def __init__(self):
        self.summary: str = ""
        self.recent: list[tuple[int, str]] = []  # (week, digest)

    def add(self, week: int, digest: str):
        self.recent.append((week, digest))

    def due(self, week: int) -> bool:
        return week % COMPRESS_EVERY == 0 and bool(self.recent)

    def summary_prompt(self, name: str) -> tuple[str, str]:
        system = SUMMARY_SYSTEM.format(name=name)
        user = SUMMARY_USER.format(
            summary=self.summary or "(none yet)",
            first=self.recent[0][0], last=self.recent[-1][0],
            digests="\n".join(d for _, d in self.recent))
        return system, user

    def check(self, text: str) -> dict:
        """Verify a new summary's citations against this window and the previous summary."""
        eventful = {w for w, d in self.recent if d != empty_digest(w)}
        carried = set(cited_weeks(self.summary))
        cited = sorted(set(cited_weeks(text)))
        invalid = [w for w in cited if w not in eventful and w not in carried]
        reasons = []
        if not cited:
            reasons.append("no citations")
        if invalid:
            reasons.append(f"cites weeks with no recorded events: {invalid}")
        return {"cited": cited, "invalid": invalid, "flagged": bool(reasons), "reasons": reasons}

    def replace(self, summary: str):
        self.summary = summary.strip()
        self.recent = []

    def render(self) -> str:
        notes = self.summary or "(none yet)"
        recent = "\n".join(d for _, d in self.recent) or (
            "(nothing since your last notes)" if self.summary else "(this is your first week)")
        return f"## Your notes so far\n{notes}\n\n## Recent weeks\n{recent}"
