"""The mock web (world v3): `deploy` publishes a repo's static files at https://<name>.<domain>.

There's no server. A deploy reads the repo's main branch through the Gitea admin API (from the
host; nothing new is reachable from any container) and keeps a snapshot: which files are served,
and the *visitor view*: what someone who opens the site sees. Contacts, reviewers, the feed, the
forum and hackathon judges get that view whenever a link to the site appears in what the agent
sends them. Nothing runs: pages are shown as their visible text, links, forms and buttons, and
the scripts as source.

Everything in a site is agent-written, so it only ever reaches a model as quoted content, and the
host never writes it to disk outside the run log.
"""

import posixpath
import re
from html.parser import HTMLParser

SITE_DIRS = ("", "docs/", "public/", "dist/", "site/", "build/")  # where index.html may be, first match wins
STATIC_EXTS = (".html", ".htm", ".css", ".js", ".mjs", ".json", ".txt", ".md", ".svg", ".csv", ".xml",
               ".webmanifest", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".woff", ".woff2")
READABLE_EXTS = (".html", ".htm", ".js", ".mjs")   # read to build the visitor view
SERVER_FILES = re.compile(r"(?i)(^|/)(app\.py|main\.py|server\.py|manage\.py|wsgi\.py|asgi\.py|requirements\.txt|"
                          r"pyproject\.toml|Pipfile|package\.json|server\.js|index\.js|Procfile|Dockerfile)$")
MAX_FILE_BYTES = 200_000     # larger files are served but not read
MAX_READ_BYTES = 1_000_000   # total read per deploy
MAX_PAGES = 4                # HTML pages in the view: index first, then pages it links to
PAGE_CHARS = 2500            # visible text per page
SCRIPT_CHARS = 2500          # script source, all pages together
VIEW_CHARS = 9000            # the whole view

URL = re.compile(r"(?i)\bhttps?://([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)\.{domain}(/[^\s\"'<>)\]]*)?")
INTERNAL_LINK = re.compile(r"(?i)\b(?:https?://)?(?:gitea(?::\d+)?/|localhost(?::\d+)?\b|127\.0\.0\.1\b|0\.0\.0\.0\b)")


def site_name(repo: str) -> str:
    """A repo name as a host label: lowercase letters, digits and hyphens."""
    s = re.sub(r"[^a-z0-9-]+", "-", repo.lower()).strip("-")
    return s[:63].strip("-") or "site"


def url_pattern(domain: str) -> re.Pattern:
    return re.compile(URL.pattern.replace("{domain}", re.escape(domain)), re.I)


def find_urls(text: str, domain: str) -> list[str]:
    """Site names linked in a text, in order, each once."""
    out = []
    for m in url_pattern(domain).finditer(text or ""):
        name = m.group(1).lower()
        if name not in out:
            out.append(name)
    return out


def site_root(paths: list[str]) -> str | None:
    """The directory the site is served from: the first of SITE_DIRS holding an index.html."""
    have = set(paths)
    return next((d for d in SITE_DIRS if f"{d}index.html" in have), None)


