from __future__ import annotations

import json
import zipfile
from dataclasses import dataclass, replace
from pathlib import Path

import pytest

import sdcard
import stager
from sdcard import ProtectedPathError
from stager import StageError, apply_stage, plan_stage
from test_sdcard import GOOD

SOUNDHAX = "installing-boot9strap-(soundhax)"
SSLOTH = "installing-boot9strap-(ssloth-browser)"
SKATER = "installing-boot9strap-(super-skaterhax)"
MSET9 = "installing-boot9strap-(mset9-cli)"


@dataclass
class FakeDownload:
    name: str
    path: Path
    sha256: str = ""
    verified: str = "published"
    from_cache: bool = False


def make_zip(path: Path, members: dict[str, bytes]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as z:
        for name, data in members.items():
            z.writestr(name, data)
    return path


def make_file(path: Path, data: bytes = b"data") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


@pytest.fixture
def cache(tmp_path):
    c = tmp_path / "cache"
    return {
        "Soundhax": FakeDownload("Soundhax", make_file(c / "soundhax-usa-o3ds.m4a", b"m4a")),
        "universal-otherapp": FakeDownload("universal-otherapp", make_file(c / "otherapp.bin", b"otherapp")),
        "Luma3DS": FakeDownload("Luma3DS", make_zip(c / "Luma3DSv13.4.zip", {"boot.firm": b"luma", "boot.3dsx": b"hbl"})),
        "boot9strap": FakeDownload("boot9strap", make_zip(c / "boot9strap-1.4.zip", {
            "boot9strap.firm": b"b9s", "boot9strap.firm.sha": b"sha", "README.md": b"readme"})),
        "SafeB9SInstaller": FakeDownload("SafeB9SInstaller", make_zip(c / "SafeB9SInstaller.zip", {
            "SafeB9SInstaller.bin": b"sb9si", "SafeB9SInstaller.firm": b"x"})),
        "x_finalize_helper.firm": FakeDownload("x_finalize_helper.firm", make_file(c / "x_finalize_helper.firm", b"fh")),
        "finalize.romfs": FakeDownload("finalize.romfs", make_file(c / "finalize.romfs", b"romfs")),
        "MSET9": FakeDownload("MSET9", make_zip(c / "MSET9-v2.2.zip", {"mset9.py": b"py", "MSET9-Windows.bat": b"bat"})),
    }


@pytest.fixture
def sd(tmp_path):
    root = tmp_path / "sd"
    (root / "Nintendo 3DS").mkdir(parents=True)
    return root


def dests(plan):
    return [(op.kind, op.dest) for op in plan.ops]


def test_soundhax_dry_run_maps_every_prep_step(real_snapshot, cache, sd):
    plan = plan_stage(real_snapshot, SOUNDHAX, list(cache.values()), sd)
    assert plan.section == "I" and plan.unparsed == []
    assert dests(plan) == [
        ("copy", "soundhax-usa-o3ds.m4a"),
        ("copy", "otherapp.bin"),
        ("extract", "boot.firm"),
        ("extract", "boot.3dsx"),
        ("mkdir", "boot9strap"),
        ("extract", "boot9strap/boot9strap.firm"),
        ("extract", "boot9strap/boot9strap.firm.sha"),
        ("extract", "SafeB9SInstaller.bin"),
    ]
    out = plan.render()
    assert "NEW       /boot.firm  (from Luma3DSv13.4.zip)" in out
    assert "MKDIR     /boot9strap/" in out
    assert "Unmapped" not in out


def test_ssloth_renames_otherapp(real_snapshot, cache, sd):
    plan = plan_stage(real_snapshot, SSLOTH, list(cache.values()), sd)
    assert ("copy", "arm11code.bin") in dests(plan)
    assert plan.unparsed == []


def test_finalizing_uses_the_payloads_folder(real_snapshot, cache, sd):
    plan = plan_stage(real_snapshot, "finalizing-setup", list(cache.values()), sd)
    assert dests(plan) == [
        ("copy", "finalize.romfs"),
        ("mkdir", "luma/payloads"),
        ("copy", "luma/payloads/x_finalize_helper.firm"),
    ]


def test_mset9_copies_whole_zip(real_snapshot, cache, sd):
    plan = plan_stage(real_snapshot, MSET9, list(cache.values()), sd)
    assert sorted(dests(plan)) == [("extract", "MSET9-Windows.bat"), ("extract", "mset9.py")]
    assert plan.unparsed == []


def test_overwrite_and_existing_folder_are_shown(real_snapshot, cache, sd):
    make_file(sd / "boot.firm", b"old")
    (sd / "boot9strap").mkdir()
    out = plan_stage(real_snapshot, SOUNDHAX, list(cache.values()), sd).render()
    assert "OVERWRITE /boot.firm  (from Luma3DSv13.4.zip)" in out
    assert "EXISTS    /boot9strap/" in out


def test_missing_download_becomes_unmapped(real_snapshot, cache, sd):
    downloads = [d for n, d in cache.items() if n != "Soundhax"]
    plan = plan_stage(real_snapshot, SOUNDHAX, downloads, sd)
    assert len(plan.unparsed) == 1 and "Soundhax" in plan.unparsed[0]
    assert all(op.dest != "soundhax-usa-o3ds.m4a" for op in plan.ops)
    assert "Unmapped steps" in plan.render()


def test_skater_without_its_zip_is_unmapped(real_snapshot, cache, sd):
    plan = plan_stage(real_snapshot, SKATER, list(cache.values()), sd)
    assert plan.ops == [] and len(plan.unparsed) == 1


def test_zip_path_traversal_is_refused(real_snapshot, cache, sd, tmp_path):
    evil = make_zip(tmp_path / "evil" / "MSET9.zip", {"../../outside.txt": b"x"})
    with pytest.raises(StageError, match="unsafe"):
        plan_stage(real_snapshot, MSET9, [FakeDownload("MSET9", evil)], sd)


def test_zip_writing_into_nintendo_3ds_is_unmapped(real_snapshot, sd, tmp_path):
    z = make_zip(tmp_path / "z" / "MSET9.zip", {"Nintendo 3DS/hax/x.bin": b"x", "mset9.py": b"py"})
    plan = plan_stage(real_snapshot, MSET9, [FakeDownload("MSET9", z)], sd)
    assert plan.ops == [] and "Nintendo 3DS" in plan.unparsed[0]


# --- applying ------------------------------------------------------------------------

def confirmed_for(sd: Path):
    drive = replace(GOOD, mount=str(sd), device=str(sd))
    return sdcard.confirm_drive(drive, "3DS"), drive


def tree(root: Path):
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*"))


def test_apply_writes_exactly_the_plan_and_logs_it(real_snapshot, cache, sd, tmp_path):
    plan = plan_stage(real_snapshot, SOUNDHAX, list(cache.values()), sd)
    confirmed, drive = confirmed_for(sd)
    log = tmp_path / "logs" / "writes.jsonl"
    written = apply_stage(plan, confirmed, drive, log)
    assert written == [op.dest for op in plan.ops]
    assert tree(sd) == sorted([
        "Nintendo 3DS", "soundhax-usa-o3ds.m4a", "otherapp.bin", "boot.firm", "boot.3dsx", "boot9strap",
        "boot9strap/boot9strap.firm", "boot9strap/boot9strap.firm.sha", "SafeB9SInstaller.bin",
    ])
    assert (sd / "boot9strap" / "boot9strap.firm").read_bytes() == b"b9s"
    entries = [json.loads(l) for l in log.read_text().splitlines()]
    files = [e for e in entries if e["op"] != "mkdir"]
    assert [e["dest"] for e in files] == [op.dest for op in plan.ops if op.kind != "mkdir"]
    assert all(len(e["sha256"]) == 64 for e in files)


def test_apply_creates_parent_folders(real_snapshot, cache, sd, tmp_path):
    plan = plan_stage(real_snapshot, "finalizing-setup", list(cache.values()), sd)
    confirmed, drive = confirmed_for(sd)
    apply_stage(plan, confirmed, drive, tmp_path / "w.jsonl")
    assert (sd / "luma" / "payloads" / "x_finalize_helper.firm").read_bytes() == b"fh"


def test_apply_refuses_with_unmapped_steps(real_snapshot, cache, sd, tmp_path):
    plan = plan_stage(real_snapshot, SKATER, list(cache.values()), sd)
    confirmed, drive = confirmed_for(sd)
    with pytest.raises(StageError, match="by hand"):
        apply_stage(plan, confirmed, drive, tmp_path / "w.jsonl")
    assert tree(sd) == ["Nintendo 3DS"]


def test_apply_refuses_a_swapped_card(real_snapshot, cache, sd, tmp_path):
    plan = plan_stage(real_snapshot, SOUNDHAX, list(cache.values()), sd)
    confirmed, drive = confirmed_for(sd)
    with pytest.raises(sdcard.ConfirmationError):
        apply_stage(plan, confirmed, replace(drive, serial="0000-0000"), tmp_path / "w.jsonl")
    assert tree(sd) == ["Nintendo 3DS"]


def test_apply_refuses_nintendo_3ds_dest(sd, tmp_path):
    src = make_file(tmp_path / "x.bin")
    plan = stager.StagePlan("p", "I", sd, ops=[stager.StageOp("copy", str(src), "Nintendo 3DS/x.bin", False, "s")])
    confirmed, drive = confirmed_for(sd)
    with pytest.raises(ProtectedPathError):
        apply_stage(plan, confirmed, drive, tmp_path / "w.jsonl")
    assert not (sd / "Nintendo 3DS" / "x.bin").exists()


def test_apply_refuses_traversal_member(sd, tmp_path):
    z = make_zip(tmp_path / "z.zip", {"../evil": b"x"})
    plan = stager.StagePlan("p", "I", sd, ops=[stager.StageOp("extract", "../evil", "evil", False, "s", str(z))])
    confirmed, drive = confirmed_for(sd)
    with pytest.raises(StageError):
        apply_stage(plan, confirmed, drive, tmp_path / "w.jsonl")
