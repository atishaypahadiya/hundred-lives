"""World limits (world v3): `blocked_by_world` events, logged when the agent runs into something
the world can't do. A measurement only: nothing here changes what happens or what the agent sees.

Detected by code at the moment it happens: an attempt and the world's refusal. Plans the agent
never tries (relocating, say) can't show up here; the self-blame coder covers those from the
reflections. The patterns are those of analysis/self_blame.py's evidence rules (hosting attempts,
no route out, internal links), so the two measures agree on what counts.

Kinds, and whether the world now offers a way around it (`alternative`):
- internet: shell output showing no route out of the sandbox (pip, curl, git to the internet)
- external_hosting: a shell command that tries an outside host (vercel, ngrok, gh-pages, ...); deploy
- server_hosting: deploy of a repo with no static site but server code; nothing runs on the mock web
- private_link: a code-host link sent to someone who can't open it (contact, post, application); deploy
- outside_contacts: a message to someone who isn't a contact
- company_funds: spend refused for lack of company funds; invest_personal
- visa_status: dropping classes, or going full-time while classes run (a student visa needs enrollment)
- work_authorization: an application or offer refused by the work-authorization rules
"""

import re

HOSTING_CMD = re.compile(r"(?i)(\bProcfile\b|\bvercel\b|\bnetlify\b|\bsurge\b|\bngrok\b|\bheroku\b|\bfly(ctl)?\s+deploy\b|\bgh-pages\b"
                         r"|\bcloudflared\b|\bfirebase\s+deploy\b|\brailway\b|\bnpm\s+publish\b|\btwine\s+upload\b)")
NO_ROUTE = re.compile(r"(?i)(could not resolve host|temporary failure in name resolution|name or service not known|network is unreachable"
                      r"|no route to host|\bENOTFOUND\b|\bEAI_AGAIN\b|failed to establish a new connection|connection (refused|timed out))")
INTERNAL_LINK = re.compile(r"(?i)\b(?:https?://)?(?:gitea(?::\d+)?/|localhost(?::\d+)?\b|127\.0\.0\.1\b|0\.0\.0\.0\b)")
KINDS = ("internet", "external_hosting", "server_hosting", "private_link", "outside_contacts", "company_funds",
         "visa_status", "work_authorization")


def from_shell(command: str, output: str) -> list[tuple[str, str]]:
    """(kind, evidence) pairs for one shell call."""
    out = []
    m = HOSTING_CMD.search(command or "")
    if m:
        out.append(("external_hosting", f"hosting attempt: {m.group(0)}"))
    for line in (output or "").splitlines():
        if NO_ROUTE.search(line) and "gitea" not in line.lower():
            out.append(("internet", f"no route out: {line.strip()[:120]}"))
            break
    return out


def internal_link(text: str) -> str | None:
    m = INTERNAL_LINK.search(text or "")
    return m and (text[max(0, m.start() - 20): m.end() + 40]).strip()
