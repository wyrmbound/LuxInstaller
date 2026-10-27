"""Find the SD card, check it against the guide's requirements, and guard every write to it.

Safety rules enforced here:
- Nothing is written without a ConfirmedDrive, which only confirm_drive() can create, and
  only for a removable, non-system FAT drive whose label the user typed.
- assert_writable() refuses any path outside the card and anything under `Nintendo 3DS/`.
  This module only ever reads that folder.
"""
from __future__ import annotations

import json
import logging
import os
import platform
import plistlib
import random
import re
import secrets
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Callable

from guide_parser import GuideSnapshot

log = logging.getLogger("3ds_assistant.sdcard")

GB = 1_000_000_000
PROTECTED_DIR = "Nintendo 3DS"
HEX32_RE = re.compile(r"^[0-9a-fA-F]{32}$")


# --- the guide's SD requirements --------------------------------------------------------
# The guide states these in prose, so they are constants here. rules_drift() checks that
# each source sentence is still in the guide snapshot, so a guide change can't go unnoticed.

@dataclass(frozen=True)
class GuideRule:
    page: str
    phrase: str


RULES = {
    "capacity": GuideRule("key-information", "Between 2GB and 128GB"),
    "large_cards": GuideRule("key-information", "Cards 200GB or larger"),
    "format": GuideRule("key-information", "FAT32 with MBR"),
    "fat16_small": GuideRule("formatting-sd-(linux)", "FAT16 partition with 32 KB cluster size"),
    "cluster_32k": GuideRule("formatting-sd-(linux)", "FAT32 partition with 32 KB cluster size"),
    "cluster_64k": GuideRule("formatting-sd-(linux)", "FAT32 partition with 64 KB cluster size"),
    "nand_space": GuideRule("troubleshooting-finalizing-setup", "at least 1.3GB"),
}
MIN_NOMINAL_GB = 2
MAX_RECOMMENDED_GB = 128
NOT_RECOMMENDED_FROM_GB = 200
FAT16_MAX_GB = 2
GOOD_CLUSTERS = (32 * 1024, 64 * 1024)
NAND_BACKUP_BYTES = int(1.3 * GB)
FORMAT_PAGES = {"Windows": "formatting-sd-(windows)", "macOS": "formatting-sd-(mac)", "Linux": "formatting-sd-(linux)"}
NOMINAL_SIZES_GB = (1, 2, 4, 8, 16, 32, 64, 128, 200, 256, 400, 512, 1000, 1024, 1500, 2048)


def page_text(page: dict) -> str:
    parts = []
    for b in page["blocks"]:
        parts += b["intro"]
        parts += [n["text"] for n in b["notes"]]
        for item in (*b["steps"], *b["bullets"]):
            parts += [item["text"], *item["details"]]
    return "\n".join(parts)


def rules_drift(snapshot: GuideSnapshot) -> list[str]:
    """Rules whose source sentence is no longer in the guide; empty means they still match."""
    drift = []
    for name, rule in RULES.items():
        if not snapshot.has_page(rule.page):
            drift.append(f"{name}: page {rule.page} is missing")
        elif rule.phrase not in page_text(snapshot.page(rule.page)):
            drift.append(f"{name}: {rule.page} no longer says {rule.phrase!r}")
    return drift


# --- drives and checks ------------------------------------------------------------------

@dataclass(frozen=True)
class DriveInfo:
    mount: str
    label: str
    filesystem: str  # normalized: FAT32, FAT16, exFAT, NTFS, ...
    cluster_size: int | None
    total_bytes: int
    free_bytes: int
    removable: bool
    system: bool
    partition_style: str | None  # MBR, GPT, or None if unknown
    device: str
    serial: str


@dataclass(frozen=True)
class Check:
    name: str
    level: str  # ok, info, warn, fail
    message: str
    guide: str | None = None


