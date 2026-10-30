"""Export finished runs, scrubbed for publishing: one self-contained replay, or the whole site.

    uv run python -m viewer.export <run_id> [--out docs/replays/<run_id>] [--deny names.txt]
    uv run python -m viewer.export --all --runs-dir runs --runs-dir ../other/runs [--out docs/runs] [--deny names.txt]

One run: writes <out>/index.html (the viewer page with the run's normalized events, grid summary,
and repo summaries embedded as JSON: no server, no fetches, no external fonts) and a scrub report
to exports/<run_id>-scrub-report.json (gitignored, outside the published directory, since it lists
the contacts for review).

--all: publishes every finished run that viewer/publish.yaml selects (see that file for the rules)
as a static site: <out>/index.html (the grid, grouped by experiment), <out>/runs.json (one trimmed
summary per run), <out>/<run_id>/index.html + data.json (one life, fetched when opened), and
<out>/assets/viewer.{css,js} shared by every page. It's built in a temporary directory and replaces
<out> only if every run passes the scrub; one refusal and nothing is written. The combined report
is exports/runs-scrub-report.json. It reads only the runs dirs; it never writes there.

The scrub runs on the exported data, after normalizing, so what's checked is what's published:
- local paths: the home directory becomes "~";
- email addresses become "[email removed]" (example.* and noreply domains kept); the match is
  loose and Unicode-aware, and an address still visible after NFKC normalization (fullwidth ＠)
  fails the export;
- dict keys get the same checks as values, and a key that would change fails the export;
- secrets: anything matching the key/token patterns, a URL with credentials in it
  (scheme://user:password@host, as in a git remote or ~/.git-credentials), or any field named like
  a secret, fails the export: nothing is written, since a redacted key may still be a leaked key;
- contacts: the persona's contact names are listed for a person to confirm they're fictional;
  any name from --deny (one per line: real family, friends, classmates) found anywhere fails
  the export.
The report says what was found and changed. Review it and the page before publishing.
"""

import argparse
import fnmatch
import hashlib
import html
import json
import re
import shutil
import sys
import tempfile
import unicodedata
from pathlib import Path

import yaml

from gateway.github import SECRETS, allowed_email

from . import server
from .normalize import normalize

ROOT = Path(__file__).resolve().parent.parent
PAGE = Path(__file__).resolve().parent / "index.html"
PUBLISH = Path(__file__).resolve().parent / "publish.yaml"
REPORTS = ROOT / "exports"  # gitignored: scrub reports stay out of the published directory
EMAIL = re.compile(r"[^\s@<>\"'()\[\]{},;:]+@[^\s@<>\"'()\[\]{},;:]+\.[^\s@<>\"'()\[\]{},;:.]{2,}")
SECRET_KEY = re.compile(r"(?i)(api[_-]?key|secret|password|passwd|token)$")
URL_CREDENTIALS = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://[^/\s:@]+:[^/\s@]+@")
SITE_MARKER = "runs.json"  # --all only ever replaces a directory that it made (or an empty one)
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
    if URL_CREDENTIALS.search(s):
        raise ScrubError(f"credentials in a URL at {path}")
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


def build(run_id: str, deny: list[str]) -> tuple[dict, dict]:
    """One finished run's scrubbed viewer data ({runs, events, repos}) and its scrub report."""
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
              "deny_list": len(deny), "persona": persona.get("name"),
              "contacts": [{"id": c.get("id"), "name": c.get("name"), "known_as": c.get("known_as")}
                           for c in persona.get("contacts") or []],
              "contact_messages": sum(e["kind"] == "message" for e in events)}
    home = str(Path.home())
    data = scrub({"runs": [summary], "events": {run_id: events}, "repos": {run_id: repos}}, report, deny, home)
    report["emails"] = sorted(set(report["emails"]))
    return data, report


def _page() -> str:
    page = FONT_LINKS.sub("", PAGE.read_text())
    if re.search(r'<link[^>]+href="https?://', page):
        raise ScrubError("the page still loads something external")
    return page


def export(run_id: str, out: Path, deny: list[str]) -> dict:
    data, report = build(run_id, deny)
    home = str(Path.home())
    blob = json.dumps(data, separators=(",", ":")).replace("</", "<\\/").replace("<!--", "<\\!--")
    page = _page().replace("<title>", f"<title>{html.escape(run_id)} · ", 1)
    page = page.replace("<main id=\"app\"></main>",
                        f'<main id="app"></main>\n<script type="application/json" id="hl-replay">{blob}</script>', 1)
    report["bytes"] = len(page.encode())
    report = scrub(report, {"paths": 0, "emails": []}, deny, home)
    out.mkdir(parents=True, exist_ok=True)
    (out / "index.html").write_text(page)
    REPORTS.mkdir(exist_ok=True)
    (REPORTS / f"{run_id}-scrub-report.json").write_text(json.dumps(report, indent=1) + "\n")
    return report


