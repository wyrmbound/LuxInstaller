"""Setup pages of the GUI on the fake-card world: load, console, drive, CFW and resume."""
from __future__ import annotations

import io
import types
import zipfile

import pytest

import flow_harness as fh
import gui_helpers as gh
from gui.pages_setup import PAGES
from gui.widgets import Page


def stub(page_name: str):
    class Stub(Page):
        name = page_name

        def build(self):
            self.seen = []

        def enter(self, **kw):
            self.seen.append(kw)

    return Stub


STUBS = [stub("downloads"), stub("stage"), stub("done"), stub("walk")]


@pytest.fixture
def world(tmp_path, monkeypatch):
    w = fh.make_world(tmp_path)
    fh.install(w, monkeypatch.setattr)
    return w


@pytest.fixture
def app_factory(world):
    made = []

    def make(*args):
        import time
        import tkinter

        for attempt in range(3):  # Tk occasionally fails to read its own library files on Windows
            try:
                app = gh.make_wizard(world, *args, page_classes=[*PAGES, *STUBS])
                break
            except tkinter.TclError:
                if attempt == 2:
                    raise
                time.sleep(0.5)
        made.append(app)
        return app

    yield make
    for app in made:
        try:
            app.destroy()
        except Exception:
            pass


def on_page(app, name):
    return lambda: app.current is app.pages[name]


def enabled(button) -> bool:
    return str(button.cget("state")) == "normal"


def to_console(app):
    app.show("load")
    gh.pump_until(app, on_page(app, "console"), what="console page")
    return app.pages["console"]


def find_method(app, model, version):
    page = to_console(app) if app.current is not app.pages["console"] else app.pages["console"]
    page.model_var.set(model)
    page.version_var.set(version)
    page.find_btn.invoke()
    gh.pump_until(app, lambda: gh.idle(app) and enabled(page.find_btn), what="method lookup")
    return page


def to_drive(app):
    page = find_method(app, "old2ds", "11.4.0-35u")
    page.next_btn.invoke()
    drive = app.pages["drive"]
    gh.pump_until(app, lambda: drive.tree.get_children() and gh.idle(app), what="drive list")
    return drive


# --- load and console ------------------------------------------------------------------

def test_load_goes_to_console_and_shows_guide_commit(app_factory):
    app = app_factory()
    to_console(app)
    assert "Guide_3DS @ local" in app.guide_label.cget("text")


def test_load_failure_offers_retry(app_factory, monkeypatch):
    import httpx

    from gui.core import Core

    def boom(self):
        raise httpx.ConnectError("no network")

    monkeypatch.setattr(Core, "load_guide", boom)
    app = app_factory()
    app.show("load")
    page = app.pages["load"]
    gh.pump_until(app, lambda: page.retry_btn.winfo_manager() != "", what="retry button")
    assert "Couldn't load the guide" in page.msg.cget("text")


def test_old_2ds_on_11_4_gets_the_near_universal_mset9(app_factory):
    # The guide's own order would pick safecerthax here; the assistant prefers MSET9, which the
    # guide's own compatibility check accepts for every model and region on 11.4+.
    app = app_factory()
    page = find_method(app, "old2ds", "11.4.0-35u")
    assert page.method_label.cget("text") == "Method: Installing boot9strap (MSET9)"
    assert "every model and region" in page.explain.get()
    urls = [w.url for w in page.links.winfo_children()]
    assert "https://3ds.hacks.guide/installing-boot9strap-(mset9-cli)" in urls
    assert enabled(page.next_btn)
    page.version_var.set("11.4.0-37u")  # editing after a lookup needs a new lookup
    assert not enabled(page.next_btn)


def test_bad_version_shows_inline_error(app_factory):
    app = app_factory()
    page = find_method(app, "old2ds", "eleven")
    assert "doesn't look like a system version" in page.status.cget("text")
    assert not enabled(page.next_btn)


def test_cfw_prefix_shows_guide_message_and_stops(app_factory):
    app = app_factory()
    page = find_method(app, "old3ds", "Sys. 11.17.0-50U")
    assert "may already have custom firmware" in page.message.cget("text")
    assert [w.url for w in page.links.winfo_children()] == ["https://3ds.hacks.guide/get-started"]
    assert not enabled(page.next_btn)


def test_model_is_required(app_factory):
    app = app_factory()
    page = to_console(app)
    page.version_var.set("11.4.0-35u")
    page.find_btn.invoke()
    assert "Choose your console model" in page.status.cget("text")


# --- drive -----------------------------------------------------------------------------

