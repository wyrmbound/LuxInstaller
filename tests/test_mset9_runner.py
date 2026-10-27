"""The official MSET9 script driven by mset9_runner on a fake SD card (never a real drive)."""
from __future__ import annotations

import os
import sys
import zipfile
from pathlib import Path

import pytest

import mset9_runner
from mset9_runner import Mset9Error, console_index, run

ZIP = Path.home() / ".3ds-assistant" / "cache" / "hacks-guide" / "MSET9" / "v2.2" / "MSET9-v2.2.zip"
ID0 = "0123456789abcdef0123456789abcdef"
ID1 = "fedcba9876543210fedcba9876543210"
DB_SIZE = 0x31E400

pytestmark = pytest.mark.skipif(not ZIP.is_file(), reason="MSET9 zip not in the download cache")


def no_root_moves(source: str) -> str:
    """Test-only: tmp isn't a mount, so drop the two calls that check/move files to the drive root."""
    lines = source.splitlines(keepends=True)
    patched = [l for l in lines if l.strip() not in ("verify_device()", "dig_for_root()")]
    assert len(lines) - len(patched) == 2
    return "".join(patched)


@pytest.fixture
def sd(tmp_path, monkeypatch):
    root = tmp_path / "SD"
    root.mkdir()
    with zipfile.ZipFile(ZIP) as z:
        z.extractall(root)
    (root / "Nintendo 3DS" / ID0 / ID1 / "dbs").mkdir(parents=True)
    monkeypatch.setattr(mset9_runner, "_is_mount", lambda p: True)
    return root


def go(sd, action, index=1):
    return run(sd, index, action, _patch=no_root_moves)


def id0(sd) -> Path:
    return sd / "Nintendo 3DS" / ID0


def hacked(sd) -> Path:
    found = [p for p in id0(sd).iterdir() if "sdmc" in p.name and len(p.name) == 32]
    assert len(found) == 1, [p.name for p in id0(sd).iterdir()]
    return found[0]


def console_prepares(sd, mii=True):
    """What powering on, opening Mii Maker and resetting the title database does on the console."""
    h = hacked(sd)
    (h / "extdata" / "00000000" / "0000008F").mkdir(parents=True, exist_ok=True)
    if mii:
        (h / "extdata" / "00000000" / "00000217").mkdir(parents=True, exist_ok=True)
    for db in ("title.db", "import.db"):
        with open(h / "dbs" / db, "wb") as f:
            f.truncate(DB_SIZE)


def test_console_index_from_the_script(sd):
    assert console_index(sd, "old", 11, 17) == 1
    assert console_index(sd, "new", 11, 17) == 2
    assert console_index(sd, "old", 11, 4) == 3
    assert console_index(sd, "new", 11, 7) == 4
    assert console_index(sd, "old", 11, 8) == 1
    with pytest.raises(Mset9Error):
        console_index(sd, "old", 11, 3)


def test_full_cycle(sd):
    assert go(sd, "status").state == "ID1 not created"

    r = go(sd, "create")
    assert r.ok and r.message == "Created the MSET9 ID1."
    assert (id0(sd) / f"{ID1}_user-id1").is_dir() and not (id0(sd) / ID1).exists()
    assert go(sd, "create").ok  # running it again is harmless

    not_ready = go(sd, "ready")
    assert not not_ready.ok and not not_ready.report["HOME menu extdata"]

    console_prepares(sd)
    ready = go(sd, "ready")
    assert ready.ok and ready.state == "Ready"

    inj = go(sd, "inject")
    assert inj.ok and (hacked(sd) / "extdata" / "002F003A.txt").is_file()
    assert go(sd, "status").state == "Injected"
    assert go(sd, "inject").ok  # already injected

    rem = go(sd, "remove")
    assert rem.ok and rem.message.startswith("MSET9 removed")
    names = sorted(p.name for p in id0(sd).iterdir())
    assert names == [ID1]  # hacked ID1 gone, the user's real ID1 back under its own name
    assert (id0(sd) / ID1 / "dbs").is_dir()
    assert go(sd, "remove").ok  # nothing left to remove


def injected(sd):
    go(sd, "create")
    console_prepares(sd)
    assert go(sd, "inject").ok


def test_remove_without_a_stdout_fd(sd):
    """The windowed exe has no fd 1. mset9.py's remove() checks os.path.exists(<path> and <bool>),
    i.e. os.path.exists(True), which stats fd 1: without it the user's ID1 was never renamed back."""
    injected(sd)
    saved = os.dup(1)
    os.close(1)
    try:
        rem = go(sd, "remove")
    finally:
        os.dup2(saved, 1)
        os.close(saved)
    assert rem.ok and "Renaming original ID1" in rem.output
    assert sorted(p.name for p in id0(sd).iterdir()) == [ID1]


def test_remove_finishes_a_half_done_removal(sd):
    """Hacked ID1 deleted but the real one still `<ID1>_user-id1`: the guide's manual removal renames it."""
    injected(sd)
    import shutil
    shutil.rmtree(hacked(sd))
    assert go(sd, "status").state == "ID1 not created"  # the script itself now says "Nothing to do."
    rem = go(sd, "remove")
    assert rem.ok and "renamed" in rem.message
    assert sorted(p.name for p in id0(sd).iterdir()) == [ID1]


def test_remove_never_overwrites_an_existing_id1(sd):
    injected(sd)
    import shutil
    shutil.rmtree(hacked(sd))
    (id0(sd) / ID1).mkdir()
    rem = go(sd, "remove")
    assert not rem.ok
    assert sorted(p.name for p in id0(sd).iterdir()) == sorted([ID1, f"{ID1}_user-id1"])


def test_ready_reports_missing_mii_maker(sd):
    go(sd, "create")
    console_prepares(sd, mii=False)
    r = go(sd, "ready")
    assert not r.ok
    assert r.report == {"HOME menu extdata": True, "Mii Maker extdata": False, "Title database": True}
    assert "Mii Maker extdata" in r.message


def test_script_errors_become_friendly(sd):
    (id0(sd).parent / ("1" * 32)).mkdir()  # a second ID0
    r = go(sd, "create")
    assert not r.ok and any("Error 04" in e for e in r.errors)
    assert "exactly one ID0" in r.message and mset9_runner.TROUBLE in r.message


def test_unexpected_prompt_is_a_failure(sd):
    out, eof = mset9_runner._execute(sd, "", no_root_moves)  # no answers at all
    assert eof
    r = mset9_runner.run(sd, 9, "status", _patch=no_root_moves)  # 9 is not a valid console number
    assert not r.ok


def test_streams_and_cwd_restored(sd):
    before = (sys.stdin, sys.stdout, sys.stderr, os.getcwd(), os.system)
    go(sd, "status")
    assert (sys.stdin, sys.stdout, sys.stderr, os.getcwd(), os.system) == before


def test_refuses_when_not_a_mount_point(tmp_path):
    root = tmp_path / "SD"
    root.mkdir()
    (root / "mset9.py").write_text("raise SystemExit('must not run')\n")
    with pytest.raises(Mset9Error, match="root of a drive"):
        run(root, 1, "status")
    assert (root / "mset9.py").read_text().startswith("raise")


def test_refuses_without_the_script(tmp_path):
    with pytest.raises(Mset9Error, match="isn't on the SD card"):
        run(tmp_path, 1, "status")
