"""Parse a Guide_3DS checkout into the snapshot the assistant runs from.

The guide is the source of truth: nothing here encodes method logic or file lists. A page
that fails to parse is recorded as an error, and asking for it raises GuideParseError with
the raw page link so the caller can send the user there instead of guessing.
"""
from __future__ import annotations

import hashlib
import io
import json
import re
import tarfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

import httpx

GUIDE_REPO = "hacks-guide/Guide_3DS"
GUIDE_BRANCH = "master"
GUIDE_SITE = "https://3ds.hacks.guide"
SNAPSHOT_FORMAT = 1

# The method routing lives in JavaScript, not markdown; methods.py runs these files.
ROUTING_FILES = ("public/assets/js/common.js", "public/assets/js/selecting.js")
VERSION_FORM = "_internal/consoleVersionSelect.html"
ROUTER_PAGE = "get-started"

MAX_INCLUDE_DEPTH = 8

FRONTMATTER_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---[ \t]*\r?\n", re.S)
SCALAR_RE = re.compile(r"^([A-Za-z_][\w-]*):[ \t]*(.*?)\s*$")
INCLUDE_RE = re.compile(r"^(?P<indent>[ \t]*)<!--@include:\s*(?P<ref>\S+)\s*-->\s*$")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
SECTION_RE = re.compile(r"^Section\s+([IVXLC]+)\s*[-–—]\s*(.+)$")
STEP_RE = re.compile(r"^\d+\.\s+(.*)$")
BULLET_RE = re.compile(r"^[*+-]\s+(.*)$")
FENCE_OPEN_RE = re.compile(r"^\s*(?P<marks>:{3,})\s*(?P<kind>\w+)\s*(?P<title>.*?)\s*$")
FENCE_CLOSE_RE = re.compile(r"^\s*(?P<marks>:{3,})\s*$")
IMAGE_ONLY_RE = re.compile(r"^\s*!\[[^\]]*\]\([^)]*\)\s*$")
REFRESH_RE = re.compile(r"http-equiv:\s*refresh.*?URL=(?P<target>[^'\"\s]+)", re.S | re.I)
NEXT_RE = re.compile(r"Continue to \[[^\]]*\]\((?P<href>(?:[^()\s]|\([^()\s]*\))+)\)")

_HREF = r"(?:[^()\s]|\([^()\s]*\))+"
LINK_RE = re.compile(
    rf"\[!\[(?P<alt>[^\]]*)\]\([^)]*\)\]\((?P<ihref>{_HREF})\)"  # [![alt](img)](href)
    rf"|(?<!!)\[(?P<text>[^\]]*)\]\((?P<href>{_HREF})\)"
)


class GuideParseError(Exception):
    """A guide page could not be parsed; `url` is the raw page to show instead."""

    def __init__(self, slug: str, reason: str):
        super().__init__(f"{slug}: {reason}")
        self.slug = slug
        self.reason = reason
        self.url = page_url(slug)


def page_url(slug: str) -> str:
    return f"{GUIDE_SITE}/{slug}"


# --- markdown ---------------------------------------------------------------------------

def link(text: str, href: str) -> dict:
    slug = anchor = None
    if not re.match(r"^[a-z]+:", href) and not href.startswith("/images/"):
        path, _, frag = href.partition("#")
        path = path.lstrip("/").removesuffix(".html")
        slug = path or None
        anchor = frag or None
    return {"text": text, "href": href, "slug": slug, "anchor": anchor}


def extract_links(text: str) -> list[dict]:
    out = []
    for m in LINK_RE.finditer(text):
        if m.group("ihref"):
            out.append(link(m.group("alt"), m.group("ihref")))
        else:
            out.append(link(m.group("text"), m.group("href")))
    return out


def parse_frontmatter(text: str) -> tuple[dict, str]:
    m = FRONTMATTER_RE.match(text)
    if not m:
        return {}, text
    scalars = {}
    for line in m.group(1).splitlines():
        sm = SCALAR_RE.match(line)
        if sm and sm.group(2) and not sm.group(2).startswith(("[", "{")):
            scalars[sm.group(1)] = sm.group(2).strip("'\"")
    return scalars, text[m.end():]


