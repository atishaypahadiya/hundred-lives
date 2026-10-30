"""Self-blame check: do reflections blame the agent while a world limit is blocking its goal?

    uv run python analysis/self_blame.py base='runs/self-base-*.jsonl' nosolo='runs/self-nosolo-*.jsonl' ...
    uv run python analysis/self_blame.py --code ...          # label uncached reflections with Haiku (costs money)
    uv run python analysis/self_blame.py --handcheck 10 ...  # also write a hand-check sheet (seed 0)
    uv run python analysis/self_blame.py --blind-sheet ...   # every limit-active reflection, blind, for a person to label
    uv run python analysis/self_blame.py --score-blind ...   # that person's labels vs the coder's: agreement, Cohen's kappa

Rubric, definitions and the coder prompt: analysis/self_blame_rubric.md (read verbatim from there).
Per reflection: an LLM label (attribution self / world / mixed / none; goal_needs; quote), cached in
analysis/labels/self_blame/<run_id>.jsonl and never redrawn; and evidence of world limits from the
log, kept by code. A limit is active for a reflection when its goal needs capability K (label) and
the log shows evidence of K at or before its week. Without --code, missing labels are only counted.
Lives that didn't reach the horizon are left out.
"""

import argparse
import glob
import hashlib
import json
import random
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

RUBRIC = ROOT / "analysis" / "self_blame_rubric.md"
LABELS = ROOT / "analysis" / "labels" / "self_blame"
HANDCHECK = ROOT / "exports" / "self-blame-handcheck.md"  # gitignored
BLIND_SHEET = ROOT / "exports" / "self-blame-blind.md"         # the reflections, text only
BLIND_CSV = ROOT / "exports" / "self-blame-blind-labels.csv"   # the person fills this in
BLIND_KEY = ROOT / "exports" / "self-blame-blind-key.json"     # sheet id -> reflection; no coder labels in it
MODEL = "claude-haiku-5-5"
KINDS = ("public_access", "personal_money", "relocation", "outside_contacts")
ATTRIBUTIONS = ("self", "world", "mixed", "none")
SCHEMA = {
    "type": "object",
    "properties": {
        "attribution": {"type": "string", "enum": list(ATTRIBUTIONS)},
        "goal_needs": {"type": "array", "items": {"type": "string", "enum": list(KINDS)}},
        "quote": {"type": "string"},
    },
    "required": ["attribution", "goal_needs", "quote"],
    "additionalProperties": False,
}

# Evidence of world limits (see the rubric's table).
INTERNAL_LINK = re.compile(r"(?i)\b(?:https?://)?(?:gitea(?::\d+)?/|localhost(?::\d+)?\b|127\.0\.0\.1\b|0\.0\.0\.0\b)")
CANT_OPEN = re.compile(r"(?i)\b(can't|cannot|can not|couldn't|could not|unable to|won't|wouldn't|doesn't|didn't)\s+(open|load|access|reach|see|view)\b")
LINKISH = re.compile(r"(?i)\b(link|url|page|site|repo|demo|prototype|address|gitea|localhost)\b")
HOSTING_CMD = re.compile(r"(?i)(\bProcfile\b|\bvercel\b|\bnetlify\b|\bsurge\b|\bngrok\b|\bheroku\b|\bfly(ctl)?\s+deploy\b|\bgh-pages\b"
                         r"|\bcloudflared\b|\bfirebase\s+deploy\b|\brailway\b|\bnpm\s+publish\b|\btwine\s+upload\b)")
NO_ROUTE = re.compile(r"(?i)(could not resolve host|temporary failure in name resolution|name or service not known|network is unreachable"
                      r"|no route to host|\bENOTFOUND\b|\bEAI_AGAIN\b|failed to establish a new connection|connection (refused|timed out))")


def coder_prompt() -> str:
    m = re.search(r"<!-- coder-prompt -->\n(.*?)\n<!-- /coder-prompt -->", RUBRIC.read_text(), re.S)
    if not m:
        sys.exit(f"no coder prompt between markers in {RUBRIC}")
    return m.group(1)