def nominal_gb(total_bytes: int) -> int:
    """The size printed on the card; formatted capacity is always a little smaller."""
    for size in NOMINAL_SIZES_GB:
        if total_bytes <= size * GB:
            return size
    return -(-total_bytes // GB)


def normalize_fs(name: str) -> str:
    key = (name or "").strip().lower().removeprefix("ms-dos ")
    return {"fat32": "FAT32", "fat16": "FAT16", "fat12": "FAT12", "fat": "FAT16", "exfat": "exFAT",
            "ntfs": "NTFS", "msdos": "FAT"}.get(key, (name or "").upper())


def evaluate(drive: DriveInfo, extra_bytes: int = 0, host_os: str | None = None) -> list[Check]:
    from methods import host_os_label

    fmt_page = FORMAT_PAGES.get(host_os or host_os_label(), "key-information")
    size = nominal_gb(drive.total_bytes)
    checks = []

    if drive.removable:
        checks.append(Check("removable", "ok", "Removable drive"))
    else:
        checks.append(Check("removable", "fail", "This is not a removable drive, so it can't be the 3DS SD card."))
    if drive.system:
        checks.append(Check("system", "fail", "This is a system drive. It will never be used."))
    else:
        checks.append(Check("system", "ok", "Not a system drive"))

    fs = drive.filesystem
    if fs == "FAT32" or (fs == "FAT16" and size <= FAT16_MAX_GB):
        checks.append(Check("filesystem", "ok", f"{fs}"))
    else:
        checks.append(Check("filesystem", "fail",
                            f"The card is {fs or 'unformatted'}; the guide needs FAT32 (FAT16 only on 2GB cards). "
                            "Format it with the guide's instructions. Formatting erases the card, so copy off "
                            "anything you want to keep first.", fmt_page))

    if drive.partition_style == "MBR":
        checks.append(Check("partition", "ok", "MBR partition table"))
    elif drive.partition_style is None:
        checks.append(Check("partition", "info", "Couldn't read the partition table type; the guide requires MBR.",
                            "key-information"))
    else:
        checks.append(Check("partition", "fail",
                            f"The card uses a {drive.partition_style} partition table; the guide requires MBR.", fmt_page))

    if drive.cluster_size in GOOD_CLUSTERS:
        checks.append(Check("cluster", "ok", f"{drive.cluster_size // 1024} KB clusters"))
    elif drive.cluster_size is None:
        checks.append(Check("cluster", "info", "Couldn't read the cluster size.", fmt_page))
    else:
        checks.append(Check("cluster", "warn",
                            f"{drive.cluster_size // 1024} KB clusters; the guide formats cards with 32 KB "
                            "(64 KB for large cards).", fmt_page))

    if size < MIN_NOMINAL_GB:
        checks.append(Check("capacity", "fail", f"A {size}GB card is too small: the NAND backup alone is about 1GB.",
                            "key-information"))
    elif size >= NOT_RECOMMENDED_FROM_GB:
        checks.append(Check("capacity", "warn", f"{size}GB cards work but the guide doesn't recommend 200GB or larger "
                            "(possible DS/GBA graphics and theme issues).", "key-information"))
    elif size > MAX_RECOMMENDED_GB:
        checks.append(Check("capacity", "warn", f"{size}GB is above the guide's 2GB-128GB range.", "key-information"))
    else:
        checks.append(Check("capacity", "ok", f"{size}GB card"))

    needed = NAND_BACKUP_BYTES + extra_bytes
    if drive.free_bytes >= needed:
        checks.append(Check("free_space", "ok", f"{drive.free_bytes / GB:.1f}GB free"))
    else:
        checks.append(Check("free_space", "fail",
                            f"{drive.free_bytes / GB:.2f}GB free; at least {needed / GB:.2f}GB is needed "
                            "(1.3GB for the NAND backup plus the files to copy).", "troubleshooting-finalizing-setup"))
    return checks


def usable(checks: list[Check]) -> bool:
    return not any(c.level == "fail" for c in checks)


# --- confirmation and write guard -------------------------------------------------------

class ConfirmationError(Exception):
    pass


class ProtectedPathError(Exception):
    pass


_TOKEN = object()


class ConfirmedDrive:
    """Proof that the user picked this drive by typing its label. Required for every write."""

    __slots__ = ("drive",)

    def __init__(self, drive: DriveInfo, token: object):
        if token is not _TOKEN:
            raise TypeError("use confirm_drive() to get a ConfirmedDrive")
        self.drive = drive

    @property
    def root(self) -> Path:
        return Path(self.drive.mount)

    def revalidate(self, current: DriveInfo | None) -> None:
        """Call before writing: refuse if a different card is now at this mount."""
        d = self.drive
        if current is None or (current.serial, current.label, current.mount, current.device) != (
                d.serial, d.label, d.mount, d.device):
            raise ConfirmationError(f"The card at {d.mount} has changed since you confirmed it. Pick the drive again.")


def confirmation_target(drive: DriveInfo) -> str:
    """What the user must type: the label, or the mount point when the card has no label."""
    return drive.label or drive.mount


def confirm_drive(drive: DriveInfo, typed: str, extra_bytes: int = 0) -> ConfirmedDrive:
    failures = [c.message for c in evaluate(drive, extra_bytes) if c.level == "fail"]
    if failures:
        raise ConfirmationError(f"{drive.mount} cannot be used: " + " ".join(failures))
    typed = (typed or "").strip()
    if drive.label:
        ok = typed.casefold() == drive.label.casefold()
    else:
        ok = bool(typed) and typed.rstrip("\\/") == drive.mount.rstrip("\\/")
    if not ok:
        raise ConfirmationError(f"That doesn't match. Type {confirmation_target(drive)!r} exactly to use {drive.mount}.")
    return ConfirmedDrive(drive, _TOKEN)


def is_protected(root: Path, path: Path) -> bool:
    try:
        rel = Path(path).resolve().relative_to(Path(root).resolve())
    except ValueError:
        return False
    return bool(rel.parts) and rel.parts[0].casefold() == PROTECTED_DIR.casefold()


def assert_writable(confirmed: ConfirmedDrive, path: Path) -> Path:
    """The resolved path, if it is on the confirmed card and outside `Nintendo 3DS/`."""
    if not isinstance(confirmed, ConfirmedDrive):
        raise TypeError("writes need a ConfirmedDrive")
    root = confirmed.root.resolve()
    target = Path(path).resolve()
    if not target.is_relative_to(root) or target == root:
        raise ProtectedPathError(f"{path} is not a file on the SD card")
    if is_protected(root, target):
        raise ProtectedPathError(f"{path} is inside '{PROTECTED_DIR}', which the assistant never modifies")
    return target


# --- reading the card -------------------------------------------------------------------

@dataclass
class Id0Info:
    name: str
    id1s: list[str]
    other: list[str]

    @property
    def user_id1_backups(self) -> list[str]:
        return [n for n in self.other if n.endswith("_user-id1")]


@dataclass
class N3dsLayout:
    present: bool
    folder: str | None
    id0s: list[Id0Info] = field(default_factory=list)
    issues: list[Check] = field(default_factory=list)


def _subdirs(path: Path) -> list[str]:
    return sorted(p.name for p in path.iterdir() if p.is_dir())


def scan_3ds_folder(root: Path) -> N3dsLayout:
    """Read (never modify) the `Nintendo 3DS` folder and report what MSET9 would complain about."""
    root = Path(root)
    folder = next((p for p in root.iterdir() if p.is_dir() and p.name.casefold() == PROTECTED_DIR.casefold()), None)
    if folder is None:
        return N3dsLayout(False, None, issues=[Check(
            "nintendo_3ds", "fail",
            "There is no 'Nintendo 3DS' folder on this card. MSET9 reports this as Error 01.",
            "troubleshooting-mset9")])

    layout = N3dsLayout(True, folder.name)
    for name in _subdirs(folder):
        if HEX32_RE.match(name):
            subs = _subdirs(folder / name)
            layout.id0s.append(Id0Info(name, [s for s in subs if HEX32_RE.match(s)],
                                       [s for s in subs if not HEX32_RE.match(s)]))
    if len(layout.id0s) != 1:
        layout.issues.append(Check("id0", "warn",
                                   f"You have {len(layout.id0s)} ID0 folders; there should be exactly 1. "
                                   "MSET9 reports this as Error 04.", "troubleshooting-mset9"))
    for id0 in layout.id0s:
        if id0.user_id1_backups:
            layout.issues.append(Check("mset9", "info",
                                       f"MSET9 is set up on this card: your data is backed up as "
                                       f"{', '.join(id0.user_id1_backups)}. The MSET9 script restores it.",
                                       "installing-boot9strap-(mset9-cli)"))
        elif len(id0.id1s) != 1:
            layout.issues.append(Check("id1", "warn",
                                       f"ID0 {id0.name[:8]}... has {len(id0.id1s)} ID1 folders; there should be "
                                       "exactly 1. MSET9 reports this as Error 05.", "troubleshooting-mset9"))
    return layout


@dataclass(frozen=True)
class CfwInfo:
    boot_firm: bool
    luma_dir: bool

    @property
    def already_modded(self) -> bool:
        return self.boot_firm and self.luma_dir


def detect_cfw(root: Path) -> CfwInfo:
    root = Path(root)
    return CfwInfo((root / "boot.firm").is_file(), (root / "luma").is_dir())


# --- integrity test (h2testw-style) -----------------------------------------------------
# Fills free space with data derived from a seed and the byte offset, then reads it back.
# Offset-derived data also catches fake-capacity cards that wrap writes onto earlier storage.
# For a true read from the card, have the user eject and reinsert it before verifying.

INTEGRITY_DIR = "3ds-assistant-h2test"
INTEGRITY_RESERVE = 16 * 1024 * 1024
MIB = 1024 * 1024


@dataclass
class PlanFile:
    path: str  # posix, relative to the card root
    size: int


@dataclass
class IntegrityPlan:
    seed: str
    chunk_size: int
    files: list[PlanFile]
    cancelled: bool = False

    @property
    def total_bytes(self) -> int:
        return sum(f.size for f in self.files)

    def to_dict(self) -> dict:
        return {"seed": self.seed, "chunk_size": self.chunk_size, "cancelled": self.cancelled,
                "files": [{"path": f.path, "size": f.size} for f in self.files]}

    @classmethod
    def from_dict(cls, d: dict) -> "IntegrityPlan":
        return cls(d["seed"], d["chunk_size"], [PlanFile(f["path"], f["size"]) for f in d["files"]], d["cancelled"])


@dataclass
class IntegrityResult:
    ok_bytes: int = 0
    bad_bytes: int = 0
    first_bad_offset: int | None = None
    missing_files: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.bad_bytes == 0 and not self.missing_files

    def _bad(self, offset: int, n: int) -> None:
        self.bad_bytes += n
        if self.first_bad_offset is None:
            self.first_bad_offset = offset


def _pattern(seed: str, index: int, chunk_size: int) -> bytes:
    return random.Random(f"{seed}:{index}").randbytes(chunk_size)


def write_integrity_test(confirmed: ConfirmedDrive, size: int | None = None, *, file_size: int = 1024 * MIB,
                         chunk_size: int = MIB, progress: Callable[[int, int], None] | None = None,
                         cancel: Callable[[], bool] | None = None) -> IntegrityPlan:
    """Write test files into INTEGRITY_DIR; keep the returned plan to verify and clean up."""
    if file_size % chunk_size:
        raise ValueError("file_size must be a multiple of chunk_size")
    test_dir = assert_writable(confirmed, confirmed.root / INTEGRITY_DIR)
    available = max(0, shutil.disk_usage(confirmed.root).free - INTEGRITY_RESERVE)
    total = available if size is None else min(size, available)

    plan = IntegrityPlan(secrets.token_hex(16), chunk_size, [])
    test_dir.mkdir(exist_ok=True)
    done = 0
    index = 0
    while done < total and not plan.cancelled:
        rel = PurePosixPath(INTEGRITY_DIR, f"{len(plan.files) + 1:05d}.h2w")
        path = assert_writable(confirmed, confirmed.root / rel)
        entry = PlanFile(rel.as_posix(), 0)
        plan.files.append(entry)
        log.info("write %s", path)
        with open(path, "wb") as f:
            while entry.size < file_size and done < total:
                if cancel and cancel():
                    plan.cancelled = True
                    break
                n = min(chunk_size, total - done)
                f.write(_pattern(plan.seed, index, chunk_size)[:n])
                index += 1
                entry.size += n
                done += n
                if progress:
                    progress(done, total)
            f.flush()
            os.fsync(f.fileno())
    return plan


def verify_integrity_test(confirmed: ConfirmedDrive, plan: IntegrityPlan,
                          progress: Callable[[int, int], None] | None = None) -> IntegrityResult:
    result = IntegrityResult()
    offset = index = 0
    total = plan.total_bytes
    for entry in plan.files:
        path = confirmed.root / entry.path
        chunks = -(-entry.size // plan.chunk_size)
        if not path.is_file():
            result.missing_files.append(entry.path)
            result._bad(offset, entry.size)
            offset += entry.size
            index += chunks
            continue
        with open(path, "rb") as f:
            remaining = entry.size
            while remaining:
                n = min(plan.chunk_size, remaining)
                got = f.read(n)
                if got == _pattern(plan.seed, index, plan.chunk_size)[:n]:
                    result.ok_bytes += n
                else:
                    result._bad(offset, n)
                index += 1
                offset += n
                remaining -= n
                if progress:
                    progress(offset, total)
    return result


def cleanup_integrity_test(confirmed: ConfirmedDrive, plan: IntegrityPlan) -> None:
    """Delete only the files this plan wrote, then the test folder if nothing else is in it."""
    for entry in plan.files:
        path = assert_writable(confirmed, confirmed.root / entry.path)
        if path.exists():
            log.info("delete %s", path)
            path.unlink()
    try:
        (confirmed.root / INTEGRITY_DIR).rmdir()
    except OSError:
        pass


# --- drive enumeration ------------------------------------------------------------------

def removable_only(drives: list[DriveInfo]) -> list[DriveInfo]:
    return [d for d in drives if d.removable and not d.system]


def list_drives() -> list[DriveInfo]:
    system = platform.system()
    if system == "Windows":
        return list_windows_drives()
    if system == "Darwin":
        return list_mac_drives()
    return list_linux_drives()


class _Win32:  # pragma: no cover - thin ctypes wrapper, exercised on real Windows only
    REMOVABLE, FIXED = 2, 3

    def __init__(self):
        import ctypes

        self.ctypes = ctypes
        self.k32 = ctypes.windll.kernel32
        self.k32.SetErrorMode(0x0001)  # no "insert a disk" dialogs for empty readers

    def letters(self) -> list[str]:
        mask = self.k32.GetLogicalDrives()
        return [chr(65 + i) for i in range(26) if mask >> i & 1]

    def drive_type(self, root: str) -> int:
        return self.k32.GetDriveTypeW(root)

    def volume(self, root: str):
        c = self.ctypes
        label, fs, serial = c.create_unicode_buffer(261), c.create_unicode_buffer(261), c.c_uint32()
        if not self.k32.GetVolumeInformationW(root, label, 261, c.byref(serial), None, None, fs, 261):
            raise OSError(f"no volume at {root}")
        return label.value, fs.value, serial.value

    def free_total(self, root: str):
        c = self.ctypes
        free, total = c.c_uint64(), c.c_uint64()
        if not self.k32.GetDiskFreeSpaceExW(root, None, c.byref(total), c.byref(free)):
            raise OSError(f"no size for {root}")
        return free.value, total.value

    def cluster(self, root: str):
        c = self.ctypes
        spc, bps, nf, nt = c.c_uint32(), c.c_uint32(), c.c_uint32(), c.c_uint32()
        if not self.k32.GetDiskFreeSpaceW(root, c.byref(spc), c.byref(bps), c.byref(nf), c.byref(nt)):
            return None
        return spc.value * bps.value

    def partition_style(self, letter: str):
        try:
            out = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command",
                 f"(Get-Partition -DriveLetter {letter} | Get-Disk).PartitionStyle"],
                capture_output=True, text=True, timeout=20).stdout.strip().upper()
        except (OSError, subprocess.TimeoutExpired):
            return None
        return out if out in ("MBR", "GPT") else None