def test_drive_needs_the_typed_label(app_factory, world):
    app = app_factory()
    drive = to_drive(app)
    assert drive.tree.item("0", "values")[1] == "3DS"
    assert drive.tree.selection() == ("0",)  # the only drive is preselected
    assert not enabled(drive.next_btn)
    drive.label_var.set("WRONG")
    assert enabled(drive.next_btn)
    drive.next_btn.invoke()
    assert "doesn't match" in drive.status.cget("text") and app.current is drive
    drive.label_var.set("3ds")
    drive.next_btn.invoke()
    gh.pump_until(app, on_page(app, "downloads"), what="downloads page")
    assert app.pages["downloads"].seen[-1] == {"card_issues": []}
    assert app.core.state.drive_label == "3DS"


def test_three_wrong_labels_lock_the_page(app_factory):
    app = app_factory()
    drive = to_drive(app)
    for _ in range(3):
        drive.label_var.set("NOPE")
        drive.next_btn.invoke()
    assert "Nothing was written" in drive.status.cget("text")
    assert not enabled(drive.next_btn) and str(drive.label_entry.cget("state")) == "disabled"


def test_unusable_drive_cannot_be_confirmed(app_factory, monkeypatch):
    import sdcard

    drives = sdcard.list_drives()
    bad = [types.SimpleNamespace(**{**vars(d), "filesystem": "NTFS"}) if False else d for d in drives]
    monkeypatch.setattr(sdcard, "list_drives", lambda: [sdcard.DriveInfo(
        **{**bad[0].__dict__, "filesystem": "exFAT"})])
    app = app_factory()
    drive = to_drive(app)
    drive.label_var.set("3DS")
    assert not enabled(drive.next_btn)
    assert "can't be used" in drive.status.cget("text")


def test_card_issues_are_shown_before_continuing(app_factory, world):
    import shutil

    shutil.rmtree(world["sd"] / "Nintendo 3DS")
    app = app_factory()
    drive = to_drive(app)
    drive.label_var.set("3DS")
    drive.next_btn.invoke()
    gh.pump_until(app, lambda: drive.confirmed_with_issues, what="card issues")
    assert app.current is drive and drive.next_btn.cget("text").startswith("Continue")
    drive.next_btn.invoke()
    assert app.current is app.pages["downloads"]
    assert "Nintendo 3DS" in app.pages["downloads"].seen[-1]["card_issues"][0].message


# --- cards that already have CFW -------------------------------------------------------

def modded(world):
    sd = world["sd"]
    (sd / "luma" / "payloads").mkdir(parents=True)
    (sd / "boot.firm").write_bytes(b"old luma")
    (sd / "luma" / "payloads" / "GodMode9.firm").write_bytes(b"old gm9")


def to_cfw(app):
    drive = to_drive(app)
    drive.label_var.set("3DS")
    drive.next_btn.invoke()
    gh.pump_until(app, on_page(app, "cfw"), what="cfw page")
    return app.pages["cfw"]


def test_modded_card_goes_to_cfw_page(app_factory, world):
    modded(world)
    app = app_factory()
    cfw = to_cfw(app)
    assert "Hold the (Select) button" in cfw.steps.get()


def test_cfw_71_points_to_updating_b9s_without_update_ui(app_factory, world):
    modded(world)
    app = app_factory()
    cfw = to_cfw(app)
    cfw.version_var.set("7.1")
    cfw.check_btn.invoke()
    assert [w.url for w in cfw.link_box.winfo_children()] == ["https://3ds.hacks.guide/updating-b9s"]
    assert cfw.update_frame.winfo_manager() == ""
    cfw.next_btn.invoke()
    assert "updating-b9s" in app.pages["done"].seen[-1]["message"]


def _zip(path, files):
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        for k, v in files.items():
            z.writestr(k, v)
    path.write_bytes(b.getvalue())
    return path