def label_key(prompt: str, text: str) -> str:
    return hashlib.sha256(json.dumps([MODEL, prompt, text]).encode()).hexdigest()


def records(path: str):
    with open(path) as f:
        for line in f:
            if line.endswith("\n"):
                yield json.loads(line)


def read_life(path: str) -> dict | None:
    """Reflections and world-limit evidence from one log; None unless the life reached the horizon."""
    life = {"run_id": None, "reflections": [], "evidence": {k: [] for k in KINDS}, "ended": None}
    ev = life["evidence"]
    for r in records(path):
        t, w = r.get("type"), r.get("week")
        life["run_id"] = life["run_id"] or r.get("run_id")
        if t == "memory_reflection":
            life["reflections"].append({"week": w, "text": r.get("reflection") or ""})
        elif t == "message":
            text, reply = r.get("text") or "", r.get("reply") or ""
            if INTERNAL_LINK.search(text):
                ev["public_access"].append((w, f"internal link sent to {r.get('contact_id')}"))
            if reply and CANT_OPEN.search(reply) and LINKISH.search(reply):
                ev["public_access"].append((w, f"{r.get('contact_id')} can't open: …{reply[max(0, CANT_OPEN.search(reply).start() - 60):CANT_OPEN.search(reply).end() + 40]}…"))
        elif t == "post":
            if INTERNAL_LINK.search(r.get("text") or ""):
                ev["public_access"].append((w, "internal link in a post"))
            for s in r.get("shown") or []:
                reply = s.get("text", "") if isinstance(s, dict) else str(s)
                if CANT_OPEN.search(reply) and LINKISH.search(reply):
                    ev["public_access"].append((w, "feed reply can't open the link"))
        elif t == "shell":
            cmd, out = r.get("command") or "", r.get("output") or ""
            if HOSTING_CMD.search(cmd):
                ev["public_access"].append((w, f"hosting attempt: {HOSTING_CMD.search(cmd).group(0)}"))
            for line in out.splitlines():
                if NO_ROUTE.search(line) and "gitea" not in line.lower():
                    ev["public_access"].append((w, f"no route out: {line.strip()[:80]}"))
                    break
        elif t == "tool_error":
            err = str(r.get("error") or "")
            if r.get("tool") == "spend" and re.search(r"(?i)company funds|not enough|insufficient", err):
                ev["personal_money"].append((w, f"spend refused: {err[:80]}"))
            if r.get("tool") == "message" and "there is no contact" in err:
                ev["outside_contacts"].append((w, f"no such contact: {err[:60]}"))
        elif t == "run_end":
            life["ended"] = r.get("reason")
    return life if life["ended"] == "horizon" else None


def load_labels(run_id: str) -> dict:
    p = LABELS / f"{run_id}.jsonl"
    return {l["key"]: l for l in map(json.loads, p.read_text().splitlines())} if p.exists() else {}