def expand_includes(text: str, current: Path, docs: Path, slug: str, depth: int = 0) -> str:
    if depth > MAX_INCLUDE_DEPTH:
        raise GuideParseError(slug, "includes nested too deeply")
    root = docs.resolve()
    out = []
    for line in text.splitlines():
        m = INCLUDE_RE.match(line)
        if not m:
            out.append(line)
            continue
        ref, indent = m.group("ref"), m.group("indent")
        target = (docs / ref[2:]) if ref.startswith("@/") else (current.parent / ref)
        target = target.resolve()
        if not target.is_relative_to(root):
            raise GuideParseError(slug, f"include {ref} points outside the guide")
        if target.suffix != ".md":
            out.append(f"{indent}<!-- embed: {ref} -->")
            continue
        if not target.is_file():
            raise GuideParseError(slug, f"missing include {ref}")
        inner = expand_includes(target.read_text(encoding="utf-8"), target, docs, slug, depth + 1)
        out.extend(indent + l if l.strip() else l for l in inner.splitlines())
    return "\n".join(out)


def find_close(lines: list[str], start: int, hi: int) -> int | None:
    """Index of the line closing the fence opened at `start` (markdown-it-container rules)."""
    marks = len(FENCE_OPEN_RE.match(lines[start])["marks"])
    for k in range(start + 1, hi):
        c = FENCE_CLOSE_RE.match(lines[k])
        if c and len(c["marks"]) >= marks:
            return k
    return None


def fence_spans(lines: list[str], slug: str, lo: int = 0, hi: int | None = None,
                nested: bool = False) -> list[tuple[int, int, bool]]:
    """(open, end, closed) spans of every ::: block. `end` is the block's own closing line when
    `closed`, otherwise the parent's end (exclusive).

    An inner block left open is closed by its parent, as VitePress renders it. A top-level
    block that never closes, or a stray closing line, is an error rather than a guess.
    """
    hi = len(lines) if hi is None else hi
    spans = []
    i = lo
    while i < hi:
        if FENCE_OPEN_RE.match(lines[i]):
            close = find_close(lines, i, hi)
            if close is None and not nested:
                raise GuideParseError(slug, f"unclosed ::: block from line {i + 1}")
            end = hi if close is None else close
            spans.append((i, end, close is not None))
            spans += fence_spans(lines, slug, i + 1, end, nested=True)
            i = end + 1
        elif FENCE_CLOSE_RE.match(lines[i]) and not nested:
            raise GuideParseError(slug, f"stray ::: on line {i + 1}")
        else:
            i += 1
    return spans


def drop_images(lines: list[str], slug: str) -> list[str]:
    """Remove image-only lines, then any admonition left with nothing in it."""
    lines = [l for l in lines if not IMAGE_ONLY_RE.match(l)]
    keep = [True] * len(lines)
    for start, end, closed in sorted(fence_spans(lines, slug), key=lambda s: s[1] - s[0]):
        if all(not keep[j] or not lines[j].strip() for j in range(start + 1, end)):
            for j in range(start, end + 1 if closed else end):
                keep[j] = False
    return [l for l, k in zip(lines, keep) if k]


def note_text(body: list[str]) -> str:
    out = []
    for l in body:
        if FENCE_CLOSE_RE.match(l) or not l.strip():
            continue
        m = FENCE_OPEN_RE.match(l)
        out.append(m["title"] if m else l.strip())
    return "\n".join(t for t in out if t)


def dedent(line: str) -> str:
    return line[4:] if line.startswith("    ") else line.lstrip("\t ")


def parse_block(lines: list[str]) -> dict:
    """Split the lines under one heading into intro text, steps, bullets and notes."""
    intro, steps, bullets, notes, embeds = [], [], [], [], []
    item: dict | None = None
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            i += 1
            continue
        if line[0] in " \t":
            if item is not None:
                item["details"].append(dedent(line))
            i += 1
            continue
        fence = FENCE_OPEN_RE.match(line)
        if fence:
            close = find_close(lines, i, len(lines))
            end = len(lines) if close is None else close
            notes.append({"kind": fence["kind"], "title": fence["title"], "text": note_text(lines[i + 1:end])})
            item = None
            i = end + 1
            continue
        if line.startswith("<!-- embed:"):
            embeds.append(line.removeprefix("<!-- embed:").removesuffix("-->").strip())
        elif m := STEP_RE.match(line):
            item = {"text": m.group(1), "details": []}
            steps.append(item)
        elif m := BULLET_RE.match(line):
            item = {"text": m.group(1), "details": []}
            bullets.append(item)
        else:
            intro.append(line.strip())
            item = None
        i += 1
    return {"intro": intro, "steps": steps, "bullets": bullets, "notes": notes, "embeds": embeds}


