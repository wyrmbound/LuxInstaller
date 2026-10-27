"""Progress through the guide, saved so the user can quit and resume mid-process."""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timezone
from pathlib import Path

import paths

STATE_FORMAT = 1


class StateError(Exception):
    pass


@dataclass
class State:
    format: int = STATE_FORMAT
    model: str | None = None
    version: str | None = None
    method: str | None = None
    guide_commit: str | None = None
    stage: str | None = None
    completed: list[str] = field(default_factory=list)
    drive_label: str | None = None
    drive_serial: str | None = None
    integrity_plan: dict | None = None
    downloads: list[dict] = field(default_factory=list)  # {name, path, sha256, verified}
    backup_dir: str | None = None
    walk: bool = False  # following the baby-step walkthrough instead of the guide's stage pages
    luma: dict | None = None  # which Luma: None = official; {"kind": "github"|"file", ...} for a fork
    updated: str | None = None

    @classmethod
    def load(cls, path: Path | None = None) -> "State":
        """The saved state, or a fresh one if none exists. A damaged file is never deleted."""
        path = Path(path or paths.STATE_PATH)
        if not path.exists():
            return cls()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, ValueError) as e:
            raise StateError(f"{path} could not be read ({e}). It was left in place; move it aside to start over.") from e
        if not isinstance(data, dict) or data.get("format") != STATE_FORMAT:
            raise StateError(f"{path} has unsupported format {data.get('format') if isinstance(data, dict) else None!r}")
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})

    def save(self, path: Path | None = None) -> None:
        """Write atomically: a crash leaves either the old file or the new one, never a torn one."""
        path = Path(path or paths.STATE_PATH)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.updated = datetime.now(timezone.utc).isoformat(timespec="seconds")
        tmp = path.with_name(path.name + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(asdict(self), f, indent=1)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)

    def mark_done(self, stage: str) -> None:
        if stage not in self.completed:
            self.completed.append(stage)
        if self.stage == stage:
            self.stage = None

    def reset(self) -> None:
        fresh = State()
        for f in fields(self):
            setattr(self, f.name, getattr(fresh, f.name))