def list_windows_drives(win=None) -> list[DriveInfo]:
    win = win or _Win32()
    system_letter = (os.environ.get("SystemDrive") or "C:")[0].upper()
    drives = []
    for letter in win.letters():
        root = f"{letter}:\\"
        kind = win.drive_type(root)
        if kind not in (win.REMOVABLE, win.FIXED):
            continue
        try:
            label, fs, serial = win.volume(root)
            free, total = win.free_total(root)
        except OSError:
            continue  # empty card reader slot
        drives.append(DriveInfo(
            mount=root, label=label, filesystem=normalize_fs(fs), cluster_size=win.cluster(root),
            total_bytes=total, free_bytes=free, removable=kind == win.REMOVABLE,
            system=letter == system_letter, partition_style=win.partition_style(letter),
            device=f"{letter}:", serial=f"{serial >> 16:04X}-{serial & 0xFFFF:04X}",
        ))
    return drives


def _statvfs(mount: str):
    return os.statvfs(mount)


LSBLK_COLUMNS = "NAME,PATH,RM,HOTPLUG,TYPE,FSTYPE,FSVER,LABEL,MOUNTPOINT,SIZE,PTTYPE,UUID"
LINUX_SYSTEM_MOUNTS = ("/", "/boot", "/home", "/usr", "/var")


