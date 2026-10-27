"""The whole CLI flow with the real stager, stages, state and backup modules on a fake SD card."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

import flow_harness as fh

MSET9_CLI = "installing-boot9strap-(mset9-cli)"


@pytest.fixture
def world(tmp_path, monkeypatch):
    w = fh.make_world(tmp_path)
    fh.install(w, monkeypatch.setattr)
    return w


def state(world) -> dict:
    return json.loads((world["app"] / "state.json").read_text(encoding="utf-8"))


def test_full_run_stages_verifies_and_backs_up(world):
    console = fh.Console(world)
    code = fh.run(world, console)
    out = "\n".join(console.lines)
    assert code == 0, out[-3000:]
    sd = world["sd"]
    assert (sd / "mset9.py").is_file() and (sd / "boot9strap" / "boot9strap.firm").is_file()
    assert (sd / "finalize.romfs").is_file() and (sd / "luma" / "payloads" / "x_finalize_helper.firm").is_file()
    assert "All 4 copied files read back correctly" in out  # MSET9 zip
    assert out.count("Stage ") == 9
    assert "MSET9 is removed and your ID1 is back in place" in out
    s = state(world)
    assert s["stage"] is None and "backup" in s["completed"]
    backup_dir = Path(s["backup_dir"])
    assert backup_dir.parent == world["backups"]
    assert sorted(p.name for p in backup_dir.iterdir()) == [
        "20261027_0123456789_sysnand_00.bin", "20261027_0123456789_sysnand_00.bin.sha", "MANIFEST.json",
        "essential.exefs"]
    assert "Do NOT delete anything yet" in out
    assert "Setup complete!" in out
    # the backups were copied, nothing was deleted from the card
    assert (sd / "gm9" / "backups" / "essential.exefs").is_file()


def test_every_stage_header_has_guide_and_troubleshooting_links(world):
    console = fh.Console(world)
    fh.run(world, console)
    for i, line in enumerate(console.lines):
        if line.startswith("Stage "):
            assert console.lines[i + 2].startswith("Guide: https://3ds.hacks.guide/")
            assert "https://3ds.hacks.guide/troubleshooting" in console.lines[i + 3]


def test_failed_check_blocks_the_next_stage(world):
    console = fh.Console(world)
    console.skip_effects = {"Removing MSET9"}  # user didn't run the removal; MSET9 still set up
    calls = {"n": 0}
    orig = console.__call__

    import cli

    def answer(prompt):
        if prompt == cli.FIX_PROMPT:
            calls["n"] += 1
            if calls["n"] == 2:  # second look: now actually remove MSET9 like the guide says
                console.skip_effects = set()
                console.console_effects()
        return orig(prompt)

    code = cli.run(["--no-tui", "--guide-dir", str(fh.GUIDE), "--backup-dir", str(world["backups"])],
                   inp=answer, out=console.out)
    out = "\n".join(console.lines)
    assert code == 0, out[-2000:]
    assert out.count("MSET9 is still set up") == 2
    i_fail = out.index("MSET9 is still set up")
    assert "Stage 5/9" in out and out.index("Stage 5/9") > i_fail  # finalizing only after the fix


def test_missing_nand_backup_fails_loudly(world):
    console = fh.Console(world)
    console.skip_effects = {"Setup Script"}
    import cli

    def inp(prompt):
        if prompt == cli.FIX_PROMPT:
            return "q"
        return console(prompt)

    code = cli.run(["--no-tui", "--guide-dir", str(fh.GUIDE), "--backup-dir", str(world["backups"])],
                   inp=inp, out=console.out)
    out = "\n".join(console.lines)
    assert code == 0 and "backups are not complete" in out and "Progress saved" in out
    assert "backup" not in state(world)["completed"]


def test_backup_stage_refuses_when_backups_vanish(world, monkeypatch):
    """Even if the check passed, a backup that's missing at copy time must fail loudly."""
    import backup

    console = fh.Console(world)
    real = backup.backup_to_pc

    def vanish(sd_root, dest_root, now=None):
        for p in (Path(sd_root) / "gm9" / "backups").glob("*sysnand*"):
            p.unlink()
        return real(sd_root, dest_root, now)

    monkeypatch.setattr(backup, "backup_to_pc", vanish)
    code = fh.run(world, console)
    out = "\n".join(console.lines)
    assert code == 2
    assert "!!! NAND BACKUP INCOMPLETE !!!" in out and "!" * 72 in out
    assert "backup" not in state(world)["completed"]


