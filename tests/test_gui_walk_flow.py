"""The whole baby-step walkthrough on a fake SD card, running the REAL MSET9 script.

Old 2DS on 11.17.0-50U, from the console page to the backups, clicking through like a person.
Between steps, the test does what the console would do to the card.
"""
from __future__ import annotations

import hashlib
import io
import json
import shutil
import types
import zipfile
from pathlib import Path

import pytest

import flow_harness as fh
import gui_helpers as gh
from test_gui_files import fake_plan
from test_mset9_runner import ZIP, no_root_moves

pytestmark = pytest.mark.skipif(not ZIP.is_file(), reason="MSET9 zip not in the download cache")
pytest.importorskip("tkinter")

DB_SIZE = 0x31E400


@pytest.fixture
def world(tmp_path, monkeypatch):
    import mset9_runner

    w = fh.make_world(tmp_path)
    fh.install(w, monkeypatch.setattr)
    shutil.copyfile(ZIP, w["dl"] / "MSET9-v2.2.zip")  # the real MSET9 release
    real_run = mset9_runner.run
    # Test-only: the fake card is a temp folder, not a drive root.
    monkeypatch.setattr(mset9_runner, "_is_mount", lambda p: True)
    monkeypatch.setattr(mset9_runner, "run",
                        lambda root, idx, action, **kw: real_run(root, idx, action, _patch=no_root_moves))
    return w


def use_downloads(app, world, monkeypatch):
    def download(plan, manual, progress=None):
        from cli import _remember_downloads

        got = []
        for r in plan.resolved:
            p = world["dl"] / r.asset_name
            got.append(types.SimpleNamespace(name=r.spec.name, path=p, sha256=hashlib.sha256(p.read_bytes()).hexdigest(),
                                             verified="published", from_cache=False))
        app.core.downloads = got
        _remember_downloads(app.core.state, got)
        app.core.state.save()
        if app.core.luma_choice.custom:
            app.core.fetch_luma_build()
        return got

    import downloader

    # Patch underneath Core.plan_downloads so its walkthrough filtering (no Python) really runs.
    monkeypatch.setattr(downloader, "plan_downloads", lambda snap, slugs, client: fake_plan(world, with_python=True))
    monkeypatch.setattr(app.core, "download", download)


def console(world, step_id: str) -> None:
    """What the console does to the SD card during each "you" step."""
    sd = world["sd"]
    id0 = sd / "Nintendo 3DS" / fh.ID0
    if step_id == "reset-software":  # power on + Mii Maker + title database reset
        hacked = next(p for p in id0.iterdir() if "sdmc" in p.name)
        for d in ("0000008F", "00000217"):
            (hacked / "extdata" / "00000000" / d).mkdir(parents=True, exist_ok=True)
        for db in ("title.db", "import.db"):
            with open(hacked / "dbs" / db, "wb") as f:
                f.truncate(DB_SIZE)
    elif step_id == "save-luma":
        (sd / "luma").mkdir(exist_ok=True)
        (sd / "luma" / "config.ini").write_text("[boot]\n")
    elif step_id == "run-finalize":
        b = sd / "gm9" / "backups"
        b.mkdir(parents=True, exist_ok=True)
        nand = b"N" * 4096
        (b / "20261027_0123456789_sysnand_00.bin").write_bytes(nand)
        (b / "20261027_0123456789_sysnand_00.bin.sha").write_bytes(hashlib.sha256(nand).digest())
        (b / "essential.exefs").write_bytes(b"E" * 512)


def start(app, world, luma=None) -> None:
    wait = lambda cond, what: gh.pump_until(app, cond, timeout=60, what=what)  # noqa: E731
    app.show("load")
    wait(lambda: app.current.name == "console", "console page")
    c = app.pages["console"]
    c.model_var.set("old2ds")
    c.version_var.set("11.17.0-50U")
    c.find_btn.invoke()
    wait(lambda: str(c.next_btn["state"]) == "normal", "method")
    assert c.method_label.cget("text") == "Method: Installing boot9strap (MSET9)"
    c.next_btn.invoke()
    d = app.pages["drive"]
    wait(lambda: d.tree.get_children(), "drives")
    d.tree.selection_set(d.tree.get_children()[0])
    gh.pump(app)
    d.label_var.set("3DS")
    wait(lambda: str(d.next_btn["state"]) == "normal", "confirm")
    d.next_btn.invoke()
    wait(lambda: app.current.name == "downloads", "downloads")
    dl = app.pages["downloads"]
    wait(lambda: dl.plan is not None, "plan")
    assert dl.prereq_vars == {}  # no Python needed: the assistant runs the MSET9 script
    if luma:
        dl.set_luma_file(str(luma))
    dl.download_btn.invoke()
    wait(lambda: app.current.name == "walk", "walkthrough starts by itself")


