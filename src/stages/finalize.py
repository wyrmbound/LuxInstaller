"""After Finalizing Setup's "Setup Script": the backups must be on the SD before anything else.

The guide's last two steps have the user copy `/gm9/backups/` off the card and then delete the
SysNAND files from it. The assistant does the copy (and verifies it) in the backup stage, so
those steps are annotated to make sure nothing is deleted before the copy is safe.
"""
from __future__ import annotations

from pathlib import Path

import backup
from sdcard import Check

TROUBLE = "troubleshooting-finalizing-setup"


def verify_backups(sd_root: Path) -> list[Check]:
    found = backup.find_backups(Path(sd_root))
    missing = found.missing
    if missing:
        return [Check("backups", "fail",
                      f"The finalize script's backups are not complete on the SD card (missing: {', '.join(missing)}). "
                      "Re-run the script from GodMode9 (hold Start on boot) until it says \"Setup complete!\".", TROUBLE)]
    # The NAND image is over 1GB; the backup stage checks it against its .sha while copying.
    where = lambda p: "/" + p.parent.relative_to(Path(sd_root)).as_posix() + "/"  # noqa: E731
    return [Check("backups", "ok", f"NAND backup and its .sha found in {where(found.nand_bin)}, "
                                   f"{found.essential.name} in {where(found.essential)}")]


def annotate_step(text: str) -> str | None:
    low = text.casefold()
    if "gm9/backups" not in low:
        return None
    if low.startswith("copy"):
        return "The assistant does this copy for you in the next stage and checks every file."
    if "delete" in low:
        return "Do NOT delete anything yet. Wait until the assistant has copied and verified your backups."
    return None