def test_quit_and_resume_mid_process(world):
    first = fh.Console(world, stop_at="Removing MSET9")
    assert fh.run(world, first) == 130
    s = state(world)
    assert s["stage"] == f"{MSET9_CLI}#IV"
    assert f"{MSET9_CLI}#III" in s["completed"] and f"{MSET9_CLI}#IV" not in s["completed"]

    second = fh.Console(world)
    code = fh.run(world, second)
    out = "\n".join(second.lines)
    assert code == 0, out[-2000:]
    assert "Saved progress: Old 3DS, Ver. 11.17.0-50E" in out and "Next: Installing boot9strap (MSET9 CLI) - Section IV" in out
    assert not any(p.startswith("System version") for p in second.prompts)  # no intake again
    assert "Stage 1/9" not in out and "Stage 4/9" in out
    assert "Setup complete!" in out


def test_resume_can_start_over(world):
    assert fh.run(world, fh.Console(world, stop_at="Removing MSET9")) == 130
    again = fh.Console(world, extra=["n", "y"])  # resume? n, start over? y
    code = fh.run(world, again, "--dry-run")
    out = "\n".join(again.lines)
    assert code == 0, out[-2000:]
    assert any(p.startswith("System version") for p in again.prompts)  # intake again
    # boot9strap + Luma were installed in the first run, so a fresh start must not reinstall.
    assert "https://3ds.hacks.guide/checking-for-cfw" in out
    assert not any(p.startswith("Type WRITE") for p in again.prompts)


def test_already_modded_card_gets_luma_and_gm9_updates(world, monkeypatch):
    import io
    import types
    import zipfile

    import cli

    sd = world["sd"]
    (sd / "luma" / "payloads").mkdir(parents=True)
    (sd / "boot.firm").write_bytes(b"old luma")
    (sd / "luma" / "payloads" / "GodMode9.firm").write_bytes(b"old gm9")

    def zipped(name, files):
        b = io.BytesIO()
        with zipfile.ZipFile(b, "w") as z:
            for k, v in files.items():
                z.writestr(k, v)
        p = world["dl"] / name
        p.write_bytes(b.getvalue())
        return p

    luma = zipped("Luma3DSv13.4.zip", {"boot.firm": "new luma", "boot.3dsx": "hbl"})
    gm9 = zipped("GodMode9-v2.2.zip", {"GodMode9.firm": "new gm9", "gm9/scripts/x.gm9": "s", "README.md": "r"})
    fetched = []

    def fake_fetch(specs):
        fetched.extend(s.name for s in specs)
        files = {"Luma3DS": luma, "GodMode9": gm9}
        return [types.SimpleNamespace(name=s.name, path=files[s.name], sha256="", verified="published", from_cache=False)
                for s in specs if s.kind == "github"]

    monkeypatch.setattr(cli, "_fetch_specs", fake_fetch)
    console = fh.Console(world, extra=[fh.OLD3DS, fh.VERSION, "1", "3DS", "13.4", "y", "y", "WRITE"])
    code = fh.run(world, console)
    out = "\n".join(console.lines)
    assert code == 0, out[-2000:]
    assert "https://3ds.hacks.guide/restoring-updating-cfw" in out
    assert (sd / "boot.firm").read_bytes() == b"new luma"
    assert (sd / "luma" / "payloads" / "GodMode9.firm").read_bytes() == b"new gm9"
    assert (sd / "gm9" / "scripts" / "x.gm9").is_file() and not (sd / "README.md").exists()
    assert "OVERWRITE /boot.firm" in out and "read back correctly" in out
    assert sorted(fetched) == ["GodMode9", "Luma3DS"]


