"""Windowed GUI (Tkinter). `run_gui(argv)` opens the wizard; see gui.app for the page flow."""
from __future__ import annotations


def run_gui(argv: list[str] | None = None) -> int:
    from gui.app import run_gui as _run

    return _run(argv)