def walk(app, world, stop_after: str | None = None) -> list[str]:
    """Answer every step; returns the step ids seen. Fails on any failed auto step."""
    kinds = {s.id: s.kind for s in app.core.walk_steps()}
    w = app.pages["walk"]
    seen = []
    while app.current.name == "walk":
        gh.pump_until(app, lambda: w.current_id is not None and (
            w.current_id == "move" or kinds.get(w.current_id) == "you" or w.retry_btn is not None
            or app.current.name != "walk" or w.current_id not in seen), timeout=60, what="next screen")
        if app.current.name != "walk":
            break
        sid = w.current_id
        if w.retry_btn is not None and kinds.get(sid) == "auto":
            raise AssertionError(f"auto step {sid} failed: {w.result_label.cget('text')}")
        if sid == "move":
            w.move_btn.invoke()
            continue
        if kinds[sid] == "auto":
            seen.append(sid)
            gh.pump_until(app, lambda: app.current.name != "walk" or w.current_id != sid or w.retry_btn is not None,
                          timeout=60, what=f"auto {sid}")
            if w.retry_btn is not None and w.current_id == sid:
                raise AssertionError(f"auto step {sid} failed: {w.result_label.cget('text')}")
            continue
        seen.append(sid)
        console(world, sid)
        w.yes_btn.invoke()
        if stop_after == sid:
            break
    return seen


def test_walkthrough_official(world, monkeypatch):
    app = gh.make_wizard(world)
    use_downloads(app, world, monkeypatch)
    try:
        start(app, world)
        seen = walk(app, world)
        assert app.current.name == "done", app.current.name
        sd = world["sd"]
        id0 = sd / "Nintendo 3DS" / fh.ID0
        assert sorted(p.name for p in id0.iterdir()) == [fh.ID1]  # MSET9 removed, real ID1 back
        assert (sd / "luma" / "payloads" / "x_finalize_helper.firm").is_file()
        assert "mset9-create" in seen and "mset9-inject" in seen and "backup" in seen
        state = json.loads((world["app"] / "state.json").read_text(encoding="utf-8"))
        assert state["walk"] and state["backup_dir"]
        assert len(list(Path(state["backup_dir"]).iterdir())) == 4
    finally:
        app.destroy()


def test_check_step_finishes_a_half_done_removal(world, monkeypatch):
    """A card left by the old windowed exe: hacked ID1 gone, real ID1 still `<ID1>_user-id1`.
    The check step's Try again must put it back instead of just failing again."""
    app = gh.make_wizard(world)
    use_downloads(app, world, monkeypatch)
    try:
        start(app, world)
        walk(app, world)
        id0 = world["sd"] / "Nintendo 3DS" / fh.ID0
        (id0 / fh.ID1).rename(id0 / f"{fh.ID1}_user-id1")
        r = app.core.walk_action("check_mset9_removed")
        assert r.ok, r.message
        assert sorted(p.name for p in id0.iterdir()) == [fh.ID1]
    finally:
        app.destroy()


def test_walkthrough_with_a_fork_then_restore(world, monkeypatch, tmp_path):
    fork = tmp_path / "LuxiOS-test.zip"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("boot.firm", b"FIRM" + b"luxios" * 100)
        z.writestr("TESTING.md", "test build")
    fork.write_bytes(buf.getvalue())

    app = gh.make_wizard(world)
    use_downloads(app, world, monkeypatch)
    try:
        start(app, world, luma=fork)
        seen = walk(app, world)
        assert seen[-1] == "try-fork" and "install-fork" in seen
        sd = world["sd"]
        assert (sd / "boot.firm").read_bytes().startswith(b"FIRMluxios")
        assert not (sd / "TESTING.md").exists()  # only boot.firm is switched
        done = app.pages["done"]
        assert "LuxiOS-test.zip" in done.text.cget("text")
        done.restore_btn.invoke()
        gh.pump_until(app, lambda: "back on the SD card" in done.restore_msg.cget("text"), what="restore")
        with zipfile.ZipFile(ZIP) as z:
            assert (sd / "boot.firm").read_bytes() == z.read("boot.firm")  # the official one again
    finally:
        app.destroy()


def test_not_a_firm_is_refused(world, monkeypatch, tmp_path):
    import luma_build

    bad = tmp_path / "boot.firm"
    bad.write_bytes(b"MZ not a firm")
    with pytest.raises(luma_build.LumaBuildError, match="FIRM"):
        luma_build.resolve(luma_build.LumaChoice("file", path=str(bad)), None, tmp_path / "cache")