def test_dry_run_skips_console_stages_and_records_nothing(world, monkeypatch):
    """safecerthax starts with a console-only section; a dry run must go straight to the copy plan."""
    import io
    import types
    import zipfile

    import cli

    def zipped(name, files):
        b = io.BytesIO()
        with zipfile.ZipFile(b, "w") as z:
            for k, v in files.items():
                z.writestr(k, v)
        p = world["dl"] / name
        p.write_bytes(b.getvalue())
        return types.SimpleNamespace(name=name.split("-")[0].split("v1")[0], path=p, sha256="", verified="published",
                                     from_cache=False)

    downloads = [zipped("Luma3DSv13.4.zip", {"boot.firm": "l", "boot.3dsx": "h"}),
                 zipped("boot9strap-1.4.zip", {"boot9strap.firm": "b", "boot9strap.firm.sha": "s"}),
                 zipped("SafeB9SInstaller-20170605.zip", {"SafeB9SInstaller.bin": "i"})]
    downloads[0].name = "Luma3DS"
    monkeypatch.setattr(cli, "get_downloads", lambda *a: downloads)
    console = fh.Console(world, extra=["3", "11.4.0-35u", "1", "3DS"])
    code = fh.run(world, console, "--dry-run")
    out = "\n".join(console.lines)
    assert code == 0, out[-2000:]
    assert "Method: Installing boot9strap (safecerthax)" in out
    assert "Stage 2/11" in out and "Section II: Prep Work" in out
    assert "Section I: Hardware Button Check" not in out
    assert not any(p == cli.STEPS_DONE for p in console.prompts)
    assert state(world)["completed"] == []


def test_finished_setup_is_not_redone(world):
    assert fh.run(world, fh.Console(world)) == 0
    again = fh.Console(world)
    assert fh.run(world, again) == 0
    assert "Setup is already finished" in "\n".join(again.lines)
    assert not any(p.startswith("Type WRITE") for p in again.prompts)


def test_resume_needs_the_label_typed_before_writing(world):
    # Stop before finalizing's file copy, resume in a new session: writing needs the label again.
    first = fh.Console(world, stop_at="Finalizing Setup - Section II")
    assert fh.run(world, first) == 130
    second = fh.Console(world)
    assert fh.run(world, second) == 0
    assert sum("type the drive label" in p for p in second.prompts) == 0  # finalize I already staged

    # Now one that stops right before finalize staging.
    w2 = fh.make_world(world["base"] / "w2")
    fh.install(w2)
    try:
        first = fh.Console(w2, stop_at="Section IV: Removing MSET9")
        assert fh.run(w2, first) == 130
        second = fh.Console(w2)
        assert fh.run(w2, second) == 0
        assert sum("type the drive label" in p for p in second.prompts) == 1
    finally:
        fh.install(world)


def test_resume_after_the_process_is_killed(tmp_path):
    """Kill the real process while it waits inside a stage, then resume in-process."""
    proc = subprocess.Popen([sys.executable, "-u", str(Path(fh.__file__)), str(tmp_path)],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                            cwd=str(tmp_path))
    world = fh.make_world(tmp_path)
    console = fh.Console(world)  # used only to compute answers and console effects
    deadline = time.time() + 60
    try:
        while time.time() < deadline:
            line = proc.stdout.readline()
            if not line:
                break
            line = line.rstrip("\n")
            console.out(line)
            if "Section III: Installing boot9strap" in console.stage and line == console_prompt():
                proc.kill()  # mid-stage: III started, not finished
                break
            if line.endswith(": ") or line.endswith("] ") or line.endswith(") "):
                proc.stdin.write(console(line) + "\n")
                proc.stdin.flush()
    finally:
        proc.kill()
        proc.wait(10)
    s = json.loads((tmp_path / "app" / "state.json").read_text(encoding="utf-8"))
    assert s["stage"] == f"{MSET9_CLI}#III" and f"{MSET9_CLI}#III" not in s["completed"]

    import cli
    import paths
    import sdcard

    saved = {n: getattr(paths, n) for n in ("APP_DIR", "CACHE_DIR", "STATE_PATH", "SNAPSHOT_PATH")}
    saved_drives, saved_dl = sdcard.list_drives, cli.get_downloads
    fh.install(world)
    try:
        second = fh.Console(world)
        code = fh.run(world, second)
    finally:
        for n, v in saved.items():
            setattr(paths, n, v)
        sdcard.list_drives, cli.get_downloads = saved_drives, saved_dl
    out = "\n".join(second.lines)
    assert code == 0, out[-2000:]
    assert "Next: Installing boot9strap (MSET9 CLI) - Section III" in out
    assert "Setup complete!" in out


def console_prompt() -> str:
    import cli

    return cli.STEPS_DONE