def test_cfw_134_updates_luma_and_gm9_after_typed_write(app_factory, world, monkeypatch):
    import cli

    modded(world)
    luma = _zip(world["dl"] / "Luma3DSv13.4.zip", {"boot.firm": "new luma", "boot.3dsx": "hbl"})
    gm9 = _zip(world["dl"] / "GodMode9-v2.2.zip", {"GodMode9.firm": "new gm9", "gm9/scripts/x.gm9": "s"})
    files = {"Luma3DS": luma, "GodMode9": gm9}
    monkeypatch.setattr(cli, "_fetch_specs", lambda specs: [
        types.SimpleNamespace(name=s.name, path=files[s.name], sha256="", verified="published", from_cache=False)
        for s in specs if s.kind == "github"])

    app = app_factory()
    cfw = to_cfw(app)
    cfw.version_var.set("13.4")
    cfw.check_btn.invoke()
    assert cfw.update_frame.winfo_manager() != "" and cfw.gm9_check.winfo_manager() != ""
    cfw.gm9_var.set(True)
    cfw.prepare_btn.invoke()
    gh.pump_until(app, lambda: cfw.plan_view.rows() and gh.idle(app), what="update plan")
    rows = cfw.plan_view.rows()
    assert ("Overwrite", "/boot.firm", "Luma3DSv13.4.zip") in rows
    assert ("Overwrite", "/luma/payloads/GodMode9.firm", "GodMode9-v2.2.zip") in rows
    assert not enabled(cfw.copy_btn)
    cfw.write_var.set("write")
    assert not enabled(cfw.copy_btn)
    cfw.write_var.set("WRITE")
    assert enabled(cfw.copy_btn)
    assert (world["sd"] / "boot.firm").read_bytes() == b"old luma"  # nothing written before the click
    cfw.copy_btn.invoke()
    gh.pump_until(app, lambda: enabled(cfw.next_btn) and gh.idle(app), what="update written")
    assert (world["sd"] / "boot.firm").read_bytes() == b"new luma"
    assert (world["sd"] / "luma" / "payloads" / "GodMode9.firm").read_bytes() == b"new gm9"
    assert cfw.progress.fraction == 1.0
    cfw.next_btn.invoke()
    assert "Luma3DS and GodMode9 updated" in app.pages["done"].seen[-1]["message"]


# --- resume ----------------------------------------------------------------------------

def saved_progress(app_factory, *args, downloaded=True):
    """Progress saved by a real run: console, drive and (unless downloaded=False) the downloads."""
    import cli

    first = app_factory(*args)
    core = first.core
    core.load_guide()
    core.choose("old2ds", "11.4.0-35u")
    drive, _ = core.drives()[0]
    core.confirm(drive, "3DS")
    if downloaded:
        cli._remember_downloads(core.state, cli.get_downloads(core.snap, core.slug, None, None))
        core.state.save()
    first.destroy()


def test_resume_goes_to_the_first_pending_stage(app_factory):
    saved_progress(app_factory)
    app = app_factory()
    app.show("load")
    gh.pump_until(app, on_page(app, "resume"), what="resume page")
    page = app.pages["resume"]
    text = page.summary.cget("text")
    assert "2DS, Ver. 11.4.0-35U" in text and "0 of 9" in text and "Prep Work" in text
    page.next_btn.invoke()
    # A new session must type the drive label again before the walkthrough writes anything,
    # and the card's CFW files must not send it to the "already modded" page.
    gh.pump_until(app, on_page(app, "drive"), what="drive re-confirm")
    d = app.pages["drive"]
    gh.pump_until(app, lambda: d.tree.get_children(), what="drive list")
    d.tree.selection_set(d.tree.get_children()[0])
    gh.pump(app)
    d.label_var.set("3DS")
    gh.pump_until(app, lambda: str(d.next_btn["state"]) == "normal", what="confirm enabled")
    d.next_btn.invoke()
    gh.pump_until(app, on_page(app, "walk"), what="walk page")


def test_resume_dry_run_jumps_to_the_first_copy(app_factory):
    saved_progress(app_factory)
    app = app_factory("--dry-run")
    app.show("load")
    gh.pump_until(app, on_page(app, "resume"), what="resume page")
    app.pages["resume"].next_btn.invoke()
    gh.pump_until(app, on_page(app, "stage"), what="stage page")
    assert app.pages["stage"].seen[-1] == {"index": 0}  # dry runs use the plan view: MSET9 Prep Work


def test_resume_without_downloads_goes_to_downloads(app_factory):
    saved_progress(app_factory, downloaded=False)  # quit before downloading
    app = app_factory()
    app.show("load")
    gh.pump_until(app, on_page(app, "resume"), what="resume page")
    app.pages["resume"].next_btn.invoke()
    gh.pump_until(app, on_page(app, "downloads"), what="downloads page")


def test_resume_start_over(app_factory):
    saved_progress(app_factory)
    app = app_factory()
    app.show("load")
    gh.pump_until(app, on_page(app, "resume"), what="resume page")
    app.pages["resume"].start_over_btn.invoke()
    assert app.current is app.pages["console"]
    assert app.core.state.method is None