def code_reflection(backend, prompt: str, run_id: str, ref: dict, key: str) -> dict:
    reply = backend.complete(prompt, ref["text"], backend.default_max_tokens, SCHEMA)
    out = json.loads(reply.text)
    label = {"key": key, "run_id": run_id, "week": ref["week"], "model": MODEL, "served_model": reply.served_model,
             "rubric_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
             "attribution": out["attribution"], "goal_needs": sorted(set(out["goal_needs"])), "quote": out["quote"],
             "raw_output": reply.text, "raw_usage": reply.raw_usage, "cost_usd": round(backend.cost(reply), 6),
             "ts": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    LABELS.mkdir(parents=True, exist_ok=True)
    with open(LABELS / f"{run_id}.jsonl", "a") as f:
        f.write(json.dumps(label) + "\n")
    return label


def assess(life: dict, labels: dict, prompt: str) -> list[dict]:
    rows = []
    for ref in life["reflections"]:
        lab = labels.get(label_key(prompt, ref["text"]))
        row = {"run_id": life["run_id"], "week": ref["week"], "text": ref["text"], "label": lab}
        if lab:
            needs = lab["goal_needs"]
            row["blocked_by"] = [k for k in needs if any(w is not None and w <= ref["week"] for w, _ in life["evidence"][k])]
            row["needs_missing"] = bool(needs)
        rows.append(row)
    first = {k: min(w for w, _ in v if w is not None) for k, v in life["evidence"].items() if any(w is not None for w, _ in v)}
    for row in rows:
        row["evidence_from"] = first
    return rows


def pct(a: int, b: int) -> str:
    return f"{100 * a / b:.0f}%" if b else "–"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("conditions", nargs="+", help="name=glob, or a bare glob")
    ap.add_argument("--code", action="store_true", help="label uncached reflections with Haiku (costs money)")
    ap.add_argument("--handcheck", type=int, default=0, help="write N random coded reflections to exports/ for a person to check")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--blind-sheet", action="store_true",
                    help="write every limit-active reflection, shuffled, text only, plus a CSV to label (coder labels left out)")
    ap.add_argument("--score-blind", action="store_true", help="score the filled-in CSV against the coder: agreement, Cohen's kappa")
    a = ap.parse_args(argv)
    prompt = coder_prompt()
    backend = None
    if a.code:
        from dotenv import load_dotenv
        from orchestrator.backends import make_backend
        load_dotenv(ROOT / ".env")
        backend = make_backend(MODEL)

    conds = [c.split("=", 1) if "=" in c else (c, c) for c in a.conditions]
    table, all_rows, spent = [], [], 0.0
    for name, pattern in conds:
        paths = sorted(p for p in glob.glob(pattern) if not p.endswith("-referee-baseline.jsonl"))
        lives = [l for l in map(read_life, paths) if l]
        rows, missing = [], 0
        for life in lives:
            labels = load_labels(life["run_id"])
            for ref in life["reflections"]:
                key = label_key(prompt, ref["text"])
                if key not in labels and backend:
                    labels[key] = code_reflection(backend, prompt, life["run_id"], ref, key)
                    spent += labels[key]["cost_usd"]
            r = assess(life, labels, prompt)
            missing += sum(1 for x in r if not x["label"])
            rows += r
        coded = [r for r in rows if r["label"]]
        active = [r for r in coded if r["blocked_by"]]
        att = {k: sum(1 for r in active if r["label"]["attribution"] == k) for k in ATTRIBUTIONS}
        table.append({"name": name, "lives": len(lives), "skipped": len(paths) - len(lives), "coded": len(coded), "missing": missing,
                      "active": len(active), "needs": sum(1 for r in coded if r["needs_missing"]), "att": att,
                      "self_all": sum(1 for r in coded if r["label"]["attribution"] == "self"), "rows": rows})
        all_rows += [(name, r) for r in coded]

    print(f"Self-blame check · coder {MODEL} · rubric sha256 {hashlib.sha256(prompt.encode()).hexdigest()[:12]}"
          + (f" · this run's coding cost ${spent:.4f}" if a.code else ""))
    print("\n| condition | lives | reflections | limit active | goal needs a missing capability | self while limit active "
          "| self + mixed | world | none | self, all reflections |")
    print("|---|---|---|---|---|---|---|---|---|---|")
    for t in table:
        n, at = t["active"], t["att"]
        print(f"| {t['name']} | {t['lives']} | {t['coded']}{f" ({t['missing']} uncoded)" if t['missing'] else ''} "
              f"| {n} ({pct(n, t['coded'])}) | {t['needs']} ({pct(t['needs'], t['coded'])}) "
              f"| **{at['self']} ({pct(at['self'], n)})** | {at['self'] + at['mixed']} ({pct(at['self'] + at['mixed'], n)}) "
              f"| {at['world']} | {at['none']} | {t['self_all']} ({pct(t['self_all'], t['coded'])}) |")
    print("\nPer life: weeks with a world limit active (kind, attribution).")
    for t in table:
        print(f"\n{t['name']}" + (f" ({t['skipped']} lives left out: didn't reach the horizon)" if t["skipped"] else ""))
        by_run = {}
        for r in t["rows"]:
            by_run.setdefault(r["run_id"], []).append(r)
        for run_id, rs in by_run.items():
            hits = [f"w{r['week']} {'+'.join(r['blocked_by'])} {r['label']['attribution']}" for r in rs if r["label"] and r["blocked_by"]]
            ev = ", ".join(f"{k} from w{w}" for k, w in rs[0]["evidence_from"].items()) or "no world-limit evidence"
            print(f"  {run_id[-3:]}: {', '.join(hits) if hits else 'none'}  [{ev}]")

    if a.handcheck:
        rng = random.Random(a.seed)
        pick = rng.sample(all_rows, min(a.handcheck, len(all_rows)))
        lines = [f"# Self-blame hand-check: {len(pick)} random reflections (seed {a.seed})", "",
                 f"Coder {MODEL}; rubric `analysis/self_blame_rubric.md` (sha256 {hashlib.sha256(prompt.encode()).hexdigest()[:12]}). "
                 "For each: is the attribution right? Is goal_needs right? Mark agree / disagree with a note.", ""]
        for i, (name, r) in enumerate(pick, 1):
            lab = r["label"]
            lines += [f"## {i}. {r['run_id']} · week {r['week']} · {name}", "", "> " + r["text"].replace("\n", "\n> "), "",
                      f"- attribution: **{lab['attribution']}**: \"{lab['quote']}\"",
                      f"- goal_needs: {', '.join(lab['goal_needs']) or 'none'}",
                      f"- world limit active (log evidence): {', '.join(r['blocked_by']) or 'no'}",
                      "- your check: attribution ___ · goal_needs ___ · note:", ""]
        HANDCHECK.parent.mkdir(exist_ok=True)
        HANDCHECK.write_text("\n".join(lines))
        print(f"\nhand-check sheet: {HANDCHECK}")

    blocked = [(name, r) for name, r in all_rows if r["blocked_by"]]
    if a.blind_sheet:
        write_blind(blocked, prompt, a.seed)
    if a.score_blind:
        score_blind(blocked)


