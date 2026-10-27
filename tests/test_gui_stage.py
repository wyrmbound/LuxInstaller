"""The GUI stage page on the fake SD card, with the console simulated by flow_harness."""
from __future__ import annotations

import json

import pytest

import flow_harness as fh
import gui_helpers as gh
from gui.pages_stage import StagePage
from gui.widgets import Page

MSET9_CLI = "installing-boot9strap-(mset9-cli)"


class Stub(Page):
    def enter(self, **kw):
        self.entered = kw


class BackupStub(Stub):
    name = "backup"


class DoneStub(Stub):
    name = "done"


@pytest.fixture
def world(tmp_path, monkeypatch):
    w = fh.make_world(tmp_path)
    fh.install(w, monkeypatch.setattr)
    return w


def make(world, *args, confirm=True):
    import cli
    import sdcard

    app = gh.make_wizard(world, *args, page_classes=[StagePage, BackupStub, DoneStub])
    core = app.core
    core.load_guide()
    core.choose("old3ds", "Ver. 11.17.0-50E")
    assert core.slug == MSET9_CLI
    if confirm:
        core.confirm(sdcard.list_drives()[0], "3DS")
    core.downloads = cli.get_downloads(core.snap, core.slug, None, None)
    return app


def page(app) -> StagePage:
    return app.pages["stage"]


def packed(widget) -> bool:
    return bool(widget.winfo_manager())


def state(world) -> dict:
    return json.loads((world["app"] / "state.json").read_text(encoding="utf-8"))


def effects(world, stage_line: str) -> None:
    c = fh.Console(world)
    c.stage = stage_line
    c.console_effects()


def test_copy_stage_needs_WRITE_then_copies_and_reads_back(world):
    app = make(world)
    app.show("stage", index=0)
    p = page(app)
    gh.pump_until(app, lambda: p.phase == "plan", what="plan")
    assert "Stage 1 of 9" in str(p.body.winfo_children()[0].cget("text"))
    rows = p.plan_view.rows()
    assert ("New", "/mset9.py", "MSET9-v2.2.zip") in rows and len(rows) == 4
    assert p.copy_btn.instate(["disabled"])
    p.write_var.set("write")
    gh.pump(app)
    assert p.copy_btn.instate(["disabled"])
    p.write_var.set("WRITE")
    gh.pump(app)
    assert not p.copy_btn.instate(["disabled"])
    assert not (world["sd"] / "mset9.py").exists()  # nothing written before the click
    p.copy_btn.invoke()
    gh.pump_until(app, lambda: p.phase == "steps", what="copy")
    sd = world["sd"]
    assert (sd / "mset9.py").is_file() and (sd / "boot9strap" / "boot9strap.firm").is_file()
    assert p.progress.fraction == 1.0
    assert f"{MSET9_CLI}#I:files" in state(world)["completed"]
    assert "done by the assistant" in p.steps_text.get()
    assert not p.next_btn.instate(["disabled"])
    p.next_btn.invoke()  # no SD checks on this stage
    gh.pump_until(app, lambda: p.index == 1, what="next stage")
    assert f"{MSET9_CLI}#I" in state(world)["completed"]
    assert p.phase == "steps" and p.copy_box is None  # stage II is console-only
    app.destroy()


def test_warning_can_be_overridden(world):
    app = make(world)
    (world["sd"] / "boot.firm").write_bytes(b"luma")  # boot.firm present, luma/config.ini missing
    app.show("stage", index=2)
    p = page(app)
    gh.pump(app)
    assert p.stage.key == f"{MSET9_CLI}#III" and p.phase == "steps"
    p.next_btn.invoke()
    gh.pump_until(app, lambda: packed(p.continue_btn), what="warning")
    assert packed(p.check_again_btn) and p.next_btn.instate(["disabled"])
    p.continue_btn.invoke()
    gh.pump_until(app, lambda: p.index == 3, what="advance")
    assert f"{MSET9_CLI}#III" in state(world)["completed"]
    app.destroy()


def test_passing_checks_advance_directly(world):
    app = make(world)
    effects(world, "Stage 3/9: Installing boot9strap (MSET9 CLI) - Section III: Installing boot9strap")
    (world["sd"] / "boot.firm").write_bytes(b"luma")
    app.show("stage", index=2)
    p = page(app)
    gh.pump(app)
    p.next_btn.invoke()
    gh.pump_until(app, lambda: p.index == 3, what="advance")
    app.destroy()