def item_text(item: dict) -> str:
    return "\n".join([item["text"], *item["details"]])


def parse_page(path: Path, docs: Path) -> dict:
    slug = path.stem
    raw = path.read_text(encoding="utf-8")
    frontmatter, body = parse_frontmatter(raw)
    refresh = REFRESH_RE.search(raw[: len(raw) - len(body)])
    body = expand_includes(body, path, docs, slug)
    lines = drop_images(body.splitlines(), slug)

    fenced = set()
    for start, end, _ in fence_spans(lines, slug):
        fenced.update(range(start, end + 1))

    title = None
    blocks: list[dict] = []
    current = {"level": 0, "heading": "", "lines": []}
    for n, line in enumerate(lines):
        h = None if n in fenced else HEADING_RE.match(line)
        if h:
            blocks.append(current)
            current = {"level": len(h.group(1)), "heading": h.group(2), "lines": []}
            if title is None and current["level"] == 1:
                title = current["heading"]
        else:
            current["lines"].append(line)
    blocks.append(current)

    title = title or frontmatter.get("title")
    page = {
        "slug": slug,
        "title": title,
        "redirect": link("", refresh["target"])["slug"] if refresh else None,
        "frontmatter": frontmatter,
        "compatibility": None,
        "requirements": [],
        "sections": [],
        "blocks": [],
        "next_page": None,
        "troubleshooting": [],
        "links": extract_links("\n".join(lines)),
    }
    for b in blocks:
        parsed = parse_block(b["lines"])
        heading = b["heading"]
        page["blocks"].append({"level": b["level"], "heading": heading, **parsed})
        sm = SECTION_RE.match(heading)
        if sm:
            page["sections"].append({"number": sm.group(1), "title": sm.group(2), **parsed})
        elif heading.lower() == "what you need":
            page["requirements"] = [
                {"text": item_text(it), "links": extract_links(item_text(it))} for it in parsed["bullets"]
            ]
        elif heading.lower() == "compatibility notes":
            text = "\n\n".join([*parsed["intro"], *(n["text"] for n in parsed["notes"])])
            page["compatibility"] = {"text": text, "links": extract_links(text)}

    nexts = NEXT_RE.findall("\n".join(lines))
    if nexts:
        page["next_page"] = link("", nexts[-1])["slug"]
    seen = set()
    for l in page["links"]:
        if l["slug"] and l["slug"].startswith("troubleshooting") and l["href"] not in seen:
            seen.add(l["href"])
            page["troubleshooting"].append(l)
    if not title and not page["redirect"]:
        raise GuideParseError(slug, "page has no title")
    return page


# --- version form -----------------------------------------------------------------------

def parse_version_form(html: str) -> dict:
    selects = {}
    for sid, inner in re.findall(r'<select\s+id="([^"]+)"[^>]*>(.*?)</select>', html, re.S):
        opts = []
        for value, text in re.findall(r'<option(?:\s+value="([^"]*)")?[^>]*>([^<]*)</option>', inner):
            v = value or text.strip()
            if v:
                opts.append(v)
        selects[sid] = opts
    order = list(selects)
    if not {"prefix", "major", "minor", "nver", "region"} <= set(order):
        raise GuideParseError(ROUTER_PAGE, "version form is missing an expected field")
    micro = order[order.index("minor") + 1]

    headers = [re.sub(r"<[^>]+>", "", h).strip() for h in re.findall(r"<th>(.*?)</th>", html, re.S)]
    radios = re.findall(r'<input\s+type="radio"[^>]*\bid="([^"]+)"', html)
    models = {}
    for header, rid in zip(headers, radios):
        family = header.split()[0].lower() if header else ""
        if family in ("old", "new"):
            models[family] = rid
    if set(models) != {"old", "new"}:
        raise GuideParseError(ROUTER_PAGE, "version form model choices changed")

    return {
        "selects": selects,
        "micro_select": micro,
        "models": models,
        "element_ids": re.findall(r'\bid="([^"]+)"', html),
    }


# --- snapshot ---------------------------------------------------------------------------

