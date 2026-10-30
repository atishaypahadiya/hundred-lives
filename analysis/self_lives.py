"""Per-life social and building profile: is a pattern a trait of the persona or one life's path?

    uv run python analysis/self_lives.py 'runs/self-base-*.jsonl'
    uv run python analysis/self_lives.py base='runs/self-base-*.jsonl' nosolo='runs/self-nosolo-*.jsonl' ...

One row per run, then the spread across runs:
  - messages sent / replied per contact (every contact in the persona, zeros included), offers taken;
  - posts, likes (total, best), followers at the end;
  - commits (from the normalized git events, quiet ones included), repos, shell calls;
  - cheap bets: applications to accelerators, angels, grants, student programs and hackathons
    (with successes), launches, hackathon placings, company spend;
  - costly bets, the irreversible commitments, from the log alone:
      quit: `leave` of paid work (job, internship, campus job, freelance);
      declined: an accepted offer of paid work that lapsed because nothing was left to make room
        (lapses forced by work authorization don't count);
      full-time on the venture: a full-time accelerator started.
    The log doesn't say why an agent quit or let an offer lapse, so "for the venture" isn't checked.
    Two kinds have no mechanic in this world and are 0 by construction: spending >= 25% of savings
    (personal cash can't be spent; `spend` draws on company funds only) and relocating;
  - training: sessions, weeks with a session, streak at the end; final cash and commitments;
  - where the actions went: the share of the life's action-using calls (tools that cost an action;
    gigs and free calls left out) that were train, post and message.
Runs that didn't reach the horizon are marked and kept out of the spread. With name=glob
arguments (conditions), each condition gets the report, then one comparison table: per life,
messages sent, distinct contacts messaged, posts, followers, cheap bets (and wins), costly bets, commits,
training sessions and weeks, and the train / post / message / post+message shares of actions (%), as mean
[min–max] over the lives that reached the horizon.
"""

import glob
import json
import statistics
import sys
from collections import Counter
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from orchestrator.tools import ACTION_COST  # noqa: E402
from viewer.normalize import normalize  # noqa: E402

CHEAP_BETS = ("accelerator", "angel", "grant", "student_program", "hackathon")
PAID_WORK = ("job", "internship", "on_campus", "freelance")


def profile(path: str) -> dict:
    recs = [json.loads(line) for line in open(path)]
    start = next(r for r in recs if r["type"] == "run_start")
    end = next((r for r in recs if r["type"] == "run_end"), None)
    persona = yaml.safe_load((start.get("sources") or {}).get("persona_text") or "{}")
    contacts = {c["id"]: c["name"] for c in persona.get("contacts") or []}
    of = lambda t: [r for r in recs if r["type"] == t]
    msgs = {cid: [0, 0] for cid in contacts}
    for m in of("message"):
        msgs.setdefault(m["contact_id"], [0, 0])
        msgs[m["contact_id"]][0] += 1
        msgs[m["contact_id"]][1] += bool(m.get("replied"))
    events = [e for seq, r in enumerate(recs, 1) for e in normalize(r, seq)]
    posts = of("post")
    bets = [d for d in of("referee_decision") if d.get("opening_type") in CHEAP_BETS]
    world = {o["id"]: o for o in yaml.safe_load((start.get("sources") or {}).get("world_text") or "{}").get("openings") or []}
    op = lambda oid: world.get(oid) or {}
    costly = ([f"w{e['week']} quit {e['commitment']['opening_id']}" for e in of("commitment_ended")
               if e.get("reason") == "left" and op(e["commitment"].get("opening_id")).get("type") in PAID_WORK]
              + [f"w{e['week']} declined {e['opening_id']}" for e in of("offer_lapsed")
                 if not e.get("reason") and op(e["opening_id"]).get("type") in PAID_WORK]
              + [f"w{e['week']} full-time {e['commitment']['opening_id']}" for e in of("commitment_started")
                 if op(e["commitment"].get("opening_id")).get("type") == "accelerator"
                 and op(e["commitment"].get("opening_id")).get("schedule") == "full_time"])
    last = (of("week_end") or [{}])[-1].get("state") or {}
    calls = Counter(r["tool"] for r in of("tool_call") if ACTION_COST.get(r["tool"], 0) >= 1)
    share = lambda t: round(100 * calls[t] / max(1, sum(calls.values())))
    return {
        "run": Path(path).stem, "dice_seed": start.get("dice_seed"), "order_seed": start.get("order_seed"),
        "reason": (end or {}).get("reason", "running"), "weeks": len(of("week_end")),
        "contacts": contacts, "messages": msgs, "offers": [(o["week"], o.get("contact_id") or o.get("contact"), o.get("offer", {}).get("kind") if isinstance(o.get("offer"), dict) else o.get("kind"))
                                                         for o in of("contact_offer")],
        "posts": len(posts), "likes": sum(p["likes"] for p in posts), "best_likes": max((p["likes"] for p in posts), default=0),
        "followers": posts[-1]["followers_after"] if posts else 0,
        "commits": sum(e["kind"] == "git_commit" for e in events), "repos": len(of("repo_created")), "shell": len(of("shell")),
        "bets": len(bets), "bet_wins": sum(d["outcome"] == "success" for d in bets),
        "bet_list": [f"w{d['week']} {d['opening_id']}→{d['outcome']}" for d in bets],
        "costly": len(costly), "costly_list": costly,
        "launches": len(of("launch")), "placed": sum(h["outcome"] == "placed" for h in of("hackathon_result")),
        "spend": len(of("spend")), "trained_weeks": last.get("trained_weeks"), "streak": last.get("train_streak"),
        "train_sessions": calls["train"], "share": {t: share(t) for t in ("train", "post", "message")},
        "post_message_share": round(100 * (calls["post"] + calls["message"]) / max(1, sum(calls.values()))),
        "cash": last.get("cash"), "commitments": [c.get("label") for c in last.get("commitments") or [] if c.get("kind") != "academics"],
    }


