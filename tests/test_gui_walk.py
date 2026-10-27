"""The baby-step walkthrough page with a fake core."""
from __future__ import annotations

import types

import pytest

import flow_harness as fh
import gui_helpers as gh

pytest.importorskip("tkinter")

from walkthrough import ActionResult, Step  # noqa: E402

GUIDE = ("installing-boot9strap-(mset9-cli)", "I")
STEPS = [
    Step("open_mii", "you", "Open Mii Maker", do=("Power on the console", "Tap Mii Maker"),
         expect="The Welcome to Mii Maker screen", if_not="Wait a little longer, then try again.",
         where="console", guide=GUIDE),
    Step("ready", "auto", "Check the card", action="mset9_ready", where="pc", guide=GUIDE),
    Step("pull", "you", "Take the SD card out", do=("With the console ON, remove the SD card",),
         expect="The menu says no SD card is inserted", where="console", guide=GUIDE,
         warning="Don't press any buttons or touch the screen."),
    Step("inject", "auto", "Inject MSET9", action="mset9_inject", where="pc", guide=GUIDE),
]


class Stub:
    def __init__(self, name):
        self.name = name

    def __call__(self, wizard):
        page = types.SimpleNamespace(name=self.name, entered=[], pack=lambda **k: None, pack_forget=lambda: None)
        page.enter = lambda **kw: page.entered.append(kw)
        return page


class FakeCore:
    def __init__(self, done=(), drift=(), fail_first=("mset9_inject",)):
        self.done = set(done)
        self.marks: list[str] = []
        self.calls: list[str] = []
        self.drift = list(drift)
        self.fail_first = set(fail_first)

    def walk_steps(self):
        return STEPS

    def walk_done(self, step_id):
        return step_id in self.done

    def walk_mark(self, step_id):
        self.marks.append(step_id)
        self.done.add(step_id)

    def walk_drift(self):
        return self.drift

    def walk_action(self, name, progress):
        self.calls.append(name)
        progress(1, 2, "halfway")
        if name in self.fail_first:
            self.fail_first.discard(name)
            return ActionResult(False, "MSET9 isn't ready yet.", ["Mii Maker extdata: missing"],
                                "Open Mii Maker on the console, then try again.")
        return ActionResult(True, f"{name} worked.")


@pytest.fixture
def world(tmp_path, monkeypatch):
    w = fh.make_world(tmp_path)
    fh.install(w, monkeypatch.setattr)
    return w


def make(world, fake: FakeCore):
    from gui.pages_walk import PAGES

    app = gh.make_wizard(world, page_classes=[*PAGES, Stub("done")])
    for name in ("walk_steps", "walk_done", "walk_mark", "walk_drift", "walk_action"):
        setattr(app.core, name, getattr(fake, name))
    return app


def page_of(app):
    return app.pages["walk"]


def wait_for(app, step_id):
    gh.pump_until(app, lambda: page_of(app).current_id == step_id, what=f"step {step_id}")


def test_full_walk_with_interstitials_and_retry(world):
    fake = FakeCore()
    app = make(world, fake)
    try:
        app.show("walk")
        p = page_of(app)
        # resume screen: first step wants the card in the console and doesn't mention it itself
        assert p.current_id == "move"
        p.move_btn.invoke()
        assert p.current_id == "open_mii"
        assert p.counter.cget("text") == "Step 1 of 4"
        p.yes_btn.invoke()
        # console -> pc: the move screen comes first
        assert p.current_id == "move" and "this computer" in p.area.winfo_children()[0].cget("text")
        p.move_btn.invoke()
        assert p.current_id == "ready"
        wait_for(app, "pull")  # auto step ran and moved on by itself (pc -> console, but "pull" mentions the card)
        assert fake.calls == ["mset9_ready"]
        p.yes_btn.invoke()
        assert p.current_id == "move"  # console -> pc again
        p.move_btn.invoke()
        gh.pump_until(app, lambda: p.retry_btn is not None, what="failure")
        assert "isn't ready" in p.result_label.cget("text")
        assert "inject" not in fake.marks
        p.retry_btn.invoke()
        gh.pump_until(app, lambda: app.current.name == "done", what="done page")
        assert fake.marks == ["open_mii", "ready", "pull", "inject"]
        assert fake.calls == ["mset9_ready", "mset9_inject", "mset9_inject"]
    finally:
        app.destroy()


def test_no_shows_help_and_guide_link(world):
    app = make(world, FakeCore())
    try:
        app.show("walk")
        p = page_of(app)
        p.move_btn.invoke()
        assert p.help_frame.winfo_manager() == ""
        p.no_btn.invoke()
        gh.pump(app)
        assert p.help_frame.winfo_manager() == "pack"
        assert "Wait a little longer" in p.help_text.cget("text")
        assert p.help_link.url == "https://3ds.hacks.guide/installing-boot9strap-(mset9-cli)"
        p.again_btn.invoke()
        assert p.help_frame.winfo_manager() == ""
        assert p.current_id == "open_mii"
    finally:
        app.destroy()


def test_resume_starts_at_first_pending_step(world):
    fake = FakeCore(done={"open_mii", "ready"})
    app = make(world, fake)
    try:
        app.show("walk")
        p = page_of(app)
        assert p.current_id == "pull"  # mentions the SD card itself: no move screen first
        assert p.counter.cget("text") == "Step 3 of 4"
    finally:
        app.destroy()


def test_resume_at_a_pc_step_asks_for_the_card_first(world):
    fake = FakeCore(done={"open_mii", "ready", "pull"}, fail_first=())
    app = make(world, fake)
    try:
        app.show("walk")
        p = page_of(app)
        assert p.current_id == "move"
        assert fake.calls == []  # nothing runs until the card is in place
        p.move_btn.invoke()
        gh.pump_until(app, lambda: app.current.name == "done", what="done")
        assert fake.calls == ["mset9_inject"]
    finally:
        app.destroy()


def test_back_to_a_finished_auto_step_does_not_rerun_it(world):
    fake = FakeCore(done={"open_mii", "ready"})
    app = make(world, fake)
    try:
        app.show("walk")
        p = page_of(app)
        p.back_btn.invoke()
        assert p.current_id == "ready"
        gh.pump(app, 0.1)
        assert fake.calls == []
        p.next_done_btn.invoke()
        assert p.current_id == "pull"
    finally:
        app.destroy()


def test_all_done_goes_to_done_page(world):
    app = make(world, FakeCore(done={s.id for s in STEPS}))
    try:
        app.show("walk")
        assert app.current.name == "done"
    finally:
        app.destroy()


def test_drift_banner(world):
    app = make(world, FakeCore(drift=["installing-boot9strap-(mset9-cli) Section I"]))
    try:
        app.show("walk")
        assert page_of(app).banner.winfo_manager() == "pack"
    finally:
        app.destroy()
    app = make(world, FakeCore())
    try:
        app.show("walk")
        assert page_of(app).banner.winfo_manager() == ""
    finally:
        app.destroy()


def test_enter_key_presses_the_primary_button(world):
    fake = FakeCore()
    app = make(world, fake)
    try:
        app.show("walk")
        p = page_of(app)
        p._on_return()
        assert p.current_id == "open_mii"
        p._on_return()
        assert fake.marks == ["open_mii"]
    finally:
        app.destroy()
