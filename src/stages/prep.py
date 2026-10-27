"""Right after staging: every planned file is on the card, matching what was written."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from sdcard import Check


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_staged(sd_root: Path, plan, log_path: Path | None) -> list[Check]:
    """Read back every file the plan wrote and compare it with the hash in the write log."""
    sd_root = Path(sd_root)
    logged = {}
    if log_path and Path(log_path).is_file():
        for line in Path(log_path).read_text(encoding="utf-8").splitlines():
            entry = json.loads(line)
            if entry.get("sha256"):
                logged[entry["dest"]] = entry["sha256"]
    bad = []
    for op in plan.ops:
        target = sd_root / op.dest
        if op.kind == "mkdir":
            if not target.is_dir():
                bad.append(f"/{op.dest}/ (folder missing)")
        elif not target.is_file():
            bad.append(f"/{op.dest} (missing)")
        elif op.dest in logged and _sha256(target) != logged[op.dest]:
            bad.append(f"/{op.dest} (contents differ from what was written)")
    if bad:
        return [Check("staged", "fail", "These files didn't read back correctly from the SD card: "
                      + "; ".join(bad) + ". The card may be failing; run the integrity test.", "troubleshooting")]
    files = sum(op.kind != "mkdir" for op in plan.ops)
    return [Check("staged", "ok", f"All {files} copied files read back correctly")]
