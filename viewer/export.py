"""Export one finished run as a self-contained replay page, scrubbed for publishing.

    uv run python -m viewer.export <run_id> [--out docs/replays/<run_id>] [--deny names.txt]

Writes <out>/index.html (the viewer page with the run's normalized events, grid summary, and
repo summaries embedded as JSON: no server, no fetches, no external fonts) and
a scrub report to exports/<run_id>-scrub-report.json (gitignored, outside the published
directory, since it lists the contacts for review). It reads only runs/; it never writes there.

The scrub runs on the exported data, after normalizing, so what's checked is what's published:
- local paths: the home directory becomes "~";
- email addresses become "[email removed]" (example.* and noreply domains kept); the match is
  loose and Unicode-aware, and an address still visible after NFKC normalization (fullwidth ＠)
  fails the export;
- dict keys get the same checks as values, and a key that would change fails the export;
- secrets: anything matching the key/token patterns, or any field named like a secret, fails
  the export: nothing is written, since a redacted key may still be a leaked key;
- contacts: the persona's contact names are listed for a person to confirm they're fictional;
  any name from --deny (one per line: real family, friends, classmates) found anywhere fails
  the export.
The report says what was found and changed. Review it and the page before publishing.
"""

import argparse
import hashlib
import html
import json
import re
import sys
import unicodedata
from pathlib import Path

import yaml

from gateway.github import SECRETS, allowed_email

from . import server
from .normalize import normalize

ROOT = Path(__file__).resolve().parent.parent
PAGE = Path(__file__).resolve().parent / "index.html"
REPORTS = ROOT / "exports"  # gitignored: scrub reports stay out of the published directory
EMAIL = re.compile(r"[^\s@<>\"'()\[\]{},;:]+@[^\s@<>\"'()\[\]{},;:]+\.[^\s@<>\"'()\[\]{},;:.]{2,}")
SECRET_KEY = re.compile(r"(?i)(api[_-]?key|secret|password|passwd|token)$")
FONT_LINKS = re.compile(r'\s*<link rel="(preconnect|stylesheet)" href="https://fonts\.(googleapis|gstatic)\.com[^>]*>')


class ScrubError(Exception):
    pass


def scrub(obj, report: dict, deny: list[str], home: str, path: str = ""):
    """A copy of obj with paths and emails replaced; secrets and denied names raise."""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if SECRET_KEY.search(str(k)) and v not in (None, "", 0, False) and not isinstance(v, (int, float, bool)):
                raise ScrubError(f"field named like a secret at {path}.{k}")
            if isinstance(k, str) and scrub(k, {"paths": 0, "emails": []}, deny, home, f"{path} key") != k:
                raise ScrubError(f"a key would need scrubbing at {path}: refusing rather than renaming it")
            out[k] = scrub(v, report, deny, home, f"{path}.{k}")
        return out
    if isinstance(obj, list):
        return [scrub(v, report, deny, home, f"{path}[{i}]") for i, v in enumerate(obj)]
    if not isinstance(obj, str):
        return obj
    s = obj
    for what, rx in SECRETS:
        if what == "email address":
            continue
        if rx.search(s.encode()):
            raise ScrubError(f"{what} at {path}")
    low = s.lower()
    for name in deny:
        if re.search(r"(?<!\w)" + re.escape(name.lower()) + r"(?!\w)", low):
            raise ScrubError(f"denied name {name!r} at {path}")
    if home and home in s:
        report["paths"] += s.count(home)
        s = s.replace(home, "~")
    for m in EMAIL.findall(s):
        if not allowed_email(m.encode()):
            report["emails"].append(hashlib.sha256(m.lower().encode()).hexdigest()[:8])
            s = s.replace(m, "[email removed]")
    for m in EMAIL.findall(unicodedata.normalize("NFKC", s)):
        if not allowed_email(m.encode()):
            raise ScrubError(f"an email address in a lookalike form at {path}")
    return s


