from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

import cli
import methods
import sdcard
from conftest import FIXTURES

GUIDE = FIXTURES / "guide_63013be"
OLD3DS = str(list(methods.MODELS).index("old3ds") + 1)


class Script:
    def __init__(self, answers):
        self.answers = list(answers)
        self.prompts = []

    def __call__(self, prompt):
        self.prompts.append(prompt)
        if not self.answers:
            raise EOFError
        return self.answers.pop(0)


@pytest.fixture
def world(tmp_path, monkeypatch):
    """Real guide fixture + real routing; fake drive list, downloader, stager and state."""
    calls = {"plan_downloads": 0, "apply_stage": 0, "saved": []}
    drive = sdcard.DriveInfo(
        mount=str(tmp_path), label="3DS", filesystem="FAT32", cluster_size=32768,
        total_bytes=31_900_000_000, free_bytes=20_000_000_000, removable=True, system=False,
        partition_style="MBR", device=str(tmp_path), serial="1234-ABCD",
    )
    monkeypatch.setattr(sdcard, "list_drives", lambda: [drive])

    resolved = types.SimpleNamespace(spec=types.SimpleNamespace(name="Luma3DS"), tag="v13.4",
                                     asset_name="Luma3DSv13.4.zip", size=553125, digest="b5" * 32,
                                     published_at="2026-04-02")
    pinned = replace_ns(resolved, spec=types.SimpleNamespace(name="boot9strap"), tag="1.4",
                        asset_name="boot9strap-1.4.zip", size=11207, digest=None)

    def plan_downloads(snap, slugs, client):
        calls["plan_downloads"] += 1
        calls["slugs"] = slugs
        return types.SimpleNamespace(resolved=[resolved, pinned], manual=[])

    def download_all(plan, client, cache_dir, progress=None):
        return [types.SimpleNamespace(name=r.spec.name, path=Path(cache_dir) / r.asset_name, sha256="ab" * 32,
                                      verified="published" if r.digest else "first-use", from_cache=False)
                for r in plan.resolved]

    class DownloadError(Exception):
        pass

    downloader = types.SimpleNamespace(make_client=lambda: None, plan_downloads=plan_downloads,
                                       download_all=download_all, import_manual=None,
                                       DownloadError=DownloadError, ChecksumMismatch=DownloadError)
    monkeypatch.setitem(sys.modules, "downloader", downloader)

    def apply_stage(plan, confirmed, current, log_path):
        calls["apply_stage"] += 1

    import paths
    import stager

    monkeypatch.setattr(stager, "plan_stage", lambda snap, slug, downloads, root, section=None: types.SimpleNamespace(
        ops=[], unparsed=[], render=lambda: "COPY Luma3DSv13.4.zip:boot.firm -> /boot.firm"))
    monkeypatch.setattr(stager, "apply_stage", apply_stage)

    app = tmp_path.parent / f"{tmp_path.name}-app"
    monkeypatch.setattr(paths, "APP_DIR", app)
    monkeypatch.setattr(paths, "STATE_PATH", app / "state.json")
    monkeypatch.setattr(paths, "CACHE_DIR", app / "cache")
    calls["state_path"] = app / "state.json"
    return calls


def replace_ns(ns, **kw):
    return types.SimpleNamespace(**{**vars(ns), **kw})


def run(answers, *extra):
    lines = []
    script = Script(answers)
    code = cli.run(["--guide-dir", str(GUIDE), *extra], inp=script, out=lines.append)
    return code, "\n".join(lines), script


def test_happy_path_to_dry_run(world):
    code, out, _ = run([OLD3DS, "Ver. 11.17.0-50E", "1", "3ds"], "--dry-run")
    assert code == 0, out
    assert "installing-boot9strap-(mset9)" in out
    assert "https://3ds.hacks.guide/installing-boot9strap-(mset9-cli)" in out
    assert "Luma3DSv13.4.zip v13.4" in out and "published sha256" in out
    assert "boot9strap-1.4.zip 1.4" in out and "no published checksum" in out
    assert "COPY Luma3DSv13.4.zip:boot.firm -> /boot.firm" in out
    assert "Dry run" in out
    assert world["slugs"] == ["installing-boot9strap-(mset9-cli)", "finalizing-setup"]
    assert world["apply_stage"] == 0
    import json

    assert json.loads(world["state_path"].read_text())["method"] == "installing-boot9strap-(mset9-cli)"


