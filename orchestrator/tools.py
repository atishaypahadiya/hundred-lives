"""Agent tool definitions, argument validation, and the per-run workspace behind `files`.

`apply`, `gig`, `leave`, and `wait` are executed by the run loop (they touch world state);
only `files` is self-contained here.
"""

from pathlib import Path

MAX_CALLS_PER_WEEK = 8

TOOLS = [
    {
        "name": "files",
        "description": (
            "Read and write files in your own workspace: notes, plans, code, drafts. Files persist "
            "across weeks. Paths are relative to the workspace root, e.g. 'notes.md' or 'app/main.py'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["list", "read", "write", "append"]},
                "path": {"type": "string", "description": "File path; not needed for list."},
                "content": {"type": "string", "description": "Text to write or append."},
            },
            "required": ["action"],
        },
    },
    {
        "name": "apply",
        "description": (
            "Apply to one of the listed openings (a job, accelerator, grant, or angel introduction) "
            "by its id. The world decides the outcome. Replies can take weeks, and some never come. "
            "You can attach files from your workspace as evidence."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "opening_id": {"type": "string", "description": "e.g. 'job-03'"},
                "details": {"type": "string",
                            "description": "What you submit: cover letter, pitch, or application answers."},
                "attachments": {"type": "array", "items": {"type": "string"},
                                "description": "Up to 3 workspace file paths to include."},
            },
            "required": ["opening_id", "details"],
        },
    },
    {
        "name": "gig",
        "description": (
            "Take gig work this week for guaranteed pay. It uses most of the week's actions, so take "
            "it early. At most once per week."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "leave",
        "description": "Leave a job or program you're in, by its commitment id (e.g. 'c4').",
        "input_schema": {
            "type": "object",
            "properties": {"commitment_id": {"type": "string"}},
            "required": ["commitment_id"],
        },
    },
    {
        "name": "wait",
        "description": "End this week. Optionally leave a one-line note about how the week went.",
        "input_schema": {
            "type": "object",
            "properties": {"note": {"type": "string"}},
            "required": [],
        },
    },
]
TOOLS_BY_NAME = {t["name"]: t for t in TOOLS}


class ToolError(Exception):
    pass


def validate(name: str, args: dict):
    """Check a call's arguments against its schema; the error says what's wrong and what's expected."""
    tool = TOOLS_BY_NAME.get(name)
    if tool is None:
        raise ToolError(f"there is no tool {name!r}; tools are {', '.join(TOOLS_BY_NAME)}")
    props, required = tool["input_schema"]["properties"], tool["input_schema"]["required"]
    problems = []
    missing = [k for k in required if args.get(k) in (None, "")]
    if missing:
        problems.append(f"missing {', '.join(missing)}")
    unknown = [k for k in args if k not in props]
    if unknown:
        owner = next((t["name"] for t in TOOLS if t["name"] != name
                      and set(unknown) <= set(t["input_schema"]["properties"])), None)
        problems.append(f"unexpected {', '.join(unknown)}"
                        + (f" (those are arguments of {owner})" if owner else ""))
    for k, spec in props.items():
        if k in args and "enum" in spec and args[k] not in spec["enum"]:
            problems.append(f"{k} must be one of {', '.join(spec['enum'])}, not {args[k]!r}")
    if problems:
        expected = ", ".join(f"{k}{' (required)' if k in required else ''}" for k in props) or "none"
        raise ToolError(f"{name} was not done: {'; '.join(problems)}. Its arguments: {expected}.")


class Workspace:
    MAX_FILE_BYTES = 64_000
    MAX_TOTAL_BYTES = 1_000_000

    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _resolve(self, path: str | None) -> Path:
        if not path:
            raise ToolError("this files action needs a path, e.g. 'notes.md'")
        p = (self.root / path).resolve()
        if p == self.root or not p.is_relative_to(self.root):
            raise ToolError("path must be a file inside your workspace")
        return p

    def _total_bytes(self) -> int:
        return sum(f.stat().st_size for f in self.root.rglob("*") if f.is_file())

    def listing(self) -> str:
        files = sorted(f for f in self.root.rglob("*") if f.is_file())
        if not files:
            return "(empty)"
        return "\n".join(f"- {f.relative_to(self.root)} ({f.stat().st_size} bytes)" for f in files)

    def read(self, path: str) -> str:
        p = self._resolve(path)
        if not p.is_file():
            raise ToolError(f"{p.relative_to(self.root)} does not exist")
        return p.read_text(errors="replace")

    def run(self, action: str, path: str | None = None, content: str | None = None) -> str:
        if action == "list":
            return self.listing()
        if action == "read":
            return self.read(path)
        if action in ("write", "append"):
            p = self._resolve(path)
            rel = p.relative_to(self.root)
            content = content or ""
            old = p.stat().st_size if p.is_file() else 0
            new = len(content.encode()) + (old if action == "append" else 0)
            if new > self.MAX_FILE_BYTES:
                raise ToolError(f"file would be {new} bytes; the limit is {self.MAX_FILE_BYTES}")
            if self._total_bytes() - old + new > self.MAX_TOTAL_BYTES:
                raise ToolError(f"workspace quota is {self.MAX_TOTAL_BYTES} bytes")
            p.parent.mkdir(parents=True, exist_ok=True)
            with p.open("a" if action == "append" else "w") as f:
                f.write(content)
            return f"{'appended to' if action == 'append' else 'wrote'} {rel} ({new} bytes)"
        raise ToolError(f"unknown action {action!r}")
