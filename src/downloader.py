"""Turn a guide page's "What You Need" list into verified, cached files.

Every download comes from a link on a guide page; nothing else is ever fetched. Pinned
URLs are used verbatim. "Latest" links are resolved with the GitHub Releases API. A
published SHA-256 is enforced; without one, the hash is recorded on first download and
any later difference aborts. Links the assistant can't fetch safely (per-region sites,
installers, ambiguous assets) are returned as manual items with the guide's own text.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import httpx

from guide_parser import GuideSnapshot

ALLOWED_HOSTS = {
    "api.github.com", "github.com", "codeload.github.com",
    "objects.githubusercontent.com", "release-assets.githubusercontent.com",
}
GH_RELEASE_RE = re.compile(r"^https://github\.com/(?P<owner>[\w.-]+)/(?P<repo>[\w.-]+)/releases(?:/(?P<rest>.*?))?/?$")
CODE_RE = re.compile(r"`([^`]+)`")
CHUNK = 1024 * 1024


class DownloadError(Exception):
    def __init__(self, message: str, url: str | None = None):
        super().__init__(message)
        self.url = url


class ChecksumMismatch(DownloadError):
    pass


@dataclass(frozen=True)
class DownloadSpec:
    name: str
    page: str
    requirement: str
    url: str | None
    kind: str  # "github", "manual" (user downloads a file), or "prerequisite" (installed on the PC)
    owner: str | None = None
    repo: str | None = None
    tag: str | None = None  # None means the latest release
    asset: str | None = None  # exact asset name
    asset_suffix: str | None = None  # e.g. ".zip" when the guide only names the type
    reason: str = ""


@dataclass(frozen=True)
class ResolvedAsset:
    spec: DownloadSpec
    tag: str
    asset_name: str
    download_url: str
    size: int
    digest: str | None  # published sha256 hex, if GitHub has one
    published_at: str


@dataclass(frozen=True)
class Downloaded:
    name: str
    path: Path
    sha256: str
    verified: str  # "published", "first-use", "recorded", or "user-provided"
    from_cache: bool
    resolved: ResolvedAsset | None = None


@dataclass
class DownloadPlan:
    resolved: list[ResolvedAsset]
    manual: list[DownloadSpec]
    prerequisites: list[DownloadSpec] = field(default_factory=list)


# --- reading the guide ------------------------------------------------------------------

def _manual(name, page, req, url, reason) -> DownloadSpec:
    return DownloadSpec(name, page, req, url, "manual", reason=reason)


def spec_for_requirement(page: str, req: dict) -> DownloadSpec:
    text = req["text"]
    if not req["links"]:
        return _manual(text.split("\n")[0], page, text, None, "the guide gives no download link")
    first = req["links"][0]
    name, url = first["text"], first["href"]
    if "installed on your computer" in text:
        return DownloadSpec(name, page, text, url, "prerequisite", reason="install this on your computer")
    m = GH_RELEASE_RE.match(url)
    if not m:
        return _manual(name, page, text, url, "not a GitHub release; download it from the guide's link")
    owner, repo, rest = m["owner"], m["repo"], (m["rest"] or "")
    parts = rest.split("/") if rest else []
    if len(parts) == 3 and parts[0] == "download":
        return DownloadSpec(name, page, text, url, "github", owner, repo, parts[1], parts[2])
    if len(parts) == 3 and parts[:2] == ["latest", "download"]:
        return DownloadSpec(name, page, text, url, "github", owner, repo, None, parts[2])
    tag = parts[1] if len(parts) == 2 and parts[0] == "tag" else None
    if parts in ([], ["latest"]) or tag:
        # The asset is named in backticks after the link: `otherapp.bin`, or `.zip` for a type.
        after = text.split(url + ")", 1)[-1]
        codes = CODE_RE.findall(after)
        if not codes:
            return _manual(name, page, text, url, "the guide doesn't say which file from this release to use")
        code = codes[0]
        if code.startswith("."):
            return DownloadSpec(name, page, text, url, "github", owner, repo, tag, asset_suffix=code)
        return DownloadSpec(name, page, text, url, "github", owner, repo, tag, asset=code)
    return _manual(name, page, text, url, "unrecognised GitHub link")


def specs_for_page(page: dict) -> list[DownloadSpec]:
    return [spec_for_requirement(page["slug"], r) for r in page["requirements"]]


# --- network ----------------------------------------------------------------------------

def make_client(token: str | None = None, transport: httpx.BaseTransport | None = None) -> httpx.Client:
    """An HTTPS-only client that refuses hosts outside ALLOWED_HOSTS, redirects included."""
    token = token if token is not None else os.environ.get("GITHUB_TOKEN")

    def guard(request: httpx.Request):
        if request.url.scheme != "https" or request.url.host not in ALLOWED_HOSTS:
            raise DownloadError(f"refusing to contact {request.url.host!r}", str(request.url))
        if token and request.url.host == "api.github.com":
            request.headers["Authorization"] = f"Bearer {token}"

    return httpx.Client(
        transport=transport, follow_redirects=True, timeout=httpx.Timeout(30.0, read=120.0),
        headers={"User-Agent": "3ds-cfw-assistant", "X-GitHub-Api-Version": "2022-11-28"},
        event_hooks={"request": [guard]},
    )


def resolve(spec: DownloadSpec, client: httpx.Client) -> ResolvedAsset:
    if spec.kind != "github":
        raise DownloadError(f"{spec.name} must be downloaded by hand: {spec.reason}", spec.url)
    api = f"https://api.github.com/repos/{spec.owner}/{spec.repo}/releases/"
    r = client.get(api + (f"tags/{spec.tag}" if spec.tag else "latest"),
                   headers={"Accept": "application/vnd.github+json"})
    if r.status_code != 200:
        raise DownloadError(f"GitHub returned {r.status_code} for {spec.name}", spec.url)
    release = r.json()
    assets = release.get("assets", [])
    if spec.asset:
        matches = [a for a in assets if a["name"] == spec.asset]
    else:
        matches = [a for a in assets if a["name"].lower().endswith(spec.asset_suffix.lower())]
    if len(matches) != 1:
        found = ", ".join(a["name"] for a in assets) or "none"
        raise DownloadError(f"{spec.name}: expected one {spec.asset or spec.asset_suffix} asset in "
                            f"{release.get('tag_name')}, found: {found}", spec.url)
    asset = matches[0]
    pinned = spec.tag is not None and spec.asset is not None
    if pinned and asset["browser_download_url"] != spec.url:
        raise DownloadError(f"{spec.name}: GitHub's URL for this asset differs from the guide's", spec.url)
    digest = asset.get("digest") or ""
    return ResolvedAsset(
        spec=spec, tag=release["tag_name"], asset_name=asset["name"],
        download_url=spec.url if pinned else asset["browser_download_url"], size=asset["size"],
        digest=digest.removeprefix("sha256:") if digest.startswith("sha256:") else None,
        published_at=release.get("published_at") or "",
    )


def plan_downloads(snapshot: GuideSnapshot, slugs: list[str], client: httpx.Client) -> DownloadPlan:
    """Resolve every requirement of the given stage pages (no file downloads yet)."""
    plan = DownloadPlan([], [])
    seen = set()
    for slug in slugs:
        for spec in specs_for_page(snapshot.stage_page(slug)):
            key = (spec.url, spec.asset, spec.asset_suffix, spec.requirement if spec.url is None else None)
            if key in seen:
                continue
            seen.add(key)
            if spec.kind == "github":
                plan.resolved.append(resolve(spec, client))
            elif spec.kind == "prerequisite":
                plan.prerequisites.append(spec)
            else:
                plan.manual.append(spec)
    return plan


# --- cache ------------------------------------------------------------------------------

def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(CHUNK):
            h.update(chunk)
    return h.hexdigest()


def cache_path(resolved: ResolvedAsset, cache_dir: Path) -> Path:
    s = resolved.spec
    return Path(cache_dir) / s.owner / s.repo / resolved.tag / resolved.asset_name


def _meta_path(path: Path) -> Path:
    return path.with_name(path.name + ".json")


def _read_meta(path: Path) -> dict | None:
    try:
        return json.loads(_meta_path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _write_meta(path: Path, meta: dict) -> None:
    _meta_path(path).write_text(json.dumps(meta, indent=1), encoding="utf-8")


def fetch(resolved: ResolvedAsset, client: httpx.Client, cache_dir: Path,
          progress: Callable[[str, int, int], None] | None = None) -> Downloaded:
    name = resolved.spec.name
    target = cache_path(resolved, cache_dir)
    meta = _read_meta(target)
    recorded = meta.get("sha256") if meta else None
    if recorded and resolved.digest and recorded != resolved.digest:
        raise ChecksumMismatch(f"{name}: the published checksum changed since this file was first downloaded",
                               resolved.download_url)

    if target.is_file() and recorded:
        actual = sha256_file(target)
        if actual == recorded and target.stat().st_size == resolved.size:
            return Downloaded(name, target, actual, "published" if resolved.digest else "recorded", True, resolved)

    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_name(target.name + ".part")
    h = hashlib.sha256()
    size = 0
    try:
        with client.stream("GET", resolved.download_url) as r:
            if r.status_code != 200:
                raise DownloadError(f"{name}: download failed with HTTP {r.status_code}", resolved.download_url)
            with open(part, "wb") as f:
                for chunk in r.iter_bytes(CHUNK):
                    f.write(chunk)
                    h.update(chunk)
                    size += len(chunk)
                    if progress:
                        progress(name, size, resolved.size)
        actual = h.hexdigest()
        if size != resolved.size:
            raise ChecksumMismatch(f"{name}: got {size} bytes, GitHub lists {resolved.size}", resolved.download_url)
        if resolved.digest and actual != resolved.digest:
            raise ChecksumMismatch(f"{name}: SHA-256 {actual} does not match the published {resolved.digest}",
                                   resolved.download_url)
        if recorded and actual != recorded:
            raise ChecksumMismatch(f"{name}: SHA-256 differs from the copy first downloaded ({recorded}); "
                                   "the release file has changed", resolved.download_url)
        os.replace(part, target)
    finally:
        part.unlink(missing_ok=True)

    _write_meta(target, {"url": resolved.download_url, "tag": resolved.tag, "size": size, "sha256": actual,
                         "published_sha256": resolved.digest,
                         "downloaded": datetime.now(timezone.utc).isoformat(timespec="seconds")})
    verified = "published" if resolved.digest else ("recorded" if recorded else "first-use")
    return Downloaded(name, target, actual, verified, False, resolved)


def download_all(plan: DownloadPlan, client: httpx.Client, cache_dir: Path,
                 progress: Callable[[str, int, int], None] | None = None) -> list[Downloaded]:
    """Fetch everything in the plan; the first checksum problem aborts the whole batch."""
    return [fetch(r, client, cache_dir, progress) for r in plan.resolved]


def import_manual(spec: DownloadSpec, src: Path, cache_dir: Path) -> Downloaded:
    """Copy a file the user downloaded by hand into the cache and record its hash."""
    src = Path(src)
    if not src.is_file() or src.stat().st_size == 0:
        raise DownloadError(f"{src} is not a usable file for {spec.name}", spec.url)
    slug = re.sub(r"[^\w.-]+", "_", spec.name)[:60] or "manual"
    target = Path(cache_dir) / "manual" / spec.page / slug / src.name
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, target)
    digest = sha256_file(target)
    if digest != sha256_file(src):
        raise ChecksumMismatch(f"copying {src} into the cache changed it", spec.url)
    _write_meta(target, {"source": str(src), "sha256": digest, "size": target.stat().st_size,
                         "imported": datetime.now(timezone.utc).isoformat(timespec="seconds")})
    return Downloaded(spec.name, target, digest, "user-provided", False, None)
