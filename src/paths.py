"""Locations the assistant reads and writes on the PC."""
from __future__ import annotations

from pathlib import Path

APP_DIR = Path.home() / ".3ds-assistant"
CACHE_DIR = APP_DIR / "cache"
GUIDE_DIR = APP_DIR / "guide"
SNAPSHOT_PATH = APP_DIR / "guide_snapshot.json"
STATE_PATH = APP_DIR / "state.json"
