"""Agent memory. Facts come from records kept by code; the LLM writes only reflections.

Each week the run adds a record: a full digest of what happened, plus the key events
(applications, replies, commitments starting or ending, offers) and whether the agent took
a gig or couldn't cover expenses. The agent sees:

- the current quarter's weeks in full detail,
- older quarters condensed to their key events plus gig and broke counts,
- a short "reflections and plans" note that the LLM rewrites every COMPRESS_EVERY weeks.

Cash, commitments, and open applications are rendered from world state in the week prompt,
so no fact in memory depends on an LLM getting it right.
"""

from dataclasses import dataclass, field

COMPRESS_EVERY = 13
REFLECTION_WORDS = 120

REFLECTION_SYSTEM = """\
You are {name}. Every quarter you write a short private note to yourself: how things are \
going and what you plan to do next. The facts of your life are kept for you in records, so \
the note is for your thinking, not for facts.\
"""

REFLECTION_USER = """\
Your note from last quarter:
{reflection}

Your records:
{records}

Write your new note in the first person, at most {words} words: how you feel things are \
going, what you've learned, and what you plan to do next. Don't restate the records. Plain \
text only.\
"""


@dataclass
class WeekRecord:
    week: int
    digest: str                                  # everything that happened, one line
    key: list[str] = field(default_factory=list)  # key events, kept when the quarter is condensed
    gig: bool = False
    broke: bool = False


class Memory:
    def __init__(self):
        self.reflection: str = ""
        self.reflection_week: int = 0
        self.weeks: list[WeekRecord] = []

    def add(self, record: WeekRecord):
        self.weeks.append(record)

    def due(self, week: int) -> bool:
        return week % COMPRESS_EVERY == 0

    def _quarters(self) -> list[list[WeekRecord]]:
        quarters: dict[int, list[WeekRecord]] = {}
        for r in self.weeks:
            quarters.setdefault((r.week - 1) // COMPRESS_EVERY, []).append(r)
        return [quarters[q] for q in sorted(quarters)]

    def records(self, current_week: int) -> str:
        """Older quarters condensed; the current quarter in full."""
        current_q = (current_week - 1) // COMPRESS_EVERY
        lines = []
        for q in self._quarters():
            first, last = q[0].week, q[-1].week
            if (first - 1) // COMPRESS_EVERY == current_q:
                lines += [r.digest for r in q]
                continue
            events = [f"[w{r.week}] {e}" for r in q for e in r.key]
            counts = []
            if gigs := sum(r.gig for r in q):
                counts.append(f"took gigs in {gigs} of {len(q)} weeks")
            if broke := sum(r.broke for r in q):
                counts.append(f"couldn't cover expenses in {broke} weeks")
            lines.append(f"Weeks {first}–{last}: " + ("; ".join(events + counts) or "nothing of note"))
        return "\n".join(lines) or "(this is your first week)"

    def reflection_prompt(self, name: str, week: int) -> tuple[str, str]:
        system = REFLECTION_SYSTEM.format(name=name)
        user = REFLECTION_USER.format(reflection=self.reflection or "(none yet)",
                                      records=self.records(week), words=REFLECTION_WORDS)
        return system, user

    def set_reflection(self, text: str, week: int):
        self.reflection = text.strip()
        self.reflection_week = week

    def render(self, week: int) -> str:
        note = (f"(written in week {self.reflection_week})\n{self.reflection}"
                if self.reflection else "(none yet)")
        return f"## Your records\n{self.records(week)}\n\n## Your reflections and plans\n{note}"