def parse_lsblk(text: str) -> list[DriveInfo]:
    drives = []

    def walk(node: dict, parent: dict | None):
        mount = node.get("mountpoint") or next(iter(node.get("mountpoints") or []), None)
        removable = bool(node.get("rm") or node.get("hotplug") or (parent and (parent.get("rm") or parent.get("hotplug"))))
        if mount:
            fstype = (node.get("fstype") or "").lower()
            fs = normalize_fs(node.get("fsver") or "FAT") if fstype == "vfat" else normalize_fs(fstype)
            st = _statvfs(mount)
            pt = (node.get("pttype") or (parent or {}).get("pttype") or "").lower()
            drives.append(DriveInfo(
                mount=mount, label=node.get("label") or "", filesystem=fs,
                cluster_size=st.f_bsize if fstype == "vfat" else None,
                total_bytes=st.f_frsize * st.f_blocks, free_bytes=st.f_frsize * st.f_bavail,
                removable=removable,
                system=mount in LINUX_SYSTEM_MOUNTS or mount.startswith("/boot/"),
                partition_style={"dos": "MBR", "gpt": "GPT"}.get(pt), device=node.get("path") or node.get("name", ""),
                serial=node.get("uuid") or "",
            ))
        for child in node.get("children") or []:
            walk(child, node)

    for dev in json.loads(text).get("blockdevices", []):
        walk(dev, None)
    return drives


