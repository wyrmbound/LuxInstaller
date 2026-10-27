"""The TUI driving the real CLI flow end to end (fake SD card, simulated console)."""
from __future__ import annotations

import asyncio
import json

import pytest

import flow_harness as fh


@pytest.fixture
def world(tmp_path, monkeypatch):
    w = fh.make_world(tmp_path)
    fh.install(w, monkeypatch.setattr)
    return w


def test_tui_runs_the_whole_setup(world, monkeypatch):
    import cli
    from tui import app as tui_app

    console = fh.Console(world)
    prompts: list[str] = []

    def run_recording(argv, inp, out):
        def recording_inp(prompt):
            prompts.append(prompt)
            return inp(prompt)

        def tee(line):
            console.out(line)  # the simulated console needs to know which stage it's in
            out(line)
        return cli.run(argv, inp=recording_inp, out=tee)

    monkeypatch.setattr(tui_app, "cli_run", run_recording)
    argv = ["--guide-dir", str(fh.GUIDE), "--backup-dir", str(world["backups"])]

    async def drive():
        app = tui_app.AssistantApp(argv)
        async with app.run_test(size=(120, 40)) as pilot:
            import time

            answered = 0
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                await pilot.pause(0.01)
                if app.exit_code is not None:
                    break
                if app.waiting and len(prompts) > answered:
                    answer = console(prompts[answered])
                    answered += 1
                    box = app.query_one("#answer")
                    box.value = answer
                    await pilot.press("enter")
            assert app.exit_code == 0, f"exit {app.exit_code} after {answered} answers; last prompts {prompts[-3:]}"
            log_text = "\n".join(str(line.text) for line in app.query_one("#log").lines)
            await pilot.press("enter")
        return log_text, answered

    log_text, answered = asyncio.run(drive())
    assert "Setup complete!" in log_text
    assert answered >= 15
    s = json.loads((world["app"] / "state.json").read_text(encoding="utf-8"))
    assert "backup" in s["completed"]
    assert any(world["backups"].iterdir())
