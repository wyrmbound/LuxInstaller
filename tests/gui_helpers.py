"""Helpers for driving the Tk wizard in tests: a hidden window, real Core, fake SD card world."""
from __future__ import annotations

import time

import flow_harness as fh


def make_wizard(world: dict, *extra_args: str, page_classes=None):
    """A withdrawn Wizard on the flow harness world (fh.install must already have run)."""
    import cli
    from gui.app import create_wizard
    from gui.core import Core

    args = cli.parse_args(["--guide-dir", str(fh.GUIDE), "--backup-dir", str(world["backups"]), *extra_args])
    app = create_wizard(Core(args), page_classes)
    app.withdraw()
    app.quiet = True  # errors go to app.last_error instead of a modal dialog
    return app


def pump(app, seconds: float = 0.05) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.update()
        time.sleep(0.005)


def pump_until(app, cond, timeout: float = 20.0, what: str = "condition") -> None:
    """Process Tk events until cond() is true (background work finishes through the queue)."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        app.update()
        if cond():
            return
        time.sleep(0.005)
    raise AssertionError(f"timed out waiting for {what}; last error: {getattr(app, 'last_error', None)}")


def idle(app) -> bool:
    return app.busy == 0
