"""Agent tool definitions, argument validation, and the per-run workspace behind `files`.

`apply`, `message`, `spend`, `gig`, `leave`, and `wait` are executed by the run loop (they
touch world state); only `files` is self-contained here.

Time: each week has ACTIONS_PER_WEEK actions, minus what commitments use, plus a hired
contractor's coding actions (shell and files only). A valid files, shell, apply, launch,
message, spend, or leave call uses 1; a gig uses the world's gig.time_cost; wait is free.
Invalid calls use nothing, but after MAX_INVALID_PER_WEEK of them the week ends.
"""

import errno
import os
import stat
from pathlib import Path

ACTIONS_PER_WEEK = 8
REAPPLY_AFTER_WEEKS = 26  # an opening can be applied to again this long after the last application
MAX_INVALID_PER_WEEK = 3
ACTION_COST = {"files": 1, "shell": 1, "apply": 1, "launch": 1, "message": 1, "spend": 1, "leave": 1,
               "post": 1, "submit": 0, "train": 1, "wait": 0}  # gig: world; submitting is part of attending a hackathon
CODING_TOOLS = ("shell", "files")  # what a contractor's extra actions can be used for
SANDBOX_TOOLS = ("shell", "launch", "submit")  # only offered when the run has a container
FEED_TOOLS = ("post",)          # only in worlds with a feed
HACKATHON_TOOLS = ("submit",)   # only in worlds with hackathons
TRAINING_TOOLS = ("train",)     # only in worlds with `training`

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
            "Apply to one of this week's listed openings (a job, accelerator, grant, or angel "
            "introduction) by its id. The world decides the outcome. Replies can take weeks, and some "
            "never come. You can apply to the same opening again "
            f"{REAPPLY_AFTER_WEEKS} weeks after you last applied to it; until then, and while you're "
            "waiting to hear back, it isn't listed. You can attach files from your workspace as evidence."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "opening_id": {"type": "string", "description": "e.g. 'job-03'"},
                "details": {"type": "string",
                            "description": "What you submit: cover letter, pitch, or application answers."},
                "attachments": {"type": "array", "items": {"type": "string"},
                                "description": "Up to 3 workspace file paths to include."},
                "repo": {"type": "string",
                         "description": "Optional: one of your repos on the code host to show as evidence."},
            },
            "required": ["opening_id", "details"],
        },
    },
    {
        "name": "shell",
        "description": (
            "Run a bash command on your own Linux computer, in your workspace directory (the same "
            "files as the files tool). Has git and Python 3.12. No internet. Commands time out after "
            "60 seconds; long output is truncated."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"command": {"type": "string"}},
            "required": ["command"],
        },
    },
    {
        "name": "launch",
        "description": (
            "Post one of your repos to the builders' forum with a title and pitch. How people respond "
            "depends on what's actually in the repo."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "repo": {"type": "string", "description": "Name of one of your repos on the code host."},
                "title": {"type": "string"},
                "pitch": {"type": "string", "description": "What it is and why someone should care."},
            },
            "required": ["repo", "title", "pitch"],
        },
    },
    {
        "name": "message",
        "description": (
            "Send a message to one of the people you know, by their contact id. They may or may not "
            "reply; if they do, you see the reply right away. What they can do for you depends on "
            "who they are and what you ask."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "contact_id": {"type": "string", "description": "e.g. 'parents'"},
                "text": {"type": "string", "description": "Your message."},
            },
            "required": ["contact_id", "text"],
        },
    },
    {
        "name": "spend",
        "description": (
            "Spend your company's funds (not your personal cash) on one item from the price list: "
            "'contractor' hires a developer for a number of weeks; 'launch_ads' promotes the next "
            "launch of one of your repos. Paid up front."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "item": {"type": "string", "enum": ["contractor", "launch_ads"]},
                "weeks": {"type": "integer", "description": "contractor only: how many weeks to hire for."},
                "repo": {"type": "string", "description": "launch_ads only: the repo to promote."},
            },
            "required": ["item"],
        },
    },
    {
        "name": "post",
        "description": (
            "Post a short public update to the social feed, optionally linking one of your repos. "
            "Anyone may see it; how many do, and how they react, depends on what you post and on "
            "your followers. Most posts get little attention."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "The post, at most 500 characters."},
                "repo": {"type": "string", "description": "Optional: one of your repos to link."},
            },
            "required": ["text"],
        },
    },
    {
        "name": "train",
        "description": (
            "Spend time on physical training (a workout, a run, sport practice). It's logged; your "
            "training streak is the number of weeks in a row with at least one session. A week with "
            "no free actions doesn't break it."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"activity": {"type": "string", "description": "What you did, briefly."}},
            "required": ["activity"],
        },
    },
    {
        "name": "submit",
        "description": (
            "Submit your project to a hackathon you were accepted to, during its event week: the "
            "hackathon's id, one of your repos, and a short pitch. Judges look at what's in the repo."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "opening_id": {"type": "string", "description": "The hackathon's id, e.g. 'hk-01'."},
                "repo": {"type": "string", "description": "Name of one of your repos on the code host."},
                "pitch": {"type": "string", "description": "What you built and why it matters."},
            },
            "required": ["opening_id", "repo", "pitch"],
        },
    },
    {
        "name": "gig",
        "description": (
            "Take gig work this week for guaranteed pay. It uses most of the week's actions. "
            "At most once per week."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "leave",
        "description": ("Leave a job or program you're in, by its commitment id (e.g. 'c4'). "
                        "Its weekly actions are free again from next week."),
        "input_schema": {
            "type": "object",
            "properties": {"commitment_id": {"type": "string"}},
            "required": ["commitment_id"],
        },
    },
    {
        "name": "wait",
        "description": ("End this week (free). Leave a one-line note about how the week went, and "
                        "the idea you're most drawn to right now."),
        "input_schema": {
            "type": "object",
            "properties": {
                "note": {"type": "string", "description": "One line about how the week went."},
                "idea": {"type": "string",
                         "description": ("The business or project idea you're most drawn to right now and "
                                         "why, in one or two sentences. Write 'none' if you have none.")},
            },
            "required": ["idea"],
        },
    },
]
TOOLS_BY_NAME = {t["name"]: t for t in TOOLS}


