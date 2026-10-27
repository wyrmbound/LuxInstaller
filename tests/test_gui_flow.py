"""The whole windowed GUI, page by page, on the fake SD card with a simulated console."""
from __future__ import annotations

import json

import pytest

import flow_harness as fh
import gui_helpers as gh
from test_gui_files import fake_download, fake_plan

pytest.importorskip("tkinter")


def use_fake_downloads(app, world, monkeypatch) -> None:
    """Like Core.download, the fake must keep what it fetched on the core."""
    fetch = fake_download(world)

    def download(plan, manual, progress=None):
        from cli import _remember_downloads

        app.core.downloads = fetch(plan, manual, progress)
        _remember_downloads(app.core.state, app.core.downloads)
        app.core.state.save()
        return app.core.downloads

    monkeypatch.setattr(app.core, "plan_downloads", lambda: fake_plan(world))
    monkeypatch.setattr(app.core, "download", download)


def enabled(btn) -> bool:
    return btn is not None and str(btn["state"]) == "normal"


@pytest.fixture
def world(tmp_path, monkeypatch):
    import gui.core

    w = fh.make_world(tmp_path)
    fh.install(w, monkeypatch.setattr)
    # These tests cover the classic stage pages (used by the rare non-MSET9 routes).
    monkeypatch.setattr(gui.core, "WALK_METHOD", "no-walkthrough")
    return w


def click_through(app, world, console, stop_before_index: int | None = None) -> None:
    """Act like a user from the Load page to the Done page (or until a given stage)."""
    from gui.core import stage_title

    wait = lambda cond, what: gh.pump_until(app, cond, timeout=30, what=what)  # noqa: E731

    wait(lambda: app.current.name in ("console", "resume"), "first page")
    if app.current.name == "resume":
        app.pages["resume"].next_btn.invoke()
        wait(lambda: app.current.name != "resume", "resume")
    else:
        c = app.pages["console"]
        c.model_var.set("old3ds")
        c.version_var.set(fh.VERSION)
        c.find_btn.invoke()
        wait(lambda: enabled(c.next_btn), "method")
        c.next_btn.invoke()

        d = app.pages["drive"]
        wait(lambda: d.tree.get_children(), "drive list")
        d.tree.selection_set(d.tree.get_children()[0])
        gh.pump(app)
        d.label_var.set("3DS")
        wait(lambda: enabled(d.next_btn), "drive confirm")
        d.next_btn.invoke()

        wait(lambda: app.current.name == "downloads", "downloads page")
        dl = app.pages["downloads"]
        wait(lambda: dl.plan is not None, "download plan")
        for var in dl.prereq_vars.values():
            var.set(True)
        dl.refresh_buttons()
        dl.download_btn.invoke()
        wait(lambda: enabled(dl.next_btn), "downloads done")
        dl.next_btn.invoke()

    stages = app.core.stages()
    while app.current.name == "stage":
        s = app.pages["stage"]
        index = s.index
        if stop_before_index is not None and index == stop_before_index:
            return
        if s.stage.stages_files and not app.core.files_done(s.stage):
            wait(lambda: s.write_var is not None or s.confirm_btn is not None, f"copy panel for stage {index + 1}")
            if s.write_var is None:  # a new session must type the label again before writing
                console.label_prompts = getattr(console, "label_prompts", 0) + 1
                s.label_var.set("3DS")
                s.confirm_btn.invoke()
            wait(lambda: s.write_var is not None, f"copy plan for stage {index + 1}")
            s.write_var.set("WRITE")
            s.copy_btn.invoke()
        wait(lambda: enabled(s.next_btn), f"stage {index + 1} steps")
        console.stage = "Stage: " + stage_title(stages[index])
        console.console_effects()  # what the console (and MSET9 script) would do to the card
        s.next_btn.invoke()
        wait(lambda: app.current.name != "stage" or s.index != index, f"stage {index + 1} checks")

    b = app.pages["backup"]
    assert app.current.name == "backup"
    b.copy_btn.invoke()
    wait(lambda: enabled(b.next_btn), "backup")
    b.next_btn.invoke()
    assert app.current.name == "done"


def test_gui_full_setup(world, monkeypatch):
    app = gh.make_wizard(world)
    use_fake_downloads(app, world, monkeypatch)
    try:
        app.show("load")
        click_through(app, world, fh.Console(world))
        sd = world["sd"]
        assert (sd / "mset9.py").is_file() and (sd / "luma" / "payloads" / "x_finalize_helper.firm").is_file()
        state = json.loads((world["app"] / "state.json").read_text(encoding="utf-8"))
        assert "backup" in state["completed"] and state["backup_dir"]
        assert "Setup complete!" in app.pages["done"].head.cget("text")
    finally:
        app.destroy()


def test_quit_before_downloading_resumes_at_downloads(world, monkeypatch):
    """Progress saved after the drive step but before any download: resume must download first."""
    first = gh.make_wizard(world)
    try:
        first.core.load_guide()
        first.core.choose("old3ds", fh.VERSION)
        first.core.confirm(first.core.drives()[0][0], "3DS")
    finally:
        first.destroy()
    second = gh.make_wizard(world)
    use_fake_downloads(second, world, monkeypatch)
    try:
        second.show("load")
        gh.pump_until(second, lambda: second.current.name == "resume", what="resume page")
        second.pages["resume"].next_btn.invoke()
        gh.pump_until(second, lambda: second.current.name != "resume", what="after resume")
        assert second.current.name == "downloads"
    finally:
        second.destroy()


def test_gui_quit_and_resume(world, monkeypatch):
    first = gh.make_wizard(world)
    use_fake_downloads(first, world, monkeypatch)
    console = fh.Console(world)
    try:
        first.show("load")
        click_through(first, world, console, stop_before_index=3)  # stop at MSET9 Section IV
    finally:
        first.destroy()  # closing the window mid-setup

    second = gh.make_wizard(world)
    try:
        second.show("load")
        gh.pump_until(second, lambda: second.current.name == "resume", what="resume page")
        assert "Section IV" in "\n".join(w.cget("text") for w in second.pages["resume"].body.winfo_children()
                                         if hasattr(w, "cget") and "text" in w.keys())
        click_through(second, world, console)
        assert getattr(console, "label_prompts", 0) == 1  # writing in a new session needed the label again
        assert "backup" in json.loads((world["app"] / "state.json").read_text(encoding="utf-8"))["completed"]
    finally:
        second.destroy()
