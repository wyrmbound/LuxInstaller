"""Copy the console's critical backups from the SD card to the PC and verify them.

Per Finalizing Setup Section IV, the finalize script leaves the SysNAND backup
(`<date>_<serial>_sysnand_##.bin` + `.bin.sha`) and `essential.exefs` in `/gm9/backups/`.
The older GM9Megascript flow used `/gm9/out/`. `seeddb.bin` is not mentioned by the guide,
so it is copied when present and never required. Nothing on the SD card is modified.
"""
from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

BACKUP_DIRS = ("gm9/backups", "gm9/out")
SEEDDB_DIRS = ("gm9/backups", "gm9/out", "gm9")
NAND_GLOB = "*_sysnand_*.bin"
ESSENTIAL_GLOB = "*_essential_*.exefs"
GUIDE_LINKS = (
    "https://3ds.hacks.guide/finalizing-setup (Section IV - Setup Script)",
    "https://3ds.hacks.guide/troubleshooting-finalizing-setup",
)


class BackupError(Exception):
    pass


class BackupMissing(BackupError):
    pass


@dataclass(frozen=True)
class BackupSet:
    nand_bin: Path | None
    nand_sha: Path | None
    essential: Path | None
    seeddb: Path | None
    found_in: str | None

    @property
    def missing(self) -> list[str]:
        out = []
        if not self.nand_bin:
            out.append("SysNAND backup (<date>_<serial>_sysnand_##.bin)")
        if not self.nand_sha:
            out.append("SysNAND checksum (<date>_<serial>_sysnand_##.bin.sha)")
        if not self.essential:
            out.append("essential.exefs")
        return out


def _present(path: Path) -> Path | None:
    return path if path.is_file() and path.stat().st_size > 0 else None


class _Meter:
    """Counts bytes processed across the whole backup for a progress(done, total, label) callback."""

    def __init__(self, total: int, progress=None):
        self.total, self.done, self.progress, self.label = total, 0, progress, ""

    def add(self, n: int) -> None:
        self.done += n
        if self.progress:
            self.progress(self.done, self.total, self.label)


def sha256_file(path: Path, meter: _Meter | None = None) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
            if meter:
                meter.add(len(chunk))
    return h.hexdigest()


def _copy(src: Path, dst: Path, meter: _Meter | None = None) -> None:
    with open(src, "rb") as fi, open(dst, "wb") as fo:
        for chunk in iter(lambda: fi.read(1 << 20), b""):
            fo.write(chunk)
            if meter:
                meter.add(len(chunk))


def find_backups(sd_root: Path) -> BackupSet:
    """Locate backups on the card (read-only). Zero-byte files count as missing.

    The NAND and essential.exefs are looked for separately: the NAND lands in /gm9/backups/ while
    essential.exefs is usually in /gm9/out/ (GodMode9 may also leave `<serial>_essential_##.exefs`
    in /gm9/backups/).
    """
    sd_root = Path(sd_root)
    nand = sha = None
    found_in = None
    for rel in BACKUP_DIRS:
        bins = sorted(p for p in (sd_root / rel).glob(NAND_GLOB) if _present(p))
        if bins:
            nand, found_in = bins[-1], rel
            sha = _present(nand.with_name(nand.name + ".sha"))
            break
    essential = next((p for rel in BACKUP_DIRS if (p := _present(sd_root / rel / "essential.exefs"))), None)
    if essential is None:
        named = sorted(p for rel in BACKUP_DIRS for p in (sd_root / rel).glob(ESSENTIAL_GLOB) if _present(p))
        essential = named[-1] if named else None
    seeddb = next((p for rel in SEEDDB_DIRS if (p := _present(sd_root / rel / "seeddb.bin"))), None)
    return BackupSet(nand, sha, essential, seeddb, found_in or (essential and essential.parent.relative_to(
        sd_root).as_posix()))


def verify_nand(bin_path: Path, sha_path: Path, meter: _Meter | None = None) -> str:
    """Check the NAND image against GodMode9's .sha (raw 32 bytes or hex text)."""
    raw = Path(sha_path).read_bytes()
    if len(raw) == 32:
        expected = raw.hex()
    else:
        expected = raw.decode("ascii", "ignore").strip().split()[0].lower() if raw.strip() else ""
    if meter:
        meter.label = f"Checking {Path(bin_path).name}"
    actual = sha256_file(bin_path, meter)
    if actual != expected:
        raise BackupError(f"NAND backup {Path(bin_path).name} does not match its .sha file "
                          f"(expected {expected or 'nothing'}, got {actual}). Do not rely on this backup; "
                          f"redo the backup. See {GUIDE_LINKS[1]}")
    return actual


def backup_to_pc(sd_root: Path, dest_root: Path, now: datetime | None = None, progress=None) -> Path:
    """Copy the backups into dest_root/<timestamp>/, verify every copy, write MANIFEST.json.

    `progress(done_bytes, total_bytes, label)` reports every byte read or written.
    """
    found = find_backups(sd_root)
    if found.missing:
        raise BackupMissing(
            "!!! NAND BACKUP INCOMPLETE !!!\n"
            f"Missing from the SD card: {', '.join(found.missing)}.\n"
            "Without these you cannot recover from a brick. Do not continue until this is fixed.\n"
            "Rerun the finalize script as described at:\n  " + "\n  ".join(GUIDE_LINKS)
        )
    sources = [s for s in (found.nand_bin, found.nand_sha, found.essential, found.seeddb) if s is not None]
    sizes = {s: s.stat().st_size for s in sources}
    # NAND hashed once against its .sha; other sources hashed once; every file copied and re-read.
    meter = _Meter(sum(sizes.values()) * 3, progress)
    nand_hash = verify_nand(found.nand_bin, found.nand_sha, meter)

    stamp = (now or datetime.now()).strftime("%Y-%m-%d_%H%M%S")
    dest = Path(dest_root) / stamp
    dest.mkdir(parents=True, exist_ok=False)
    manifest = {"source": str(sd_root), "found_in": found.found_in, "created": stamp, "files": {}}
    for src in sources:
        meter.label = f"Copying {src.name}"
        src_hash = nand_hash if src == found.nand_bin else sha256_file(src, meter)
        copy = dest / src.name
        _copy(src, copy, meter)
        meter.label = f"Checking the copy of {src.name}"
        if sha256_file(copy, meter) != src_hash:
            raise BackupError(f"The copy of {src.name} on the PC does not match the SD card. Try again.")
        manifest["files"][src.name] = {"sha256": src_hash, "size": src.stat().st_size}
    manifest["seeddb"] = "copied" if found.seeddb else "not present (optional)"
    (dest / "MANIFEST.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    return dest