def tools_for(sandbox: bool, contacts: bool = True, feed: bool = False, hackathons: bool = False,
              training: bool = False) -> list[dict]:
    return [t for t in TOOLS if (sandbox or t["name"] not in SANDBOX_TOOLS)
            and (training or t["name"] not in TRAINING_TOOLS)
            and (contacts or t["name"] != "message")
            and (feed or t["name"] not in FEED_TOOLS)
            and (hackathons or t["name"] not in HACKATHON_TOOLS)]


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
    """The agent's files. With a sandbox, the agent's container can also write here (including
    symlinks, from background processes), so the host never follows a symlink: every open walks
    the path one component at a time with O_NOFOLLOW, and listings skip links."""

    MAX_FILE_BYTES = 64_000
    MAX_TOTAL_BYTES = 1_000_000

    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _parts(self, path: str | None) -> list[str]:
        if not path:
            raise ToolError("this files action needs a path, e.g. 'notes.md'")
        parts = [x for x in Path(path).parts if x not in ("", ".")]
        if not parts or Path(path).is_absolute() or ".." in parts:
            raise ToolError("path must be a file inside your workspace, e.g. 'notes.md' or 'app/main.py'")
        return parts

    def _open(self, parts: list[str], flags: int, create_dirs: bool = False) -> int:
        """Open root/parts without following any symlink; returns a file descriptor."""
        nofollow = os.O_NOFOLLOW | os.O_CLOEXEC
        dir_fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | nofollow)
        try:
            for part in parts[:-1]:
                try:
                    nxt = os.open(part, os.O_RDONLY | os.O_DIRECTORY | nofollow, dir_fd=dir_fd)
                except FileNotFoundError:
                    if not create_dirs:
                        raise
                    os.mkdir(part, 0o755, dir_fd=dir_fd)
                    nxt = os.open(part, os.O_RDONLY | os.O_DIRECTORY | nofollow, dir_fd=dir_fd)
                os.close(dir_fd)
                dir_fd = nxt
            return os.open(parts[-1], flags | nofollow, 0o644, dir_fd=dir_fd)
        except FileNotFoundError:
            raise ToolError(f"{'/'.join(parts)} does not exist")
        except (NotADirectoryError, IsADirectoryError):
            raise ToolError(f"{'/'.join(parts)} is not a file")
        except OSError as e:
            if e.errno == errno.ELOOP:
                raise ToolError(f"{'/'.join(parts)} is a symbolic link; the files tool doesn't follow links")
            raise
        finally:
            os.close(dir_fd)

    def _files(self) -> list[tuple[Path, int]]:
        out = []
        for dirpath, dirnames, filenames in os.walk(self.root):  # does not descend into dir links
            for name in filenames:
                st = os.lstat(os.path.join(dirpath, name))
                if stat.S_ISREG(st.st_mode):
                    out.append((Path(dirpath, name).relative_to(self.root), st.st_size))
        return sorted(out)

    def _total_bytes(self) -> int:
        return sum(size for _, size in self._files())

    def listing(self) -> str:
        files = [f for f in self._files() if ".git" not in f[0].parts]
        if not files:
            return "(empty)"
        return "\n".join(f"- {rel} ({size} bytes)" for rel, size in files)

    def _size(self, parts: list[str]) -> int:
        try:
            fd = self._open(parts, os.O_RDONLY)
        except ToolError:
            return 0
        try:
            return os.fstat(fd).st_size
        finally:
            os.close(fd)

    def read(self, path: str) -> str:
        fd = self._open(self._parts(path), os.O_RDONLY)
        with os.fdopen(fd, "rb") as f:
            if not stat.S_ISREG(os.fstat(f.fileno()).st_mode):
                raise ToolError(f"{path} is not a file")
            return f.read(self.MAX_FILE_BYTES * 4).decode(errors="replace")

    def run(self, action: str, path: str | None = None, content: str | None = None) -> str:
        if action == "list":
            return self.listing()
        if action == "read":
            return self.read(path)
        if action in ("write", "append"):
            parts = self._parts(path)
            rel = "/".join(parts)
            content = content or ""
            old = self._size(parts)
            new = len(content.encode()) + (old if action == "append" else 0)
            if new > self.MAX_FILE_BYTES:
                raise ToolError(f"file would be {new} bytes; the limit is {self.MAX_FILE_BYTES}")
            if self._total_bytes() - old + new > self.MAX_TOTAL_BYTES:
                raise ToolError(f"workspace quota is {self.MAX_TOTAL_BYTES} bytes")
            mode = os.O_APPEND if action == "append" else os.O_TRUNC
            fd = self._open(parts, os.O_WRONLY | os.O_CREAT | mode, create_dirs=True)
            with os.fdopen(fd, "w") as f:
                f.write(content)
            return f"{'appended to' if action == 'append' else 'wrote'} {rel} ({new} bytes)"
        raise ToolError(f"unknown action {action!r}")
