"""After "Installing boot9strap": Luma3DS should now be booting from the SD card."""
from __future__ import annotations

from pathlib import Path

from sdcard import Check


def verify_installed(sd_root: Path) -> list[Check]:
    sd_root = Path(sd_root)
    checks = []
    if (sd_root / "boot.firm").is_file() and (sd_root / "boot.firm").stat().st_size:
        checks.append(Check("boot_firm", "ok", "boot.firm is on the root of the SD card"))
    else:
        # The guide: a white LED and shutdown means Luma3DS's boot.firm is missing from the root.
        checks.append(Check("boot_firm", "fail", "boot.firm is missing from the root of the SD card. "
                            "Without it the console shows a white LED and shuts down.", "troubleshooting"))
    if (sd_root / "luma" / "config.ini").is_file():
        checks.append(Check("luma_config", "ok", "Luma3DS has saved its configuration (luma/config.ini)"))
    else:
        checks.append(Check("luma_config", "warn",
                            "luma/config.ini isn't on the card yet. Luma3DS writes it when you press (Start) "
                            "on its configuration menu. If you didn't reach that menu, boot9strap may not "
                            "have installed.", "troubleshooting"))
    return checks
