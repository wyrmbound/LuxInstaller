"""Which Luma3DS to end up with: the official release, a fork's GitHub release, or a local build.

Setup always installs official Luma3DS (the build the guide's steps and checks are written
for) and finishes with it, NAND backup included. A chosen fork replaces only /boot.firm at the
very end, and restore_official() puts the official one back, so an untested build can't get
in the way of the setup itself.
"""
from __future__ import annotations

import hashlib
import re
import shutil
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

FIRM_MAGIC = b"FIRM"
REPO_RE = re.compile(r"^(?:https://github\.com/)?(?P<owner>[\w.-]+)/(?P<repo>[\w.-]+?)(?:\.git)?/?$")


class LumaBuildError(Exception):
    def __init__(self, message: str, url: str | None = None):
        super().__init__(message)
        self.url = url


@dataclass(frozen=True)
class LumaChoice:
    kind: str  # "official", "github" or "file"
    repo: str = ""  # "owner/name" for github
    path: str = ""  # local .zip or .firm for file

    @property
    def custom(self) -> bool:
        return self.kind != "official"

    @property
    def label(self) -> str:
        if self.kind == "github":
            return self.repo
        if self.kind == "file":
            return Path(self.path).name
        return "Luma3DS (official)"

    def to_dict(self) -> dict:
        return {"kind": self.kind, "repo": self.repo, "path": self.path}

    @classmethod
    def from_dict(cls, d: dict | None) -> "LumaChoice":
        return cls(**{k: d.get(k, "") for k in ("kind", "repo", "path")}) if d else cls("official")


@dataclass(frozen=True)
class LumaBuild:
    choice: LumaChoice
    firm: Path  # extracted boot.firm, in the cache
    version: str  # release tag, or file name
    sha256: str
    verified: str  # "published", "first-use", "recorded", "user-provided"


def parse_repo(text: str) -> str:
    m = REPO_RE.match((text or "").strip())
    if not m:
        raise LumaBuildError("Enter the fork as owner/name, for example wyrmbound/LuxiOS.")
    return f"{m['owner']}/{m['repo']}"


def _sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def check_firm(data: bytes, where: str) -> None:
    if not data.startswith(FIRM_MAGIC):
        raise LumaBuildError(f"{where} isn't a 3DS boot.firm (it doesn't start with the FIRM header).")


def extract_boot_firm(archive: Path, dest_dir: Path) -> Path:
    """The single boot.firm inside a zip (root preferred), written to dest_dir/boot.firm."""
    with zipfile.ZipFile(archive) as z:
        names = [n for n in z.namelist() if PurePosixPath(n).name.casefold() == "boot.firm" and ".." not in n]
        root = [n for n in names if "/" not in n.strip("/")]
        pick = root or names
        if len(pick) != 1:
            raise LumaBuildError(f"{Path(archive).name} has {len(pick)} boot.firm files; expected exactly one.")
        data = z.read(pick[0])
    check_firm(data, f"boot.firm in {Path(archive).name}")
    dest_dir.mkdir(parents=True, exist_ok=True)
    out = dest_dir / "boot.firm"
    out.write_bytes(data)
    return out


def _pick_release(repo: str, client, url: str) -> tuple[str, bool]:
    """(tag, is_prerelease): the latest stable release, else the newest pre-release.

    GitHub's "latest release" skips pre-releases, and forks often only publish betas.
    """
    r = client.get(f"https://api.github.com/repos/{repo}/releases/latest",
                   headers={"Accept": "application/vnd.github+json"})
    if r.status_code == 200:
        return r.json()["tag_name"], False
    r = client.get(f"https://api.github.com/repos/{repo}/releases?per_page=20",
                   headers={"Accept": "application/vnd.github+json"})
    if r.status_code != 200:
        raise LumaBuildError(f"Couldn't read {repo}'s releases (GitHub returned {r.status_code}).", url)
    releases = [x for x in r.json() if not x.get("draft")]
    if not releases:
        raise LumaBuildError(f"{repo} has no published releases yet.", url)
    return releases[0]["tag_name"], bool(releases[0].get("prerelease"))


def resolve(choice: LumaChoice, client, cache_dir: Path) -> LumaBuild:
    """Download (or import) the chosen build and extract a checked boot.firm into the cache."""
    import downloader

    if choice.kind == "github":
        owner, repo = choice.repo.split("/", 1)
        url = f"https://github.com/{choice.repo}/releases"
        tag, prerelease = _pick_release(choice.repo, client, url)
        base = f"https://github.com/{choice.repo}/releases/download/{tag}/"
        # A bare boot.firm first: its published checksum then covers exactly what gets installed.
        last = None
        for spec in (downloader.DownloadSpec(choice.repo, "custom-luma", "", base + "boot.firm", "github", owner,
                                             repo, tag, asset="boot.firm"),
                     downloader.DownloadSpec(choice.repo, "custom-luma", "", url, "github", owner, repo, tag,
                                             asset_suffix=".zip")):
            try:
                resolved = downloader.resolve(spec, client)
                break
            except downloader.DownloadError as e:
                last = e
        else:
            raise LumaBuildError(f"{choice.repo} release {tag} has no boot.firm or .zip to install "
                                 f"({getattr(last, 'args', [''])[0]}).", url)
        got = downloader.fetch(resolved, client, cache_dir)
        if got.path.suffix.casefold() == ".zip":
            firm = extract_boot_firm(got.path, got.path.parent / "extracted")
        else:
            check_firm(got.path.read_bytes(), got.path.name)
            firm = got.path
        return LumaBuild(choice, firm, resolved.tag + (" (pre-release)" if prerelease else ""), _sha256(firm),
                         got.verified)

    if choice.kind == "file":
        src = Path(choice.path)
        if not src.is_file():
            raise LumaBuildError(f"{src} doesn't exist.")
        digest = _sha256(src)
        dest = Path(cache_dir) / "custom-luma" / digest[:16]
        if src.suffix.casefold() == ".zip":
            firm = extract_boot_firm(src, dest)
        else:
            check_firm(src.read_bytes(), src.name)
            dest.mkdir(parents=True, exist_ok=True)
            firm = dest / "boot.firm"
            shutil.copyfile(src, firm)
        return LumaBuild(choice, firm, src.name, _sha256(firm), "user-provided")

    raise LumaBuildError("Official Luma3DS is installed by the normal setup; there's nothing extra to fetch.")
