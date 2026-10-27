"""Turn a guide page's SD-card copy steps into an exact plan, show it, then apply it safely.

Only step wordings matched below become file operations. Any other step that writes to the
SD card lands in `plan.unparsed`, and apply_stage() refuses to run until it is empty, so the
assistant never guesses at a copy. Writes go through sdcard.assert_writable(), which keeps
them on the confirmed card and out of `Nintendo 3DS/`.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from guide_parser import GuideSnapshot
from sdcard import PROTECTED_DIR, ConfirmedDrive, DriveInfo, assert_writable

PREP_MARKER = "insert your sd card into your computer"

_FILES = r"(?P<files>`[^`]+`(?:(?:,\s*|\s+and\s+|,\s+and\s+)`[^`]+`)*)"
_DEST = r"(?:the root of your SD card|the `(?P<dir>/[^`]*)` folder on your SD card)"
COPY_ALL_RE = re.compile(
    r"^Copy everything from the (?P<name>.+?) `\.zip`(?: \([^)]*\))? to the root of your SD card\b", re.I)
COPY_FOLDER_FROM_ZIP_RE = re.compile(
    r"^Copy the `(?P<folder>[^`/]+)` folder from the (?P<name>.+?) `\.zip` to the root of your SD card\b", re.I)
COPY_FROM_ZIP_RE = re.compile(rf"^Copy {_FILES} from the (?P<name>.+?) `\.zip` to {_DEST}", re.I)
COPY_BY_TYPE_RE = re.compile(r"^Copy the (?P<name>.+?) `(?P<ext>\.\w+)` to the root of your SD card\b", re.I)
COPY_FILE_RE = re.compile(
    r"^Copy `(?P<file>[^`]+)` to the root of your SD card(?: and rename it to `(?P<rename>[^`]+)`)?\s*$", re.I)
COPY_TO_FOLDER_RE = re.compile(r"^Copy `(?P<file>[^`]+)` to the `(?P<folder>[^`/]+)` folder\s*$", re.I)
MKDIR_ROOT_RE = re.compile(r"^Create a folder named `(?P<dir>[^`]+)` on the root of your SD card\b", re.I)
MKDIR_INSIDE_RE = re.compile(
    r"^Open the `(?P<parent>[^`]+)` folder on your SD card and create a folder named `(?P<dir>[^`]+)` inside", re.I)
FILE_OP_RE = re.compile(r"\b(copy|move|create|rename|delete|extract|overwrite)\b", re.I)
OFF_CARD_RE = re.compile(r"\b(to|on) your computer\b|\bto a safe location\b|\bto your Desktop\b", re.I)


class StageError(Exception):
    pass


@dataclass(frozen=True)
class StageOp:
    kind: str  # mkdir, copy, extract
    source: str  # local file, or the member name inside `archive`
    dest: str  # posix, relative to the SD root
    overwrite: bool
    step: str
    archive: str | None = None


@dataclass
class StagePlan:
    slug: str
    section: str
    sd_root: Path
    ops: list[StageOp] = field(default_factory=list)
    unparsed: list[str] = field(default_factory=list)

    def render(self) -> str:
        lines = [f"Dry run for {self.slug}, Section {self.section} (SD card: {self.sd_root})", ""]
        for op in self.ops:
            dest = "/" + op.dest + ("/" if op.kind == "mkdir" else "")
            if op.kind == "mkdir":
                tag = "EXISTS" if op.overwrite else "MKDIR"
                lines.append(f"{tag:<9} {dest}")
            else:
                origin = Path(op.archive).name if op.archive else Path(op.source).name
                lines.append(f"{'OVERWRITE' if op.overwrite else 'NEW':<9} {dest}  (from {origin})")
        if not self.ops:
            lines.append("(nothing to copy)")
        if self.unparsed:
            lines += ["", "Unmapped steps (do these by hand / see guide link):"]
            lines += [f"  - {u}" for u in self.unparsed]
        return "\n".join(lines)


def _split_files(group: str) -> list[str]:
    return re.findall(r"`([^`]+)`", group)


def _safe_member(name: str) -> PurePosixPath:
    p = PurePosixPath(name.replace("\\", "/"))
    if p.is_absolute() or ".." in p.parts or (p.parts and ":" in p.parts[0]):
        raise StageError(f"refusing unsafe path {name!r} in archive")
    return p


def _find_download(downloads: list, name: str):
    matches = [d for d in downloads if d.name.casefold() == name.casefold()]
    return matches[0] if len(matches) == 1 else None


def _zip_members(path: Path) -> list[str]:
    with zipfile.ZipFile(path) as z:
        names = [i.filename for i in z.infolist() if not i.is_dir()]
    for n in names:
        _safe_member(n)
    return names


def _prep_section(page: dict, section: str | None) -> dict:
    for sec in page["sections"]:
        if section is not None:
            if sec["number"] == section:
                return sec
        elif any(PREP_MARKER in s["text"].casefold() for s in sec["steps"]):
            return sec
    raise StageError(f"{page['slug']}: no section {'%s ' % section if section else ''}copies files to the SD card")


def prep_section_number(page: dict) -> str | None:
    """The section whose SD copy steps plan_stage() handles by default, if the page has one."""
    try:
        return _prep_section(page, None)["number"]
    except StageError:
        return None


def plan_stage(snapshot: GuideSnapshot, slug: str, downloads: list, sd_root: Path,
               section: str | None = None) -> StagePlan:
    page = snapshot.stage_page(slug)
    sec = _prep_section(page, section)
    root = Path(sd_root)
    plan = StagePlan(slug, sec["number"], root)
    folders: dict[str, str] = {}  # folder name -> path made by an earlier step

    def add(kind, source, dest, step, archive=None):
        dest = PurePosixPath(dest).as_posix().strip("/")
        if dest.split("/")[0].casefold() == PROTECTED_DIR.casefold():
            raise _Unmapped(f"would write inside '{PROTECTED_DIR}'")
        target = root / dest
        exists = target.is_dir() if kind == "mkdir" else target.exists()
        plan.ops.append(StageOp(kind, source, dest, exists, step, archive))

    for step in sec["steps"]:
        text = step["text"].strip()
        before = len(plan.ops)
        try:
            if m := COPY_ALL_RE.match(text):
                dl = _need(downloads, m["name"])
                for member in _zip_members(dl.path):
                    add("extract", member, _safe_member(member).as_posix(), text, str(dl.path))
            elif m := COPY_FOLDER_FROM_ZIP_RE.match(text):
                dl = _need(downloads, m["name"])
                inside = [n for n in _zip_members(dl.path)
                          if _safe_member(n).parts[0].casefold() == m["folder"].casefold()]
                if not inside:
                    raise _Unmapped(f"{dl.path.name} has no `{m['folder']}` folder")
                for member in inside:
                    add("extract", member, _safe_member(member).as_posix(), text, str(dl.path))
            elif m := COPY_FROM_ZIP_RE.match(text):
                dl = _need(downloads, m["name"])
                members = _zip_members(dl.path)
                dest_dir = (m["dir"] or "/").strip("/")
                for wanted in _split_files(m["files"]):
                    hits = [n for n in members if PurePosixPath(n).name == wanted]
                    if len(hits) != 1:
                        raise _Unmapped(f"{wanted} appears {len(hits)} times in {dl.path.name}")
                    add("extract", hits[0], f"{dest_dir}/{wanted}" if dest_dir else wanted, text, str(dl.path))
            elif m := COPY_BY_TYPE_RE.match(text):
                dl = _need(downloads, m["name"])
                if dl.path.suffix.casefold() != m["ext"].casefold():
                    raise _Unmapped(f"{dl.path.name} is not a {m['ext']} file")
                add("copy", str(dl.path), dl.path.name, text)
            elif m := COPY_FILE_RE.match(text):
                dl = _by_file(downloads, m["file"])
                add("copy", str(dl.path), m["rename"] or m["file"], text)
            elif m := COPY_TO_FOLDER_RE.match(text):
                folder = folders.get(m["folder"].casefold())
                if folder is None:
                    raise _Unmapped(f"no earlier step says where the `{m['folder']}` folder is")
                dl = _by_file(downloads, m["file"])
                add("copy", str(dl.path), f"{folder}/{m['file']}", text)
            elif m := MKDIR_ROOT_RE.match(text):
                folders[m["dir"].casefold()] = m["dir"]
                add("mkdir", "", m["dir"], text)
            elif m := MKDIR_INSIDE_RE.match(text):
                path = f"{m['parent'].strip('/')}/{m['dir']}"
                folders[m["dir"].casefold()] = path
                add("mkdir", "", path, text)
            elif FILE_OP_RE.search(text) and "sd card" in text.casefold() and not OFF_CARD_RE.search(text):
                raise _Unmapped("the assistant doesn't recognise this step")
        except _Unmapped as e:
            del plan.ops[before:]
            plan.unparsed.append(f"{text} ({e})")
    return plan


class _Unmapped(Exception):
    pass


def _need(downloads: list, name: str):
    dl = _find_download(downloads, name)
    if dl is None:
        raise _Unmapped(f"no download named {name!r}")
    return dl


def _by_file(downloads: list, filename: str):
    hits = [d for d in downloads if d.path.name.casefold() == filename.casefold()
            or d.name.casefold() == filename.casefold()]
    if len(hits) != 1:
        raise _Unmapped(f"no single download provides {filename}")
    return hits[0]


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def apply_stage(plan: StagePlan, confirmed: ConfirmedDrive, current_drive: DriveInfo | None, log_path: Path,
                progress=None) -> list[str]:
    """Carry out a plan on the confirmed card; every write is logged as one JSON line.

    `progress(done, total, dest)` is called after each planned item.
    """
    if plan.unparsed:
        raise StageError("some steps could not be mapped; do them by hand first:\n" + "\n".join(plan.unparsed))
    confirmed.revalidate(current_drive)
    if Path(plan.sd_root).resolve() != confirmed.root.resolve():
        raise StageError("this plan was made for a different SD card")
    log_path = Path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    written = []

    def log(op: str, dest: str, sha: str | None):
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                "op": op, "dest": dest, "sha256": sha}) + "\n")

    def ensure_dir(rel: PurePosixPath):
        for i in range(1, len(rel.parts) + 1):
            d = assert_writable(confirmed, confirmed.root.joinpath(*rel.parts[:i]))
            if not d.is_dir():
                d.mkdir()
                log("mkdir", PurePosixPath(*rel.parts[:i]).as_posix(), None)

    for op in plan.ops:
        if progress and written:
            progress(len(written), len(plan.ops), written[-1])
        rel = PurePosixPath(op.dest)
        target = assert_writable(confirmed, confirmed.root / op.dest)
        if op.kind == "mkdir":
            ensure_dir(rel)
            written.append(op.dest)
            continue
        if rel.parent.parts:
            ensure_dir(rel.parent)
        tmp = assert_writable(confirmed, target.with_name(target.name + ".3dsa-part"))
        if op.kind == "extract":
            _safe_member(op.source)
            with zipfile.ZipFile(op.archive) as z, z.open(op.source) as src, open(tmp, "wb") as out:
                shutil.copyfileobj(src, out)
        else:
            shutil.copyfile(op.source, tmp)
        sha = _sha256(tmp)
        os.replace(tmp, target)
        log(op.kind, op.dest, sha)
        written.append(op.dest)
    if progress and written:
        progress(len(written), len(plan.ops), written[-1])
    return written