def test_failure_blocks_until_fixed(world):
    app = make(world)
    effects(world, "Stage 1/9: Installing boot9strap (MSET9 CLI) - Section I: Prep Work")  # MSET9 set up
    app.show("stage", index=3)
    p = page(app)
    gh.pump(app)
    assert p.stage.key == f"{MSET9_CLI}#IV"
    p.next_btn.invoke()
    gh.pump_until(app, lambda: packed(p.check_again_btn), what="failure")
    assert not packed(p.continue_btn) and p.next_btn.instate(["disabled"])
    assert "Fix this" in p.status.cget("text")
    p._continue_anyway()  # even calling it directly must not get past a failure
    gh.pump(app)
    assert p.index == 3 and f"{MSET9_CLI}#IV" not in state(world)["completed"]
    p.check_again_btn.invoke()  # still not fixed
    gh.pump_until(app, lambda: app.busy == 0 and packed(p.check_again_btn), what="recheck")
    assert p.index == 3
    effects(world, "Stage 4/9: Installing boot9strap (MSET9 CLI) - Section IV: Removing MSET9")
    p.check_again_btn.invoke()
    gh.pump_until(app, lambda: p.index == 4, what="advance after fix")
    assert f"{MSET9_CLI}#IV" in state(world)["completed"]
    app.destroy()


def test_disconnected_card_asks_to_check_again(world, monkeypatch):
    import sdcard

    app = make(world)
    harness_drives = sdcard.list_drives
    monkeypatch.setattr(sdcard, "list_drives", lambda: [])
    app.show("stage", index=2)
    p = page(app)
    gh.pump(app)
    p.next_btn.invoke()
    gh.pump_until(app, lambda: packed(p.check_again_btn), what="card missing")
    assert "Put the SD card in this computer" in p.status.cget("text")
    monkeypatch.setattr(sdcard, "list_drives", harness_drives)
    effects(world, "Stage 3/9: Installing boot9strap (MSET9 CLI) - Section III: Installing boot9strap")
    (world["sd"] / "boot.firm").write_bytes(b"luma")
    p.check_again_btn.invoke()
    gh.pump_until(app, lambda: p.index == 3, what="advance")
    app.destroy()


def test_copy_stage_without_card_waits(world, monkeypatch):
    import sdcard

    app = make(world)
    harness_drives = sdcard.list_drives
    monkeypatch.setattr(sdcard, "list_drives", lambda: [])
    app.show("stage", index=0)
    p = page(app)
    gh.pump_until(app, lambda: packed(p.check_again_btn), what="card prompt")
    assert "Put the SD card" in p.copy_msg.cget("text")
    monkeypatch.setattr(sdcard, "list_drives", harness_drives)
    p.check_again_btn.invoke()
    gh.pump_until(app, lambda: p.phase == "plan", what="plan")
    app.destroy()


def test_new_session_must_type_the_label_before_finalize_copy(world):
    app = make(world, confirm=False)
    core = app.core
    core.state.drive_label, core.state.drive_serial = "3DS", "1234-ABCD"  # saved by an earlier session
    app.show("stage", index=4)
    p = page(app)
    gh.pump_until(app, lambda: p.phase == "label", what="label prompt")
    assert p.stage.key == "finalizing-setup#I"
    p.label_var.set("SD")
    p.confirm_btn.invoke()
    gh.pump(app)
    assert p.phase == "label" and "doesn't match" in p.status.cget("text")
    p.label_var.set("3ds")
    p.confirm_btn.invoke()
    gh.pump_until(app, lambda: p.phase == "plan", what="plan")
    p.write_var.set("WRITE")
    gh.pump(app)
    p.copy_btn.invoke()
    gh.pump_until(app, lambda: p.phase == "steps", what="copy")
    sd = world["sd"]
    assert (sd / "finalize.romfs").is_file() and (sd / "luma" / "payloads" / "x_finalize_helper.firm").is_file()
    assert "Do NOT delete" not in p.steps_text.get()  # that note belongs to Section IV
    app.destroy()


def test_unmapped_steps_disable_copying(world):
    app = make(world)
    app.core.downloads = []  # nothing to copy from: every copy step is unmapped
    app.show("stage", index=0)
    p = page(app)
    gh.pump_until(app, lambda: p.phase == "plan", what="plan")
    assert p.copy_btn is None and p.next_btn.instate(["disabled"])
    assert "won't write" in p.status.cget("text") or "Nothing will be written" in p.status.cget("text")
    app.destroy()


def test_dry_run_shows_plan_and_changes_nothing(world):
    app = make(world, "--dry-run")
    app.show("stage", index=0)
    p = page(app)
    gh.pump_until(app, lambda: p.phase == "plan", what="plan")
    assert len(p.plan_view.rows()) == 4
    assert p.copy_btn is None and p.write_var is None
    assert p.next_btn.instate(["disabled"]) and "Dry run" in p.status.cget("text")
    p.on_next()
    p._complete()
    gh.pump(app)
    assert state(world)["completed"] == [] and state(world)["stage"] is None
    assert not (world["sd"] / "mset9.py").exists()
    app.destroy()


def test_backup_stage_and_end_redirect(world):
    app = make(world)
    n = len(app.core.stages())
    app.show("stage", index=n - 1)
    assert app.current is app.pages["backup"]
    app.show("stage", index=n)
    assert app.current is app.pages["done"]
    app.destroy()


def test_finalize_section_iv_warns_not_to_delete(world):
    app = make(world)
    app.show("stage", index=7)
    p = page(app)
    gh.pump(app)
    assert p.stage.key == "finalizing-setup#IV"
    text = p.steps_text.get()
    assert "Do NOT delete anything yet" in text and "copy for you" in text
    app.destroy()
