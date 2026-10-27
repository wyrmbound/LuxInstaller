"""Downloads, Backup and Done pages on the fake SD card."""
from __future__ import annotations

import hashlib
import types

import pytest

import flow_harness as fh
import gui_helpers as gh

pytest.importorskip("tkinter")


class Stub:
    """Records where a page navigated to."""

    def __init__(self, name):
        self.name = name

    def __call__(self, wizard):  # acts as a page class
        page = types.SimpleNamespace(name=self.name, entered=[], pack=lambda **k: None, pack_forget=lambda: None)
        page.enter = lambda **kw: page.entered.append(kw)
        return page


@pytest.fixture
def world(tmp_path, monkeypatch):
    w = fh.make_world(tmp_path)
    fh.install(w, monkeypatch.setattr)
    return w


@pytest.fixture
def app(world):
    from gui.pages_files import PAGES

    a = gh.make_wizard(world, page_classes=[*PAGES, Stub("stage"), Stub("walk")])
    a.core.load_guide()
    a.core.choose("old3ds", fh.VERSION)
    a.core.state.walk = False  # these tests cover the classic stage-page path
    drive = a.core.drives()[0][0]
    a.core.confirm(drive, "3DS")
    yield a
    a.destroy()


def fake_plan(world, with_python=True, manual=False):
    import downloader

    def resolved(name, file, tag, digest):
        p = world["dl"] / file
        spec = downloader.DownloadSpec(name, "p", "", f"https://github.com/x/{name}", "github", "x", name, None, file)
        return downloader.ResolvedAsset(spec, tag, file, spec.url, p.stat().st_size,
                                        hashlib.sha256(p.read_bytes()).hexdigest() if digest else None, "2026")

    plan = downloader.DownloadPlan(
        [resolved("MSET9", "MSET9-v2.2.zip", "v2.2", True),
         resolved("x_finalize_helper.firm", "x_finalize_helper.firm", "v1.10.4", True),
         resolved("finalize.romfs", "finalize.romfs", "v1.10.4", False)], [], [])
    if with_python:
        plan.prerequisites.append(downloader.DownloadSpec(
            "Python", "p", "Any 3.x version of Python **installed on your computer**",
            "https://www.python.org/downloads/", "prerequisite"))
    if manual:
        plan.manual.append(downloader.DownloadSpec("Soundhax", "p", "The Soundhax file for your region",
                                                   "http://soundhax.com", "manual"))
    return plan