# ---------- the whole site (--all) ----------

def select(publish: dict) -> tuple[list[dict], list[dict]]:
    """Runs to publish, in page order (group, batch, run id), and every other run with why it's out."""
    stems = sorted({p.stem for d in server.runs_dirs for p in d.glob("*.jsonl")})
    chosen, skipped = [], []
    for stem in stems:
        path = server._log_path(stem)
        if path is None:  # batch baselines and bad names
            continue
        batch = re.sub(r"-\d{3}$", "", stem)
        hit = next(((gi, bi, g, b) for gi, g in enumerate(publish["groups"]) for bi, b in enumerate(g["batches"])
                    if fnmatch.fnmatchcase(batch, b["batch"])), None)
        if hit is None:
            skipped.append({"run_id": stem, "why": "not in viewer/publish.yaml"})
            continue
        s = server.summarize(path)
        agent = (s.get("models") or {}).get("agent") or ""
        why = ("unfinished" if s["status"] != "ended" else
               f"ended: {s.get('reason')}" if s.get("reason") != "horizon" else
               "PILOT (uncommitted or unrecorded code)" if s.get("pilot") else
               "Ollama agent (plumbing run)" if agent.startswith("ollama:") or "qwen" in agent else None)
        if why:
            skipped.append({"run_id": stem, "why": why})
            continue
        gi, bi, g, b = hit
        chosen.append({"run_id": stem, "order": (gi, bi, stem), "group": g["id"], "batch": batch, "batch_label": b["label"],
                       "dir": str(path.parent)})
    chosen.sort(key=lambda c: c["order"])
    return chosen, skipped


GRID_WEEK = ("week", "cash", "commitment", "school", "gig", "broke")  # what a tile draws; the life page has the rest


def grid_entry(summary: dict, c: dict) -> dict:
    keep = {k: v for k, v in summary.items() if k != "weeks"}
    keep["weeks"] = [{k: w.get(k) for k in GRID_WEEK} for w in summary.get("weeks") or []]
    return {**keep, "group": c["group"], "batch": c["batch"], "batch_label": c["batch_label"]}


def _split_page(page: str) -> tuple[str, str, str]:
    """The viewer page as a shell plus its one <style> and one <script>, so every page can share them."""
    css = re.findall(r"<style>(.*?)</style>", page, re.S)
    js = re.findall(r"<script>(.*?)</script>", page, re.S)
    if len(css) != 1 or len(js) != 1:
        raise SystemExit(f"viewer/index.html should have one <style> and one <script>; found {len(css)} and {len(js)}")
    shell = re.sub(r"<style>.*?</style>", lambda m: "<!--CSS-->", page, count=1, flags=re.S)
    shell = re.sub(r"<script>.*?</script>", lambda m: "<!--JS-->", shell, count=1, flags=re.S)
    return shell, css[0], js[0]


def _stub(shell: str, title: str, assets: str, config: dict) -> str:
    blob = json.dumps(config, separators=(",", ":")).replace("</", "<\\/")
    page = re.sub(r"<title>.*?</title>", f"<title>{html.escape(title)}</title>", shell, count=1)
    page = page.replace("<!--CSS-->", f'<link rel="stylesheet" href="{assets}viewer.css">', 1)
    page = page.replace("<main id=\"app\"></main>",
                        f'<main id="app"></main>\n<script type="application/json" id="hl-static">{blob}</script>', 1)
    return page.replace("<!--JS-->", f'<script src="{assets}viewer.js"></script>', 1)


