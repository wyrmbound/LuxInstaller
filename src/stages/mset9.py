"""After "Removing MSET9": the ID0 should hold exactly one normal ID1 again.

While MSET9 is set up, the ID0 holds a hacked ID1 (garbled name) next to the user's real
ID1 renamed `<id1>_user-id1` (troubleshooting-mset9, manual removal). Read-only check.
"""
from __future__ import annotations

from pathlib import Path

from sdcard import Check, scan_3ds_folder

TROUBLE = "troubleshooting-mset9"


def verify_removed(sd_root: Path) -> list[Check]:
    layout = scan_3ds_folder(Path(sd_root))
    if not layout.present:
        return [Check("mset9_removed", "fail", "The 'Nintendo 3DS' folder is missing from the card.", TROUBLE)]
    checks = []
    for id0 in layout.id0s:
        if id0.user_id1_backups and not (set(id0.other) - set(id0.user_id1_backups)):
            # Only the renamed real ID1 is left: the MSET9 script now says "Nothing to do".
            name = id0.user_id1_backups[0]
            checks.append(Check("mset9_removed", "fail",
                                f"Your data folder is still named {name}. Rename it to {name[:32]} "
                                "(the last step of the manual removal on the troubleshooting page). "
                                "Don't continue until this is fixed.", TROUBLE))
        elif id0.user_id1_backups or id0.other:
            leftovers = ", ".join(id0.other)
            checks.append(Check("mset9_removed", "fail",
                                f"MSET9 is still set up (found {leftovers}). Run the MSET9 script again and "
                                "choose 4 then 5, or follow the manual removal on the troubleshooting page. "
                                "Don't continue until this is fixed.", TROUBLE))
        elif len(id0.id1s) == 1:
            checks.append(Check("mset9_removed", "ok", "MSET9 is removed and your ID1 is back in place"))
        else:
            checks.append(Check("mset9_removed", "warn",
                                f"Expected exactly one ID1 folder, found {len(id0.id1s)}.", TROUBLE))
    if not layout.id0s:
        checks.append(Check("mset9_removed", "fail", "No ID0 folder was found in 'Nintendo 3DS'.", TROUBLE))
    return checks
