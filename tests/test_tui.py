from __future__ import annotations

import asyncio
import threading
from pathlib import Path

import pytest

from textual.widgets import Input, Label, RichLog

import tui.app as tui_app
from tui.app import AssistantApp, style_line

SCREENSHOT = Path(__file__).resolve().parents[1] / "docs" / "screenshots" / "tui-prompt.svg"


class FakeFlow:
    """Stands in for cli.run: prints, asks two questions, prints the answers."""

    def __init__(self):
        self.answers = []
        self.argv = None
        self.eof = threading.Event()

    def __call__(self, argv, inp, out):
        self.argv = argv
        out("Guide: hacks-guide/Guide_3DS @ 63013be")
        out("Stage 3/9: Installing boot9strap (MSET9 CLI) - Section III: Installing boot9strap")
        out("  [warn] luma/config.ini isn't on the card yet.")
        try:
            self.answers.append(inp("Which console do you have? Number: "))
            self.answers.append(inp("System version (e.g. Ver. 11.17.0-50U): "))
        except EOFError:
            self.eof.set()
            return 130
        if "q" in self.answers:
            out("Progress saved. Run the assistant again to continue from this stage.")
            return 0
        out(f"You chose {self.answers[0]} on {self.answers[1]}")
        return 0


def log_text(app) -> str:
    return "\n".join(line.text for line in app.query_one("#log", RichLog).lines)


async def wait_for(pilot, cond, timeout=3.0):
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while not cond():
        if loop.time() > end:
            raise AssertionError("timed out waiting for the TUI")
        await pilot.pause(0.02)


def prompt_text(app) -> str:
    return str(app.query_one("#prompt", Label).render())


@pytest.fixture
def flow(monkeypatch):
    f = FakeFlow()
    monkeypatch.setattr(tui_app, "cli_run", f)
    return f


async def answer(pilot, app, text):
    await wait_for(pilot, lambda: app.waiting)
    app.query_one("#answer", Input).value = text
    await pilot.press("enter")


def test_answers_reach_the_flow_and_output_is_shown(flow):
    async def go():
        app = AssistantApp(["--guide-dir", "x", "--dry-run"])
        async with app.run_test(size=(100, 30)) as pilot:
            await wait_for(pilot, lambda: app.waiting)
            assert "Which console" in prompt_text(app)
            assert "Stage 3/9" in log_text(app)
            SCREENSHOT.parent.mkdir(parents=True, exist_ok=True)
            app.save_screenshot(filename=SCREENSHOT.name, path=str(SCREENSHOT.parent))
            await answer(pilot, app, "1")
            await wait_for(pilot, lambda: "System version" in prompt_text(app))
            await answer(pilot, app, "Ver. 11.17.0-50E")
            await wait_for(pilot, lambda: app.exit_code is not None)
            assert "You chose 1 on Ver. 11.17.0-50E" in log_text(app)
            assert "Exit code 0" in prompt_text(app)
            await pilot.press("enter")
        return app

    app = asyncio.run(go())
    assert flow.answers == ["1", "Ver. 11.17.0-50E"]
    assert flow.argv == ["--guide-dir", "x", "--dry-run"]
    assert app.return_code == 0 or app.return_value == 0
    assert SCREENSHOT.is_file() and SCREENSHOT.stat().st_size > 1000


def test_ctrl_q_at_a_prompt_sends_q(flow):
    async def go():
        app = AssistantApp([])
        async with app.run_test() as pilot:
            await wait_for(pilot, lambda: app.waiting)
            await pilot.press("ctrl+q")
            await wait_for(pilot, lambda: app.exit_code is not None or not app.is_running)
        return app

    asyncio.run(go())
    assert flow.answers[0] == "q"


def test_closing_mid_prompt_raises_eof_without_hanging(flow):
    async def go():
        app = AssistantApp([])
        async with app.run_test() as pilot:
            await wait_for(pilot, lambda: app.waiting)
            app.exit(130)
            await pilot.pause(0.05)

    asyncio.run(go())
    assert flow.eof.wait(3), "the flow thread was left blocked on its prompt"


def test_unexpected_error_is_shown(monkeypatch):
    def boom(argv, inp, out):
        out("starting")
        raise RuntimeError("kaput")

    monkeypatch.setattr(tui_app, "cli_run", boom)

    async def go():
        app = AssistantApp([])
        async with app.run_test() as pilot:
            await wait_for(pilot, lambda: app.exit_code is not None)
            assert "kaput" in log_text(app) and app.exit_code == 2
            await pilot.press("escape")

    asyncio.run(go())


@pytest.mark.parametrize("line, style", [
    ("Stage 1/9: Finalizing Setup", "bold cyan"),
    ("  [fail] boot.firm is missing", "bold red"),
    ("!!! NAND BACKUP INCOMPLETE !!!", "bold red"),
    ("  [warn] luma/config.ini", "yellow"),
    ("plain text", ""),
])
def test_style_line(line, style):
    assert str(style_line(line).style) == style
