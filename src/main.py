"""Entry point: `luxinstaller [--tui | --no-tui] [--guide-dir DIR] [--dry-run] [--backup-dir DIR] [--restart]`.

No flag opens the desktop GUI, `--tui` the terminal UI, `--no-tui` the plain command line.
"""
from __future__ import annotations

import sys

import cli


def _run_tui(argv: list[str]) -> int:
    from tui import run_tui

    return run_tui(argv)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--no-tui" in argv:
        return cli.run(argv)
    if "--tui" in argv:
        return _run_tui([a for a in argv if a != "--tui"])
    cli.parse_args(argv)  # reject unknown flags with the usual usage message before opening a window
    try:
        from gui import run_gui

        return run_gui(argv)
    except ImportError as e:
        print(f"The desktop window isn't available ({e}); starting the terminal interface instead.")
    except Exception as e:
        if type(e).__name__ != "TclError":
            raise
        print(f"The desktop window couldn't open ({e}); starting the terminal interface instead.")
    return _run_tui(argv)


if __name__ == "__main__":
    sys.exit(main())