def test_write_needs_typed_WRITE(world):
    code, out, _ = run([OLD3DS, "Ver. 11.17.0-50E", "1", "3DS", "write"])
    assert code == 1 and "Nothing was written" in out
    assert world["apply_stage"] == 0
    code, out, _ = run([OLD3DS, "Ver. 11.17.0-50E", "1", "3DS", "WRITE"], "--restart")
    # Files written, then the run waits inside stage 1 for the console steps; input ends there.
    assert code == 130 and world["apply_stage"] == 1
    assert "Stage 1/9" in out


def test_wrong_label_is_refused(world):
    code, out, _ = run([OLD3DS, "Ver. 11.17.0-50E", "1", "SD", "E:", "3DSX"])
    assert code == 1
    assert out.count("doesn't match") == 3
    assert world["plan_downloads"] == 0


def test_invalid_version_is_asked_again(world):
    code, out, script = run([OLD3DS, "eleven", "11.17.0-51E", "Ver. 11.3.0-36U", "1", "3DS"], "--dry-run")
    assert code == 0, out
    assert sum("System version" in p for p in script.prompts) == 3
    assert "installing-boot9strap-(soundhax)" in out


def test_cfw_prefix_stops_with_guide_message(world):
    code, out, _ = run([OLD3DS, "Sys. 11.17.0-50U"])
    assert code == 1
    assert "may already have custom firmware" in out
    assert "https://3ds.hacks.guide/get-started" in out
    assert world["plan_downloads"] == 0


def test_routing_error_prints_link(world, monkeypatch):
    def boom(*a, **k):
        raise methods.RoutingError("the guide's routing script failed: boom")

    monkeypatch.setattr(methods, "select_method", boom)
    code, out, _ = run([OLD3DS, "Ver. 11.17.0-50E"])
    assert code == 2
    assert "routing script failed" in out and "See: https://3ds.hacks.guide/get-started" in out


def test_already_modded_card_points_to_checking_for_cfw(world, tmp_path):
    (tmp_path / "luma").mkdir()
    (tmp_path / "boot.firm").write_bytes(b"x")
    code, out, _ = run([OLD3DS, "Ver. 11.17.0-50E", "1", "3DS", "7.1"])
    assert code == 0
    assert "https://3ds.hacks.guide/checking-for-cfw" in out
    assert "Hold the (Select) button" in out  # the guide's own steps
    assert "https://3ds.hacks.guide/updating-b9s" in out  # the guide's route for Luma3DS 7.1
    assert world["plan_downloads"] == 0


def test_manual_item_blank_path_stops(world, monkeypatch):
    spec = types.SimpleNamespace(name="Super-skaterhax", url="https://skater.nintendohomebrew.com",
                                 requirement="The Super-skaterhax `.zip` for your console's region", kind="manual")
    dl = sys.modules["downloader"]
    monkeypatch.setattr(dl, "plan_downloads", lambda s, slugs, c: types.SimpleNamespace(resolved=[], manual=[spec]))
    code, out, _ = run([str(list(methods.MODELS).index("new3ds") + 1), "Ver. 11.17.0-50U", "1", "3DS", ""])
    assert code == 1
    assert "https://skater.nintendohomebrew.com" in out and "Nothing was written" in out


PYTHON = types.SimpleNamespace(name="Python", url="https://www.python.org/downloads/", kind="prerequisite",
                               requirement="Any 3.x version of Python **installed on your computer**")


