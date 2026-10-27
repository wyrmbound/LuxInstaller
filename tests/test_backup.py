from __future__ import annotations

import hashlib
import json
from datetime import datetime

import pytest

from backup import BackupError, BackupMissing, backup_to_pc, find_backups, verify_nand

NAND = "20261027_0123456789_sysnand_00.bin"
NOW = datetime(2026, 10, 27, 12, 30, 5)


def make_sd(root, where="gm9/backups", nand=b"nand-image" * 100, sha=None, essential=b"exefs", seeddb=None,
            sha_hex=False):
    d = root / where
    d.mkdir(parents=True)
    if nand is not None:
        (d / NAND).write_bytes(nand)
        digest = sha if sha is not None else hashlib.sha256(nand).digest()
        (d / (NAND + ".sha")).write_bytes(digest.hex().encode() if sha_hex else digest)
    if essential is not None:
        (d / "essential.exefs").write_bytes(essential)
    if seeddb is not None:
        (root / "gm9" / "seeddb.bin").write_bytes(seeddb)
    return root


def tree(root):
    return sorted((p.relative_to(root).as_posix(), p.stat().st_mtime_ns, p.stat().st_size) for p in root.rglob("*"))


def test_happy_path(tmp_path):
    sd = make_sd(tmp_path / "sd")
    before = tree(sd)
    dest = backup_to_pc(sd, tmp_path / "pc", now=NOW)
    assert dest.name == "2026-10-27_123005"
    assert sorted(p.name for p in dest.iterdir()) == sorted([NAND, NAND + ".sha", "essential.exefs", "MANIFEST.json"])
    manifest = json.loads((dest / "MANIFEST.json").read_text())
    assert manifest["files"][NAND]["sha256"] == hashlib.sha256(b"nand-image" * 100).hexdigest()
    assert manifest["seeddb"].startswith("not present")
    assert tree(sd) == before  # SD untouched


def test_missing_nand_fails_loudly(tmp_path):
    sd = make_sd(tmp_path / "sd", nand=None)
    with pytest.raises(BackupMissing) as e:
        backup_to_pc(sd, tmp_path / "pc", now=NOW)
    msg = str(e.value)
    assert "NAND BACKUP INCOMPLETE" in msg and "sysnand" in msg
    assert "https://3ds.hacks.guide/finalizing-setup" in msg
    assert "https://3ds.hacks.guide/troubleshooting-finalizing-setup" in msg
    assert not (tmp_path / "pc").exists()


def test_missing_essential_fails(tmp_path):
    sd = make_sd(tmp_path / "sd", essential=None)
    with pytest.raises(BackupMissing, match="essential.exefs"):
        backup_to_pc(sd, tmp_path / "pc", now=NOW)


def test_zero_byte_nand_counts_as_missing(tmp_path):
    sd = make_sd(tmp_path / "sd", nand=b"")
    with pytest.raises(BackupMissing):
        backup_to_pc(sd, tmp_path / "pc", now=NOW)


def test_sha_mismatch_fails(tmp_path):
    sd = make_sd(tmp_path / "sd", sha=b"\x00" * 32)
    with pytest.raises(BackupError, match="does not match"):
        backup_to_pc(sd, tmp_path / "pc", now=NOW)


def test_hex_sha_is_accepted(tmp_path):
    sd = make_sd(tmp_path / "sd", sha_hex=True)
    found = find_backups(sd)
    assert verify_nand(found.nand_bin, found.nand_sha) == hashlib.sha256(b"nand-image" * 100).hexdigest()


def test_legacy_gm9_out_location(tmp_path):
    sd = make_sd(tmp_path / "sd", where="gm9/out")
    assert find_backups(sd).found_in == "gm9/out"
    assert (backup_to_pc(sd, tmp_path / "pc", now=NOW) / NAND).is_file()


def test_nand_and_essential_in_different_folders(tmp_path):
    """A real Old 2DS card: NAND in /gm9/backups/ (with a serial-named essential copy), plain
    essential.exefs in /gm9/out/ (troubleshooting-finalizing-setup keeps it there)."""
    sd = tmp_path / "sd"
    nand_name = "261027_XX01234567_sysnand_00.bin"
    b = sd / "gm9" / "backups"
    b.mkdir(parents=True)
    nand = b"N" * 4096
    (b / nand_name).write_bytes(nand)
    (b / (nand_name + ".sha")).write_bytes(hashlib.sha256(nand).digest())
    (b / "XX01234567_essential_00.exefs").write_bytes(b"E" * 512)
    (sd / "gm9" / "out").mkdir()
    (sd / "gm9" / "out" / "essential.exefs").write_bytes(b"E" * 512)

    found = find_backups(sd)
    assert not found.missing
    assert found.nand_bin.name == nand_name and found.found_in == "gm9/backups"
    dest = backup_to_pc(sd, tmp_path / "pc", now=NOW)
    assert nand_name in {p.name for p in dest.iterdir()}


def test_serial_named_essential_alone_counts(tmp_path):
    sd = make_sd(tmp_path / "sd", essential=None)
    (sd / "gm9" / "backups" / "XX01234567_essential_00.exefs").write_bytes(b"E" * 512)
    assert not find_backups(sd).missing


def test_seeddb_copied_when_present(tmp_path):
    sd = make_sd(tmp_path / "sd", seeddb=b"seeds")
    dest = backup_to_pc(sd, tmp_path / "pc", now=NOW)
    assert (dest / "seeddb.bin").read_bytes() == b"seeds"
    assert json.loads((dest / "MANIFEST.json").read_text())["seeddb"] == "copied"