class _Page(HTMLParser):
    """Visible text, links, forms and scripts of one HTML page."""

    SKIP = {"script", "style", "noscript", "template", "head"}
    BLOCK = {"p", "div", "section", "article", "header", "footer", "main", "nav", "li", "ul", "ol", "br",
             "h1", "h2", "h3", "h4", "h5", "h6", "tr", "table", "form", "pre", "blockquote", "hr"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title, self.text, self.links, self.controls, self.scripts, self.images = "", [], [], [], [], []
        self._skip, self._in_title, self._in_script, self._link = 0, False, False, None
        self._inline: list[str] = []

    def handle_starttag(self, tag, attrs):
        a = {k: v or "" for k, v in attrs}
        if tag == "title":
            self._in_title = True
        if tag == "script":
            self._in_script = True
            if a.get("src"):
                self.scripts.append(("src", a["src"]))
        if tag in self.SKIP:
            self._skip += 1
        if tag in self.BLOCK:
            self.text.append("\n")
        if tag == "a":
            self._link = [a.get("href", ""), ""]
        if tag in ("input", "textarea", "select"):
            kind = a.get("type", "text") if tag == "input" else tag
            if kind not in ("hidden",):
                label = a.get("placeholder") or a.get("aria-label") or a.get("name") or a.get("id") or ""
                value = a.get("value", "") if kind in ("submit", "button") else ""
                self.controls.append(f"{kind} field" + (f" \"{label}\"" if label else "") + (f" [{value}]" if value else ""))
        if tag == "button":
            self.controls.append("button")
        if tag == "img" and a.get("alt"):
            self.images.append(a["alt"])

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        if tag == "script":
            self._in_script = False
            if self._inline:
                self.scripts.append(("inline", "".join(self._inline)))
                self._inline = []
        if tag in self.SKIP:
            self._skip = max(0, self._skip - 1)
        if tag == "a" and self._link:
            self.links.append((self._link[0], " ".join(self._link[1].split())))
            self._link = None
        if tag in self.BLOCK:
            self.text.append("\n")

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        if self._in_script:
            self._inline.append(data)
            return
        if self._skip:
            return
        self.text.append(data)
        if self._link is not None:
            self._link[1] += data
        if self.controls and self.controls[-1] == "button" and data.strip():
            self.controls[-1] = f"button \"{data.strip()[:60]}\""


def _clean(text: str) -> str:
    lines = [" ".join(l.split()) for l in text.splitlines()]
    out, blank = [], False
    for l in lines:
        if l:
            out.append(l)
            blank = False
        elif not blank and out:
            out.append("")
            blank = True
    return "\n".join(out).strip()


def _cut(s: str, n: int) -> str:
    return s if len(s) <= n else s[: n - 1] + "…"


def visitor_view(url: str, root: str, served: list[str], files: dict[str, str]) -> str:
    """What someone who opens the site sees. `served`: paths (repo-relative) the site serves;
    `files`: the readable ones that were read, path -> text."""
    pages = [f"{root}index.html"]
    queue = [f"{root}index.html"]
    parsed: dict[str, _Page] = {}
    while queue and len(parsed) < MAX_PAGES:
        path = queue.pop(0)
        if path not in files:
            continue
        p = _Page()
        try:
            p.feed(files[path])
            p.close()
        except Exception:  # malformed HTML still shows what was parsed
            pass
        parsed[path] = p
        for href, _ in p.links:
            target = _resolve(path, href, root)
            if target and target in files and target not in pages and target.endswith((".html", ".htm")):
                pages.append(target)
                queue.append(target)
    out, script_left = [f"{url} serves {len(served)} file{'s' if len(served) != 1 else ''}: "
                         + ", ".join(p[len(root):] for p in served[:30]) + (" …" if len(served) > 30 else "")], SCRIPT_CHARS
    for path, p in parsed.items():
        rel = "/" + path[len(root):]
        block = [f"\n--- page {rel}" + (f" (title: {_cut(' '.join(p.title.split()), 120)})" if p.title.strip() else "") + " ---",
                 _cut(_clean("".join(p.text)), PAGE_CHARS) or "(no visible text)"]
        if p.links:
            block.append("Links: " + "; ".join(f"{t or '(no text)'} -> {h}" for h, t in p.links[:20]))
        if p.controls:
            block.append("Forms and buttons: " + "; ".join(p.controls[:20]))
        if p.images:
            block.append("Images: " + "; ".join(_cut(a, 80) for a in p.images[:10]))
        for kind, s in p.scripts:
            if kind == "src":
                target = _resolve(path, s, root)
                src = files.get(target) if target else None
                head = f"Script {s}" + ("" if target in served else " (not on this site)")
            else:
                src, head = s, "Inline script"
            if src is not None and script_left > 0:
                code = _cut(src.strip(), script_left)
                script_left -= len(code)
                block.append(f"{head}, source:\n{code}")
            else:
                block.append(head)
        out.append("\n".join(block))
    if len(served) and len(parsed) == 0:
        out.append("(index.html couldn't be read)")
    return _cut("\n".join(out), VIEW_CHARS)


def _resolve(page: str, href: str, root: str) -> str | None:
    """A link on `page` as a repo path, if it points inside the site (served from `root`); None for
    other sites, anchors, and paths outside the site."""
    href = (href or "").split("#")[0].split("?")[0].strip()
    if not href or re.match(r"(?i)^[a-z][a-z0-9+.-]*:", href) or href.startswith("//"):
        return None
    joined = root + href.lstrip("/") if href.startswith("/") else posixpath.join(posixpath.dirname(page), href)
    path = posixpath.normpath(joined)
    if href.endswith("/") or path + "/" == root:
        path = posixpath.join(path, "index.html") if path + "/" != root else root + "index.html"
    if path.startswith("..") or not path.startswith(root):
        return None
    return path