def export_all(out: Path, deny: list[str]) -> dict:
    publish = yaml.safe_load(PUBLISH.read_text())
    chosen, skipped = select(publish)
    if not chosen:
        raise SystemExit("no finished runs match viewer/publish.yaml")
    if out.exists() and any(out.iterdir()) and not (out / SITE_MARKER).is_file():
        raise SystemExit(f"{out} exists and wasn't made by --all (no {SITE_MARKER}); refusing to replace it")
    home = str(Path.home())
    shell, css, js = _split_page(_page())
    reports, refused, grid = [], [], []
    tmp = Path(tempfile.mkdtemp(prefix=".runs-", dir=out.parent))
    try:
        for c in chosen:
            try:
                data, report = build(c["run_id"], deny)
            except ScrubError as e:
                refused.append({"run_id": c["run_id"], "why": str(e)})
                continue
            d = tmp / c["run_id"]
            d.mkdir()
            blob = json.dumps(data, separators=(",", ":"))
            (d / "data.json").write_text(blob)
            (d / "index.html").write_text(_stub(shell, f"{c['run_id']} · Hundred Lives", "../assets/",
                                                {"page": "run", "run_id": c["run_id"], "data": "data.json", "grid": "../", "home": "../../"}))
            report["bytes"] = len(blob.encode())
            reports.append(scrub(report, {"paths": 0, "emails": []}, deny, home))
            grid.append(grid_entry(data["runs"][0], c))
        if refused:
            raise ScrubError("; ".join(f"{r['run_id']}: {r['why']}" for r in refused))
        groups = [{k: g.get(k) for k in ("id", "short", "title", "note")} for g in publish["groups"]
                  if any(r["group"] == g["id"] for r in grid)]
        (tmp / "runs.json").write_text(json.dumps({"groups": groups, "runs": grid}, separators=(",", ":")))
        (tmp / "index.html").write_text(_stub(shell, "Runs · Hundred Lives", "assets/", {"page": "grid", "runs": "runs.json", "home": "../"}))
        (tmp / "assets").mkdir()
        (tmp / "assets" / "viewer.css").write_text(css)
        (tmp / "assets" / "viewer.js").write_text(js)
        for f in tmp.rglob("*"):  # what's published is what was checked: every file, once more, as text
            if f.is_file():
                text = f.read_text()
                for what, rx in SECRETS:
                    if what != "email address" and rx.search(text.encode()):
                        raise ScrubError(f"{what} in {f.relative_to(tmp)}")
                if URL_CREDENTIALS.search(text):
                    raise ScrubError(f"credentials in a URL in {f.relative_to(tmp)}")
                if home in text:
                    raise ScrubError(f"the home path in {f.relative_to(tmp)}")
        size = sum(f.stat().st_size for f in tmp.rglob("*") if f.is_file())
        if out.exists():
            shutil.rmtree(out)
        tmp.rename(out)
    finally:
        if tmp.exists():
            shutil.rmtree(tmp, ignore_errors=True)
    personas = {}
    for r in reports:
        p = personas.setdefault(r["persona"], {"runs": 0, "contacts": {}})
        p["runs"] += 1
        for ct in r["contacts"]:
            p["contacts"][ct["id"]] = ct
    summary = {"out": str(out), "runs": len(reports), "skipped": skipped, "bytes": size,
               "data_bytes": sum(r["bytes"] for r in reports), "paths": sum(r["paths"] for r in reports),
               "emails": sorted({e for r in reports for e in r["emails"]}),
               "runs_with_emails": [r["run_id"] for r in reports if r["emails"]],
               "events": sum(r["events"] for r in reports), "contact_messages": sum(r["contact_messages"] for r in reports),
               "deny_list": len(deny),
               "personas": {k: {"runs": v["runs"], "contacts": list(v["contacts"].values())} for k, v in personas.items()},
               "per_run": [{k: r[k] for k in ("run_id", "events", "bytes", "paths", "emails", "repos", "contact_messages")}
                           for r in reports]}
    REPORTS.mkdir(exist_ok=True)
    (REPORTS / "runs-scrub-report.json").write_text(json.dumps(summary, indent=1) + "\n")
    return summary


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("run_id", nargs="?")
    ap.add_argument("--all", action="store_true", help="every finished run viewer/publish.yaml selects, as a static site")
    ap.add_argument("--out", type=Path)
    ap.add_argument("--runs-dir", type=Path, action="append", help="repeatable; default runs/")
    ap.add_argument("--deny", type=Path, help="real names that must not appear, one per line (not committed)")
    a = ap.parse_args(argv)
    if bool(a.all) == bool(a.run_id):
        ap.error("give a run id or --all")
    server.runs_dirs = [d.resolve() for d in a.runs_dir or server.runs_dirs]
    deny = [l.strip() for l in a.deny.read_text().splitlines() if l.strip()] if a.deny else []
    if a.all:
        out = (a.out or ROOT / "docs" / "runs").resolve()
        try:
            s = export_all(out, deny)
        except ScrubError as e:
            sys.exit(f"export refused, nothing written: {e}")
        print(f"published {s['runs']} runs to {out}: {s['bytes'] / 1e6:.1f} MB in all ({s['data_bytes'] / 1e6:.1f} MB of run data)")
        print(f"scrub: {s['paths']} home paths -> ~, {len(s['emails'])} distinct emails removed "
              f"(in {len(s['runs_with_emails'])} runs), no secrets, no URL credentials; deny list: {s['deny_list']} names")
        print(f"{s['events']:,} events, {s['contact_messages']} contact messages")
        for name, p in s["personas"].items():
            print(f"contacts of {name} ({p['runs']} runs; confirm these are fictional):")
            for c in p["contacts"]:
                print(f"  {c['id']}: {c['name']} ({c['known_as']})")
        why = {}
        for k in s["skipped"]:
            why.setdefault(k["why"], []).append(k["run_id"])
        print("not published:")
        for w, ids in why.items():
            print(f"  {len(ids):3d} {w}: {', '.join(ids[:4])}{' …' if len(ids) > 4 else ''}")
        print(f"report in {REPORTS / 'runs-scrub-report.json'}")
        return
    if not server.RUN_ID.match(a.run_id):
        sys.exit(f"bad run id {a.run_id!r}")
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