def _with_prereq(monkeypatch, world):
    dl = sys.modules["downloader"]
    orig = dl.plan_downloads

    def plan(s, slugs, c):
        p = orig(s, slugs, c)
        p.prerequisites = [PYTHON]
        return p

    monkeypatch.setattr(dl, "plan_downloads", plan)


def test_prerequisite_confirmed_without_file_prompt(world, monkeypatch):
    _with_prereq(monkeypatch, world)
    code, out, script = run([OLD3DS, "Ver. 11.17.0-50E", "1", "3DS", "y"], "--dry-run")
    assert code == 0, out
    assert "Prerequisite: Python" in out and "https://www.python.org/downloads/" in out
    assert not any("Path to the downloaded file" in p for p in script.prompts)


@pytest.mark.parametrize("answer", ["", "n", "no"])
def test_prerequisite_not_installed_stops(world, monkeypatch, answer):
    _with_prereq(monkeypatch, world)
    code, out, _ = run([OLD3DS, "Ver. 11.17.0-50E", "1", "3DS", answer])
    assert code == 1 and "install Python first" in out


def test_real_downloader_dataclasses_fit(world, monkeypatch):
    import importlib

    monkeypatch.delitem(sys.modules, "downloader")  # drop the fake; use the real module's types
    real = importlib.import_module("downloader")
    spec = real.DownloadSpec("Python", "installing-boot9strap-(mset9-cli)", PYTHON.requirement, PYTHON.url, "prerequisite")
    plan = real.DownloadPlan(resolved=[], manual=[], prerequisites=[spec])
    monkeypatch.setattr(real, "plan_downloads", lambda s, slugs, c: plan)
    monkeypatch.setattr(real, "download_all", lambda p, c, d, progress=None: [])
    monkeypatch.setattr(real, "make_client", lambda: None)
    code, out, _ = run([OLD3DS, "Ver. 11.17.0-50E", "1", "3DS", "yes"], "--dry-run")
    assert code == 0, out
    assert "Prerequisite: Python" in out


def _fake_frontends(monkeypatch, gui_run=None):
    import tui

    seen = []
    monkeypatch.setattr(cli, "run", lambda argv: seen.append(("cli", argv)) or 0)
    monkeypatch.setattr(tui, "run_tui", lambda argv: seen.append(("tui", argv)) or 0)
    gui_run = gui_run or (lambda argv: seen.append(("gui", argv)) or 0)
    monkeypatch.setitem(sys.modules, "gui", types.SimpleNamespace(run_gui=gui_run))
    return seen


def test_main_dispatches_by_flag(world, monkeypatch):
    import main

    seen = _fake_frontends(monkeypatch)
    assert main.main(["--no-tui", "--dry-run"]) == 0
    assert main.main(["--tui", "--dry-run"]) == 0
    assert main.main(["--dry-run", "--backup-dir", "B"]) == 0
    assert seen == [("cli", ["--no-tui", "--dry-run"]), ("tui", ["--dry-run"]),
                    ("gui", ["--dry-run", "--backup-dir", "B"])]


def test_main_falls_back_to_tui_without_a_display(world, monkeypatch, capsys):
    import main

    class TclError(Exception):
        pass

    def no_display(argv):
        raise TclError("no display name and no $DISPLAY environment variable")

    seen = _fake_frontends(monkeypatch, no_display)
    assert main.main(["--restart"]) == 0
    assert seen == [("tui", ["--restart"])]
    assert "couldn't open" in capsys.readouterr().out


def test_main_falls_back_to_tui_without_tkinter(world, monkeypatch, capsys):
    import main

    seen = _fake_frontends(monkeypatch)
    monkeypatch.setitem(sys.modules, "gui", None)  # import gui -> ImportError
    assert main.main([]) == 0
    assert seen == [("tui", [])]
    assert "isn't available" in capsys.readouterr().out


def test_main_rejects_unknown_flags(world, monkeypatch):
    import main

    _fake_frontends(monkeypatch)
    with pytest.raises(SystemExit):
        main.main(["--bogus"])
