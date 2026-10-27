from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from state import STATE_FORMAT, State, StateError

SRC = Path(__file__).resolve().parents[1] / "src"


def test_missing_file_gives_fresh_state(tmp_path):
    s = State.load(tmp_path / "state.json")
    assert s.stage is None and s.completed == [] and s.format == STATE_FORMAT


def test_round_trip(tmp_path):
    p = tmp_path / "d" / "state.json"
    s = State(model="new3ds", version="Ver. 11.17.0-50U", method="installing-boot9strap-(mset9)", stage="prep")
    s.downloads.append({"name": "Luma3DS", "path": "x.zip", "sha256": "ab"})
    s.save(p)
    t = State.load(p)
    assert (t.model, t.stage, t.downloads) == ("new3ds", "prep", s.downloads)
    assert t.updated


def test_mark_done_and_reset(tmp_path):
    s = State(stage="prep")
    s.mark_done("prep")
    s.mark_done("prep")
    assert s.completed == ["prep"] and s.stage is None
    s.model = "old3ds"
    s.reset()
    assert s == State()


def test_corrupt_file_raises_and_is_kept(tmp_path):
    p = tmp_path / "state.json"
    p.write_text("{not json", encoding="utf-8")
    with pytest.raises(StateError):
        State.load(p)
    assert p.read_text(encoding="utf-8") == "{not json"


def test_unknown_format_is_refused(tmp_path):
    p = tmp_path / "state.json"
    p.write_text(json.dumps({"format": 99}), encoding="utf-8")
    with pytest.raises(StateError):
        State.load(p)


CHILD = r"""
import os, sys
sys.path.insert(0, sys.argv[1])
from state import State
p = sys.argv[2]
s = State(stage="stage-1")
s.save(p)
s.stage = "stage-2"
os.replace = lambda *a, **k: os._exit(1)   # killed mid-save, after the temp file is written
s.save(p)
"""


def test_resume_after_kill_mid_save(tmp_path):
    p = tmp_path / "state.json"
    r = subprocess.run([sys.executable, "-I", "-c", CHILD, str(SRC), str(p)])
    assert r.returncode == 1
    s = State.load(p)
    assert s.stage == "stage-1"  # last complete save, not a torn file
