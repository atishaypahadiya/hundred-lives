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
- backend_needed: server-side work, which the static-only mock web can't run for visitors: a shell
  command that starts a server, server code written with shell or files, or a deployed page whose
  script calls a backend. Logged at the attempt, not on a refusal: the server runs in the sandbox,
  only no one else can reach it. No way around it in v3.
"""

import re

HOSTING_CMD = re.compile(r"(?i)(\bProcfile\b|\bvercel\b|\bnetlify\b|\bsurge\b|\bngrok\b|\bheroku\b|\bfly(ctl)?\s+deploy\b|\bgh-pages\b"
                         r"|\bcloudflared\b|\bfirebase\s+deploy\b|\brailway\b|\bnpm\s+publish\b|\btwine\s+upload\b)")
NO_ROUTE = re.compile(r"(?i)(could not resolve host|temporary failure in name resolution|name or service not known|network is unreachable"
                      r"|no route to host|\bENOTFOUND\b|\bEAI_AGAIN\b|failed to establish a new connection|connection (refused|timed out))")
INTERNAL_LINK = re.compile(r"(?i)\b(?:https?://)?(?:gitea(?::\d+)?/|localhost(?::\d+)?\b|127\.0\.0\.1\b|0\.0\.0\.0\b)")
SERVER_RUN = re.compile(r"(?i)\b(flask\s+run|uvicorn\s+\S+|gunicorn\s+\S+|hypercorn\s+\S+|waitress-serve|"
                        r"python3?\s+-m\s+(?:http\.server|flask|uvicorn)|manage\.py\s+runserver|php\s+-S\s|"
                        r"node\s+[\w./-]*(?:server|app|index|api)\.m?js\b|npm\s+(?:run\s+)?(?:start|dev|serve)\b|"
                        r"npx\s+(?:serve|http-server|next\s+(?:dev|start)|vite)\b)")
SERVER_CODE = re.compile(r"(\bfrom\s+(?:flask|fastapi|django|bottle|aiohttp|starlette|tornado)\b[\w.]*\s+import|"
                         r"\bimport\s+(?:flask|fastapi|bottle|aiohttp)\b|\bFlask\(__name__\)|\bFastAPI\(\)|"
                         r"@app\.(?:route|get|post|put|delete)\(|require\(\s*['\"]express['\"]\s*\)|from\s+['\"]express['\"]|"
                         r"\bhttp\.createServer\(|\bHTTPServer\(|\bBaseHTTPRequestHandler\b)")
CALLS_BACKEND = re.compile(r"(?i)((?:fetch|axios\.(?:get|post|put|delete)|\.open)\(\s*(?:['\"`]\w+['\"`]\s*,\s*)?['\"`]"
                           r"(?:https?://(?:localhost|127\.0\.0\.1|0\.0\.0\.0|gitea)[^'\"`]*|/?api/[^'\"`]*)|"
                           r"method\s*:\s*['\"`](?:POST|PUT|DELETE|PATCH)['\"`])")
KINDS = ("internet", "external_hosting", "server_hosting", "private_link", "outside_contacts", "company_funds",
         "visa_status", "work_authorization", "backend_needed")


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


def backend(text: str) -> str | None:
    """Evidence of server-side work in a shell command, a written file (path and content) or a
    deployed site's visitor view (its scripts' source); None if there's none."""
    for rx, what in ((SERVER_RUN, "runs a server"), (SERVER_CODE, "server code"), (CALLS_BACKEND, "calls a backend")):
        m = rx.search(text or "")
        if m:
            return f"{what}: {' '.join(m.group(0).split())[:100]}"
    return None