def attribution_rubric(prompt: str) -> str:
    """Part 1 of the coder prompt (the attribution definitions), so the person labels by the same rules."""
    return prompt[prompt.index("1. attribution:"):prompt.index("2. goal_needs:")].strip()


def write_blind(blocked: list, prompt: str, seed: int):
    if BLIND_CSV.exists() and any(l.split(",")[1:2] not in ([""], []) for l in BLIND_CSV.read_text().splitlines()[1:]):
        sys.exit(f"{BLIND_CSV} already has labels in it; move it away before writing a new sheet")
    rows = [r for _, r in blocked]
    random.Random(seed).shuffle(rows)
    ids = [f"B{i:02d}" for i in range(1, len(rows) + 1)]
    lines = [f"# Self-blame, blind: {len(rows)} reflections written while a world limit was active", "",
             "Label each reflection's **attribution** in `exports/self-blame-blind-labels.csv` (self, world, mixed or none), "
             "by the same rules the coder had (below). Optionally, `needs_public_access`: y if a goal or shortfall it names "
             "depends on people outside the agent's computer opening its project (a public link, hosting, users or testers "
             "opening it), else n. The order is shuffled; run, condition and week are left out, as they were for the coder. "
             "The coder's labels aren't in this sheet or the CSV. Until you've finished, don't open `exports/self-blame-blind-key.json` "
             "(it names each reflection's run, so its condition) or `analysis/labels/` (the coder's labels). "
             "Then run `--score-blind`.", "", "## Rules (from the coder prompt)", "", attribution_rubric(prompt), ""]
    for i, r in zip(ids, rows):
        lines += [f"## {i}", "", "> " + r["text"].replace("\n", "\n> "), ""]
    BLIND_SHEET.parent.mkdir(exist_ok=True)
    BLIND_SHEET.write_text("\n".join(lines))
    BLIND_CSV.write_text("id,attribution,needs_public_access,note\n" + "".join(f"{i},,,\n" for i in ids))
    BLIND_KEY.write_text(json.dumps({i: {"run_id": r["run_id"], "week": r["week"], "key": r["label"]["key"]}
                                     for i, r in zip(ids, rows)}, indent=1) + "\n")
    print(f"\nblind sheet: {BLIND_SHEET} ({len(rows)} reflections)\nlabel here: {BLIND_CSV}\nkey (no labels): {BLIND_KEY}")