def list_linux_drives() -> list[DriveInfo]:  # pragma: no cover - needs lsblk
    out = subprocess.run(["lsblk", "-J", "-b", "-o", LSBLK_COLUMNS], capture_output=True, text=True, check=True, timeout=20)
    return parse_lsblk(out.stdout)


def parse_diskutil(volume_plist: bytes, whole_plist: bytes | None) -> DriveInfo:
    v = plistlib.loads(volume_plist)
    whole = plistlib.loads(whole_plist) if whole_plist else {}
    scheme = whole.get("Content", "")
    mount = v.get("MountPoint", "")
    removable = bool(v.get("RemovableMediaOrExternalDevice") or v.get("Removable") or v.get("Ejectable")
                     or v.get("RemovableMedia") in ("Removable", True))
    return DriveInfo(
        mount=mount, label=v.get("VolumeName", ""), filesystem=normalize_fs(v.get("FilesystemName", "")),
        cluster_size=v.get("VolumeAllocationBlockSize"), total_bytes=v.get("TotalSize", 0),
        free_bytes=v.get("FreeSpace", v.get("APFSContainerFree", 0)), removable=removable,
        system=mount == "/" or mount.startswith("/System"),
        partition_style={"FDisk_partition_scheme": "MBR", "GUID_partition_scheme": "GPT"}.get(scheme),
        device=v.get("DeviceIdentifier", ""), serial=v.get("VolumeUUID") or v.get("DiskUUID") or "",
    )


def list_mac_drives() -> list[DriveInfo]:  # pragma: no cover - needs diskutil
    def info(target: str) -> bytes:
        return subprocess.run(["diskutil", "info", "-plist", target], capture_output=True, check=True, timeout=20).stdout

    drives = []
    for vol in sorted(Path("/Volumes").iterdir()):
        try:
            raw = info(str(vol))
            parent = plistlib.loads(raw).get("ParentWholeDisk")
            drives.append(parse_diskutil(raw, info(parent) if parent else None))
        except (OSError, subprocess.SubprocessError, plistlib.InvalidFileException):
            continue
    return drives