COMPARE = [("messages / life", lambda r: sum(s for s, _ in r["messages"].values())),
           ("distinct contacts", lambda r: sum(s > 0 for s, _ in r["messages"].values())),
           ("posts", lambda r: r["posts"]), ("followers", lambda r: r["followers"]),
           ("cheap bets", lambda r: r["bets"]), ("cheap bets won", lambda r: r["bet_wins"]),
           ("costly bets", lambda r: r["costly"]),
           ("commits", lambda r: r["commits"]),
           ("train sessions", lambda r: r["train_sessions"]), ("trained weeks", lambda r: r["trained_weeks"] or 0),
           ("train share %", lambda r: r["share"]["train"]), ("post share %", lambda r: r["share"]["post"]),
           ("message share %", lambda r: r["share"]["message"]),
           ("post+message %", lambda r: r["post_message_share"])]


def main(args: list[str]):
    conds = [a.split("=", 1) if "=" in a else ["runs", a] for a in args]
    done = {}
    for name, pat in conds:
        if len(conds) > 1:
            print(f"\n===== {name}: {pat}")
        done[name] = report(pat)
    if len(conds) > 1:
        names = list(done)
        print("\ncomparison: mean [min–max] per life, over lives that reached the horizon")
        print(f"{'':<20}" + "".join(f"{n} (n={len(done[n])})".ljust(22) for n in names))
        for label, f in COMPARE:
            cells = []
            for n in names:
                xs = [f(r) for r in done[n]]
                cells.append(f"{statistics.mean(xs):.1f} [{min(xs)}–{max(xs)}]" if xs else "–")
            print(f"{label:<20}" + "".join(c.ljust(22) for c in cells))


def report(pattern: str) -> list[dict]:
    paths = sorted(p for p in glob.glob(pattern) if not p.endswith("-referee-baseline.jsonl"))
    if not paths:
        sys.exit(f"no run logs match {pattern}")
    runs = [profile(p) for p in paths]
    for r in runs:
        print(f"\n{r['run']}  dice {r['dice_seed']} order {r['order_seed']}  {r['reason']} after {r['weeks']} weeks")
        print("  messages (sent/replied): " + ", ".join(f"{r['contacts'].get(c, c)} {s}/{rep}" for c, (s, rep) in r["messages"].items()))
        print(f"  offers taken: {r['offers'] or 'none'}")
        print(f"  posts {r['posts']}, likes {r['likes']} (best {r['best_likes']}), followers {r['followers']}")
        print(f"  commits {r['commits']}, repos {r['repos']}, shell calls {r['shell']}")
        print(f"  cheap bets {r['bets']} ({r['bet_wins']} won): {', '.join(r['bet_list']) or 'none'}; launches {r['launches']}, "
              f"hackathons placed {r['placed']}, spend {r['spend']}")
        print(f"  costly bets {r['costly']}: {', '.join(r['costly_list']) or 'none'} "
              f"(savings >= 25% and relocating: no mechanic, 0 by construction)")
        print(f"  actions: train {r['share']['train']}%, post {r['share']['post']}%, message {r['share']['message']}%"
              f" ({r['train_sessions']} train sessions)")
        print(f"  trained {r['trained_weeks']} weeks, streak {r['streak']} at the end; cash ${r['cash']:,.0f}; "
              f"holding: {', '.join(r['commitments']) or 'nothing'}")
    done = [r for r in runs if r["reason"] == "horizon"]
    if len(done) < 2:
        return done
    contacts = done[-1]["contacts"]  # one batch, one persona
    print(f"\nacross {len(done)} lives that reached the horizon (min / median / max):")
    stat = lambda xs: f"{min(xs)} / {statistics.median(xs):g} / {max(xs)}"
    sent = lambda r, ids=None: sum(s for c, (s, _) in r["messages"].items() if ids is None or c in ids)
    print(f"  messages sent, all contacts: {stat([sent(r) for r in done])}")
    for c, name in contacts.items():
        xs = [r["messages"].get(c, [0, 0])[0] for r in done]
        print(f"    {name:<16} {stat(xs)}   lives that wrote at least once: {sum(x > 0 for x in xs)}/{len(done)}")
    print(f"  distinct contacts written to: {stat([sum(s > 0 for s, _ in r['messages'].values()) for r in done])}")
    for k, label in (("posts", "posts"), ("followers", "followers"), ("commits", "commits"), ("shell", "shell"),
                     ("bets", "cheap bets"), ("bet_wins", "cheap bets won"), ("costly", "costly bets"),
                     ("launches", "launches"), ("spend", "spend"), ("trained_weeks", "trained weeks")):
        print(f"  {label:<16} {stat([r[k] or 0 for r in done])}")
    return done


if __name__ == "__main__":
    main(sys.argv[1:] or ["runs/self-*.jsonl"])