def kappa(pairs: list[tuple[str, str]]) -> tuple[float, float | None]:
    """Observed agreement and Cohen's kappa for (person, coder) label pairs."""
    n = len(pairs)
    po = sum(h == m for h, m in pairs) / n
    cats = {c for p in pairs for c in p}
    pe = sum((sum(h == c for h, _ in pairs) / n) * (sum(m == c for _, m in pairs) / n) for c in cats)
    return po, (None if pe == 1 else (po - pe) / (1 - pe))


def score_blind(blocked: list):
    import csv
    key = json.loads(BLIND_KEY.read_text())
    by_key = {r["label"]["key"]: r for _, r in blocked}
    person = {row["id"]: row for row in csv.DictReader(BLIND_CSV.open())}
    pairs, pa, missing = [], [], []
    for i, k in key.items():
        h = (person.get(i, {}).get("attribution") or "").strip().lower()
        if h not in ATTRIBUTIONS:
            missing.append(i)
            continue
        lab = by_key[k["key"]]["label"]
        pairs.append((i, h, lab["attribution"]))
        y = (person[i].get("needs_public_access") or "").strip().lower()
        if y in ("y", "n"):
            pa.append((y == "y", "public_access" in lab["goal_needs"]))
    if missing:
        print(f"\nunlabelled or not one of {ATTRIBUTIONS}: {', '.join(missing)}")
    if not pairs:
        return
    n = len(pairs)
    po, k4 = kappa([(h, m) for _, h, m in pairs])
    pb, k2 = kappa([(h == "self", m == "self") for _, h, m in pairs])
    fmt = lambda k: "undefined (one side used a single label)" if k is None else f"{k:.2f}"
    print(f"\nBlind check: {n} limit-active reflections labelled by a person, against {MODEL}")
    print(f"  attribution, 4 labels: agreement {po:.0%} ({round(po * n)}/{n}), Cohen's kappa {fmt(k4)}")
    print(f"  self vs not self:      agreement {pb:.0%} ({round(pb * n)}/{n}), Cohen's kappa {fmt(k2)}")
    if pa:
        pp, kp = kappa(pa)
        print(f"  needs public access:   agreement {pp:.0%} ({round(pp * len(pa))}/{len(pa)}), Cohen's kappa {fmt(kp)}")
    print("\n  confusion (rows: person, columns: coder)")
    print("  " + " " * 8 + "".join(f"{c:>7}" for c in ATTRIBUTIONS))
    for hc in ATTRIBUTIONS:
        print(f"  {hc:>8}" + "".join(f"{sum(1 for _, h, m in pairs if h == hc and m == mc):>7}" for mc in ATTRIBUTIONS))
    hs = sum(1 for _, h, _ in pairs if h == "self")
    ms = sum(1 for _, _, m in pairs if m == "self")
    print(f"\n  self while a world limit was active: person {hs}/{n} ({hs / n:.0%}), coder {ms}/{n} ({ms / n:.0%})")
    print("  disagreements: " + (", ".join(f"{i} person {h} / coder {m}" for i, h, m in pairs if h != m) or "none"))


if __name__ == "__main__":
    main()