def fake_download(world):
    def download(plan, manual, progress=None):
        got = []
        for r in plan.resolved:
            progress and progress(r.spec.name, r.size // 2, r.size)
            progress and progress(r.spec.name, r.size, r.size)
            p = world["dl"] / r.asset_name
            got.append(types.SimpleNamespace(name=r.spec.name, path=p, sha256=hashlib.sha256(p.read_bytes()).hexdigest(),
                                             verified="published" if r.digest else "first-use", from_cache=False))
        for name, path in manual.items():
            got.append(types.SimpleNamespace(name=name, path=path, sha256="", verified="user-provided", from_cache=False))
        return got
    return download


def test_downloads_need_prerequisite_ticked_then_download_with_progress(app, world, monkeypatch):
    monkeypatch.setattr(app.core, "plan_downloads", lambda: fake_plan(world))
    monkeypatch.setattr(app.core, "download", fake_download(world))
    app.show("downloads")
    page = app.pages["downloads"]
    gh.pump_until(app, lambda: page.plan is not None, what="plan")
    rows = [page.tree.item(i, "values") for i in page.tree.get_children()]
    assert [r[0] for r in rows] == ["MSET9", "x_finalize_helper.firm", "finalize.romfs"]
    assert rows[0][4] == "published SHA-256" and rows[2][4].startswith("none published")
    assert str(page.download_btn["state"]) == "disabled"  # Python not ticked yet
    page.prereq_vars["Python"].set(True)
    page.refresh_buttons()
    assert str(page.download_btn["state"]) == "normal"
    page.download_btn.invoke()
    gh.pump_until(app, lambda: str(page.next_btn["state"]) == "normal", what="download")
    assert page.overall.fraction == 1.0
    assert page.tree.set("MSET9", "state").startswith("✔")
    page.next_btn.invoke()
    assert app.pages["stage"].entered[-1] == {"index": 0}


def test_walk_mode_skips_python_and_moves_on_by_itself(app, world, monkeypatch):
    app.core.state.walk = True
    monkeypatch.setattr(app.core, "download", fake_download(world))
    import downloader

    real_plan = downloader.plan_downloads
    monkeypatch.setattr(downloader, "plan_downloads", lambda snap, slugs, client: fake_plan(world))
    app.show("downloads")
    page = app.pages["downloads"]
    gh.pump_until(app, lambda: page.plan is not None)
    assert page.prereq_vars == {}  # the assistant runs the MSET9 script itself: no Python needed
    page.download_btn.invoke()
    gh.pump_until(app, lambda: app.pages["walk"].entered, what="walk page")
    assert real_plan is not None


def test_fork_choice_is_saved_and_validated(app, world, monkeypatch, tmp_path):
    monkeypatch.setattr(app.core, "plan_downloads", lambda: fake_plan(world, with_python=False))
    fetched = []
    monkeypatch.setattr(app.core, "download", lambda plan, manual, progress=None: fetched.append(app.core.luma_choice) or [])
    app.show("downloads")
    page = app.pages["downloads"]
    gh.pump_until(app, lambda: page.plan is not None)
    page.luma_kind.set("github")
    page.luma_repo.set("not a repo")
    page.download_btn.invoke()
    assert "owner/name" in page.status.cget("text") and not fetched
    page.luma_repo.set("https://github.com/wyrmbound/LuxiOS")
    page.download_btn.invoke()
    gh.pump_until(app, lambda: fetched, what="download")
    assert fetched[0].kind == "github" and fetched[0].repo == "wyrmbound/LuxiOS"
    assert app.core.state.luma["repo"] == "wyrmbound/LuxiOS"


def test_manual_file_must_be_chosen(app, world, monkeypatch, tmp_path):
    monkeypatch.setattr(app.core, "plan_downloads", lambda: fake_plan(world, with_python=False, manual=True))
    monkeypatch.setattr(app.core, "download", fake_download(world))
    app.show("downloads")
    page = app.pages["downloads"]
    gh.pump_until(app, lambda: page.plan is not None)
    assert str(page.download_btn["state"]) == "disabled"
    f = tmp_path / "soundhax-usa.m4a"
    f.write_bytes(b"m4a")
    page.set_manual_file("Soundhax", str(f))
    assert str(page.download_btn["state"]) == "normal"


def test_checksum_failure_stops_with_message(app, world, monkeypatch):
    import downloader

    def boom(plan, manual, progress=None):
        raise downloader.ChecksumMismatch("MSET9: SHA-256 does not match the published one", "https://github.com/x")

    monkeypatch.setattr(app.core, "plan_downloads", lambda: fake_plan(world, with_python=False))
    monkeypatch.setattr(app.core, "download", boom)
    app.show("downloads")
    page = app.pages["downloads"]
    gh.pump_until(app, lambda: page.plan is not None)
    page.download_btn.invoke()
    gh.pump_until(app, lambda: "Stopped" in page.status.cget("text"))
    assert "Nothing was written to the SD card" in app.last_error
    assert str(page.next_btn["state"]) == "disabled"


def make_backups(world, nand=b"N" * 4096):
    b = world["sd"] / "gm9" / "backups"
    b.mkdir(parents=True, exist_ok=True)
    (b / "20261027_0123_sysnand_00.bin").write_bytes(nand)
    (b / "20261027_0123_sysnand_00.bin.sha").write_bytes(hashlib.sha256(nand).digest())
    (b / "essential.exefs").write_bytes(b"E" * 512)


def test_backup_copies_with_progress_and_finishes(app, world):
    make_backups(world)
    app.show("backup")
    page = app.pages["backup"]
    page.copy_btn.invoke()
    gh.pump_until(app, lambda: page.dest is not None, what="backup")
    assert page.progress.fraction == 1.0
    assert sorted(p.name for p in page.dest.iterdir()) == [
        "20261027_0123_sysnand_00.bin", "20261027_0123_sysnand_00.bin.sha", "MANIFEST.json", "essential.exefs"]
    page.next_btn.invoke()
    assert "backup" in app.core.state.completed
    assert app.current.name == "done"
    assert str(page.dest) in app.pages["done"].text.cget("text")


def test_missing_nand_backup_is_loud_and_blocks(app, world):
    app.show("backup")
    page = app.pages["backup"]
    page.copy_btn.invoke()
    gh.pump_until(app, lambda: page.alarm.winfo_manager() == "pack", what="alarm")
    assert "NAND BACKUP INCOMPLETE" in page.alarm.cget("text")
    assert str(page.next_btn["state"]) == "disabled"
    assert "backup" not in app.core.state.completed


def test_damaged_nand_backup_is_refused(app, world):
    make_backups(world)
    (world["sd"] / "gm9" / "backups" / "20261027_0123_sysnand_00.bin").write_bytes(b"X" * 4096)
    app.show("backup")
    page = app.pages["backup"]
    page.copy_btn.invoke()
    gh.pump_until(app, lambda: page.alarm.winfo_manager() == "pack", what="alarm")
    assert "does not match" in page.alarm.cget("text")
    assert str(page.next_btn["state"]) == "disabled"
