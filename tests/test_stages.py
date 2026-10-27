from __future__ import annotations

import hashlib
import json
import types
from pathlib import Path

import pytest

import stages
from stages import boot9strap, finalize, mset9, prep

MSET9_CLI = "installing-boot9strap-(mset9-cli)"
ID0 = "0123456789abcdef0123456789abcdef"
ID1 = "fedcba9876543210fedcba9876543210"


def levels(checks):
    return [c.level for c in checks]


# --- building stages from the guide ----------------------------------------------------

def test_mset9_stages_follow_the_guide(real_snapshot):
    built = stages.build_stages(real_snapshot, MSET9_CLI)
    assert [s.key for s in built] == [
        f"{MSET9_CLI}#I", f"{MSET9_CLI}#II", f"{MSET9_CLI}#III", f"{MSET9_CLI}#IV",
        "finalizing-setup#I", "finalizing-setup#II", "finalizing-setup#III", "finalizing-setup#IV",
        "backup",
    ]
    by = {s.key: s for s in built}
    assert [k for k, s in by.items() if s.stages_files] == [f"{MSET9_CLI}#I", "finalizing-setup#I"]
    assert by[f"{MSET9_CLI}#III"].verifiers == (boot9strap.verify_installed,)
    assert by[f"{MSET9_CLI}#IV"].verifiers == (mset9.verify_removed,)
    assert by["finalizing-setup#IV"].verifiers == (finalize.verify_backups,)
    assert not by[f"{MSET9_CLI}#II"].needs_card and by["backup"].needs_card


@pytest.mark.parametrize("slug", [
    "installing-boot9strap-(soundhax)", "installing-boot9strap-(ssloth-browser)",
    "installing-boot9strap-(safecerthax)", "installing-boot9strap-(super-skaterhax)",
])
def test_every_method_verifies_the_boot9strap_install(real_snapshot, slug):
    built = stages.build_stages(real_snapshot, slug)
    assert sum(boot9strap.verify_installed in s.verifiers for s in built) == 1
    assert not any(mset9.verify_removed in s.verifiers for s in built)


def test_every_stage_links_guide_and_troubleshooting(real_snapshot):
    for s in stages.build_stages(real_snapshot, MSET9_CLI):
        assert s.url.startswith("https://3ds.hacks.guide/")
        assert "https://3ds.hacks.guide/troubleshooting" in s.troubleshooting
    by = {s.key: s for s in stages.build_stages(real_snapshot, MSET9_CLI)}
    assert by[f"{MSET9_CLI}#II"].url == "https://3ds.hacks.guide/installing-boot9strap-(mset9-cli)#section-ii-mset9"
    assert "https://3ds.hacks.guide/troubleshooting-mset9" in by[f"{MSET9_CLI}#II"].troubleshooting


@pytest.mark.parametrize("heading, slug", [
    ("Section II - MSET9", "section-ii-mset9"),
    ("Section III - RTC and DSP setup", "section-iii-rtc-and-dsp-setup"),
    ("Section I - Prep Work", "section-i-prep-work"),
])
def test_anchor(heading, slug):
    assert stages.anchor(heading) == slug


def test_finalize_backup_steps_are_annotated(real_snapshot):
    st = {s.key: s for s in stages.build_stages(real_snapshot, MSET9_CLI)}["finalizing-setup#IV"]
    notes = [st.annotate(step["text"]) for step in st.section["steps"]]
    assert any(n and "copy for you" in n for n in notes)
    assert any(n and "Do NOT delete" in n for n in notes)


# --- verifiers -------------------------------------------------------------------------

def test_boot9strap_verifier(tmp_path):
    assert levels(boot9strap.verify_installed(tmp_path)) == ["fail", "warn"]
    (tmp_path / "boot.firm").write_bytes(b"x")
    assert levels(boot9strap.verify_installed(tmp_path)) == ["ok", "warn"]
    (tmp_path / "luma").mkdir()
    (tmp_path / "luma" / "config.ini").write_text("x")
    assert levels(boot9strap.verify_installed(tmp_path)) == ["ok", "ok"]


def test_mset9_removed_verifier(tmp_path):
    id0 = tmp_path / "Nintendo 3DS" / ID0
    (id0 / f"{ID1}_user-id1").mkdir(parents=True)
    (id0 / "sdmc_hax_id1").mkdir()
    checks = mset9.verify_removed(tmp_path)
    assert levels(checks) == ["fail"] and "still set up" in checks[0].message
    (id0 / "sdmc_hax_id1").rmdir()
    (id0 / f"{ID1}_user-id1").rename(id0 / ID1)
    assert levels(mset9.verify_removed(tmp_path)) == ["ok"]


def test_mset9_removed_without_folder(tmp_path):
    assert levels(mset9.verify_removed(tmp_path)) == ["fail"]


def test_finalize_backups_verifier(tmp_path):
    assert levels(finalize.verify_backups(tmp_path)) == ["fail"]
    b = tmp_path / "gm9" / "backups"
    b.mkdir(parents=True)
    (b / "x_sysnand_00.bin").write_bytes(b"n")
    (b / "x_sysnand_00.bin.sha").write_bytes(hashlib.sha256(b"n").digest())
    assert "essential.exefs" in finalize.verify_backups(tmp_path)[0].message
    (b / "essential.exefs").write_bytes(b"e")
    assert levels(finalize.verify_backups(tmp_path)) == ["ok"]


def test_verify_staged_reads_back(tmp_path):
    (tmp_path / "boot.firm").write_bytes(b"firm")
    (tmp_path / "boot9strap").mkdir()
    plan = types.SimpleNamespace(ops=[
        types.SimpleNamespace(kind="mkdir", dest="boot9strap"),
        types.SimpleNamespace(kind="copy", dest="boot.firm"),
    ])
    log = tmp_path / "log.jsonl"
    log.write_text(json.dumps({"dest": "boot.firm", "sha256": hashlib.sha256(b"firm").hexdigest()}) + "\n")
    assert levels(prep.verify_staged(tmp_path, plan, log)) == ["ok"]
    (tmp_path / "boot.firm").write_bytes(b"FIRM")
    assert "differ" in prep.verify_staged(tmp_path, plan, log)[0].message
    (tmp_path / "boot.firm").unlink()
    assert "missing" in prep.verify_staged(tmp_path, plan, log)[0].message