def export(run_id: str, out: Path, deny: list[str]) -> dict:
    path = server._log_path(run_id)
    if path is None:
        raise SystemExit(f"no run log {run_id}.jsonl in {', '.join(map(str, server.runs_dirs))}")
    records = list(server._records(path))
    if not any(r.get("type") == "run_end" for r in records):
        raise SystemExit(f"{run_id} hasn't ended; export finished runs only")
    events = [ev for seq, rec in enumerate(records, 1) for ev in normalize(rec, seq)]
    summary = server.summarize(path)
    bundles = sorted(p for p in (path.parent / run_id / "repos").glob("*.bundle") if p.is_file() and not p.is_symlink())
    repos = [server.read_bundle(b) for b in bundles]
    for r in repos:
        r.pop("bundle_bytes", None)

    start = next(r for r in records if r.get("type") == "run_start")
    persona_text = (start.get("sources") or {}).get("persona_text")
    if not persona_text:
        raise SystemExit(f"{run_id}'s run_start has no persona_text; can't list its contacts for review")
    persona = yaml.safe_load(persona_text)
    report = {"run_id": run_id, "paths": 0, "emails": [], "events": len(events), "repos": [r["repo"] for r in repos],
              "deny_list": len(deny),
              "contacts": [{"id": c.get("id"), "name": c.get("name"), "known_as": c.get("known_as")}
                           for c in persona.get("contacts") or []],
              "contact_messages": sum(e["kind"] == "message" for e in events)}
    home = str(Path.home())
    data = scrub({"runs": [summary], "events": {run_id: events}, "repos": {run_id: repos}}, report, deny, home)

    page = PAGE.read_text()
    page = FONT_LINKS.sub("", page)
    blob = json.dumps(data, separators=(",", ":")).replace("</", "<\\/").replace("<!--", "<\\!--")
    page = page.replace("<title>", f"<title>{html.escape(run_id)} · ", 1)
    page = page.replace("<main id=\"app\"></main>",
                        f'<main id="app"></main>\n<script type="application/json" id="hl-replay">{blob}</script>', 1)
    if "https://" in FONT_LINKS.pattern and re.search(r'<link[^>]+href="https?://', page):
        raise ScrubError("the page still loads something external")
    report["bytes"] = len(page.encode())
    report["emails"] = sorted(set(report["emails"]))
    report = scrub(report, {"paths": 0, "emails": []}, deny, home)
    out.mkdir(parents=True, exist_ok=True)
    (out / "index.html").write_text(page)
    REPORTS.mkdir(exist_ok=True)
    (REPORTS / f"{run_id}-scrub-report.json").write_text(json.dumps(report, indent=1) + "\n")
    return report


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("run_id")
    ap.add_argument("--out", type=Path)
    ap.add_argument("--runs-dir", type=Path, action="append", help="repeatable; default runs/")
    ap.add_argument("--deny", type=Path, help="real names that must not appear, one per line (not committed)")
    a = ap.parse_args(argv)
    server.runs_dirs = [d.resolve() for d in a.runs_dir or server.runs_dirs]
    if not server.RUN_ID.match(a.run_id):
        sys.exit(f"bad run id {a.run_id!r}")
    deny = [l.strip() for l in a.deny.read_text().splitlines() if l.strip()] if a.deny else []
    out = a.out or ROOT / "docs" / "replays" / a.run_id
    try:
        report = export(a.run_id, out, deny)
    except ScrubError as e:
        sys.exit(f"export refused, nothing written: {e}")
    print(json.dumps({k: v for k, v in report.items() if k != "contacts"}, indent=1))
    print("contacts (confirm these are fictional):")
    for c in report["contacts"]:
        print(f"  {c['id']}: {c['name']} ({c['known_as']})")
    print(f"wrote {out / 'index.html'}; report in {REPORTS / (a.run_id + '-scrub-report.json')}")


if __name__ == "__main__":
    main()