def build_snapshot(docs: Path, commit: str) -> "GuideSnapshot":
    docs = Path(docs)
    pages, errors = {}, {}
    for path in sorted(docs.glob("*.md")):
        try:
            pages[path.stem] = parse_page(path, docs)
        except GuideParseError as e:
            errors[e.slug] = {"reason": e.reason, "url": e.url}
        except (OSError, UnicodeDecodeError, IndexError) as e:
            errors[path.stem] = {"reason": f"unreadable: {e}", "url": page_url(path.stem)}

    routing = {}
    for rel in ROUTING_FILES:
        f = docs / rel
        if f.is_file():
            src = f.read_text(encoding="utf-8")
            routing[rel] = {"source": src, "sha256": hashlib.sha256(src.encode()).hexdigest()}

    form = None
    form_path = docs / VERSION_FORM
    try:
        if not form_path.is_file():
            raise GuideParseError(ROUTER_PAGE, f"missing {VERSION_FORM}")
        form = parse_version_form(form_path.read_text(encoding="utf-8"))
    except GuideParseError as e:
        errors[e.slug] = {"reason": e.reason, "url": e.url}

    return GuideSnapshot({
        "format": SNAPSHOT_FORMAT,
        "repo": GUIDE_REPO,
        "commit": commit,
        "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "pages": pages,
        "errors": errors,
        "routing": routing,
        "version_form": form,
    })


class GuideSnapshot:
    def __init__(self, data: dict):
        self.data = data

    commit = property(lambda self: self.data["commit"])
    pages = property(lambda self: self.data["pages"])
    errors = property(lambda self: self.data["errors"])
    routing = property(lambda self: self.data["routing"])
    version_form = property(lambda self: self.data["version_form"])

    def has_page(self, slug: str) -> bool:
        return slug in self.pages and slug not in self.errors

    def page(self, slug: str) -> dict:
        if slug in self.errors:
            raise GuideParseError(slug, self.errors[slug]["reason"])
        if slug not in self.pages:
            raise GuideParseError(slug, "page is not in this guide snapshot")
        return self.pages[slug]

    def stage_page(self, slug: str) -> dict:
        """A page the assistant walks the user through; it must have numbered steps."""
        page = self.page(slug)
        if page["redirect"]:
            raise GuideParseError(slug, f"page is retired; the guide redirects it to {page['redirect']}")
        if not any(s["steps"] for s in page["sections"]):
            raise GuideParseError(slug, "no numbered sections with steps were found")
        return page

    def save(self, path: Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=1, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)

    @classmethod
    def load(cls, path: Path) -> "GuideSnapshot":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if data.get("format") != SNAPSHOT_FORMAT:
            raise GuideParseError(ROUTER_PAGE, f"snapshot format {data.get('format')} is not supported; re-parse the guide")
        return cls(data)


# --- fetching the guide -----------------------------------------------------------------

def fetch_latest_commit(client: httpx.Client) -> str:
    r = client.get(
        f"https://api.github.com/repos/{GUIDE_REPO}/commits/{GUIDE_BRANCH}",
        headers={"Accept": "application/vnd.github.sha"},
    )
    r.raise_for_status()
    sha = r.text.strip()
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise GuideParseError(ROUTER_PAGE, "GitHub did not return a commit hash")
    return sha


def check_for_update(snapshot: GuideSnapshot, client: httpx.Client) -> str | None:
    """The newer commit hash if the guide has changed since the snapshot, else None."""
    latest = fetch_latest_commit(client)
    return None if latest == snapshot.commit else latest


def _wanted(rest: tuple[str, ...]) -> bool:
    if len(rest) == 1:
        return rest[0].endswith(".md")
    return rest[0] in ("_include", "_internal") or "/".join(rest) in ROUTING_FILES


def download_guide(commit: str, dest: Path, client: httpx.Client) -> Path:
    """Fetch the guide at `commit` and unpack only the English pages and routing files."""
    r = client.get(f"https://codeload.github.com/{GUIDE_REPO}/tar.gz/{commit}", follow_redirects=True)
    r.raise_for_status()
    docs = Path(dest) / "docs"
    docs.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(r.content), mode="r:gz") as tar:
        for member in tar.getmembers():
            parts = PurePosixPath(member.name).parts
            if not member.isfile() or len(parts) < 3 or parts[1] != "docs":
                continue
            rest = parts[2:]
            if ".." in parts or member.name.startswith("/") or not _wanted(rest):
                continue
            target = docs.joinpath(*rest)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(tar.extractfile(member).read())
    return docs


def refresh_snapshot(client: httpx.Client, guide_dir: Path, snapshot_path: Path) -> GuideSnapshot:
    commit = fetch_latest_commit(client)
    docs = download_guide(commit, Path(guide_dir) / commit, client)
    snapshot = build_snapshot(docs, commit)
    snapshot.save(snapshot_path)
    return snapshot
