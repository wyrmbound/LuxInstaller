"""PyInstaller entry for the windowed exe (LuxInstaller): opens the desktop GUI only."""
import sys

from gui import run_gui

sys.exit(run_gui(sys.argv[1:]))
