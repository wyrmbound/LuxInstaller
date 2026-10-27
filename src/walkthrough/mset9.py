"""The MSET9 route, start to finish, as baby steps.

Written from the guide's "Installing boot9strap (MSET9 CLI)" and "Finalizing Setup" pages at
commit 63013be. Everything the guide does on a computer (copying files, running the MSET9
script, checking the card, copying backups) is an "auto" step the assistant does itself.
"""
from __future__ import annotations

import hashlib

from walkthrough import Step

MSET9 = "installing-boot9strap-(mset9-cli)"
FINALIZE = "finalizing-setup"
TROUBLE_MSET9 = "troubleshooting-mset9"

# sha256[:16] of each section's step texts when the wording below was written (guide 63013be).
FINGERPRINTS = {
    (MSET9, "I"): "bdf867e285125d6b",
    (MSET9, "II"): "070c3c0fbf0f079f",
    (MSET9, "III"): "f53301167f492b17",
    (MSET9, "IV"): "075e948a4d177126",
    (FINALIZE, "I"): "256487a5ed3b6178",
    (FINALIZE, "II"): "fa8b03a5272f4f6a",
    (FINALIZE, "III"): "793abbfbd3fd7d75",
    (FINALIZE, "IV"): "21f2d25332120fd5",
}

STEPS: tuple[Step, ...] = (
    # --- MSET9 Section I: set up MSET9 on the card -----------------------------------------
    Step("copy-mset9", "auto", "Copying the MSET9 files to the SD card", action="copy_mset9",
         where="pc", guide=(MSET9, "I")),
    Step("mset9-create", "auto", "Setting up MSET9 on the SD card", action="mset9_create",
         where="pc", guide=(MSET9, "I")),
    Step("boot-home", "you", "Turn the console on",
         do=("Put the SD card in the console",
             "Power on the console",
             "Wait for the HOME Menu"),
         expect="The HOME Menu appears, but most of your games and apps are missing. "
                "That's expected: MSET9 hides them for now, and they all come back at the end.",
         if_not="If it says \"Could not detect an SD card\", power off, push the SD card in firmly, and "
                "power on again.",
         where="console", guide=(MSET9, "I")),
    Step("mii-maker", "you", "Open Mii Maker once",
         do=("Tap Mii Maker on the HOME Menu",
             "Wait for the \"Welcome to Mii Maker\" screen (or the normal Mii Maker screen if you've used it before)",
             "Press (HOME) and close Mii Maker"),
         expect="You're back on the HOME Menu.",
         if_not="If Mii Maker shows a message about creating data, let it finish, then press (HOME).",
         where="console", guide=(MSET9, "I")),
    Step("reset-software", "you", "Refresh the software list",
         do=("Open System Settings",
             "Tap Data Management",
             "Tap Nintendo 3DS",
             "Tap Software",
             "Tap Reset, and confirm"),
         expect="It finishes and goes back. This doesn't erase any of your data.",
         if_not="If you can't find Software, make sure you tapped Nintendo 3DS (not DSiWare).",
         where="console", guide=(MSET9, "I")),
    Step("mset9-ready", "auto", "Checking that MSET9 is ready", action="mset9_ready",
         where="pc", guide=(MSET9, "I")),

    # --- MSET9 Section II: trigger it -------------------------------------------------------
    Step("hover-settings", "you", "Make System Settings the selected icon",
         do=("Put the SD card in the console and power it on",
             "On the HOME Menu, use the D-Pad to move the cursor onto System Settings (don't open it)",
             "Power the console off, then on again"),
         expect="When the console starts up, the cursor is already on System Settings.",
         if_not="Move the cursor onto System Settings with the D-Pad, then power off and on again.",
         where="console", guide=(MSET9, "II")),
    Step("open-extra-data", "you", "Open Extra Data",
         do=("Press (A) to open System Settings",
             "Tap Data Management",
             "Tap Nintendo 3DS",
             "Tap Extra Data"),
         expect="You're on the Extra Data screen.",
         if_not="Go back with (B) and try again: Data Management, then Nintendo 3DS, then Extra Data.",
         warning="From now on, don't press any buttons or touch the screen until the assistant says so.",
         where="console", guide=(MSET9, "II")),
    Step("pull-sd", "you", "Take the SD card out while the console is still on",
         do=("Leave the console ON (don't press anything)",
             "Pull the SD card out of the console",
             "Put the SD card in this computer"),
         expect="The console says no SD card is inserted. That's expected.",
         if_not="If the console turned off or you pressed something, start again from "
                "\"Make System Settings the selected icon\" (use Back).",
         warning="Don't turn the console off. Don't touch any buttons or the screen.",
         where="pc", guide=(MSET9, "II")),
    Step("mset9-inject", "auto", "Arming MSET9", action="mset9_inject", where="pc", guide=(MSET9, "II")),
    Step("push-sd", "you", "Put the SD card back in",
         do=("Without pressing any buttons or touching the screen, put the SD card back in the console",),
         expect="After a few seconds SafeB9SInstaller appears: text on both screens, with steps listed on "
                "the bottom screen.",
         if_not="A red screen, or stuck on a loading screen for more than 10 seconds, means it didn't trigger. "
                "Power off, then go back to \"Make System Settings the selected icon\" and try again. "
                "If it keeps failing, see the MSET9 troubleshooting page.",
         where="console", guide=(MSET9, "II")),

    # --- MSET9 Section III: install boot9strap ----------------------------------------------
    Step("install-b9s", "you", "Install boot9strap",
         do=("Look at the top screen: it shows a button combination",
             "Press those buttons, in that order"),
         expect="All seven steps on the bottom screen turn green.",
         if_not="If the top screen is blank but the bottom says \"Crypto Status - all checks passed\", press "
                "D-Pad Left, D-Pad Down, D-Pad Right, D-Pad Up, then (A). If a step is red and no combination "
                "is shown, stop and see the MSET9 troubleshooting page.",
         where="console", guide=(MSET9, "III")),
    Step("reboot-luma", "you", "Restart",
         do=("Press (A) to reboot",),
         expect="The Luma3DS configuration menu appears.",
         if_not="If the console just shows the HOME Menu, power off and on again while holding (Select).",
         where="console", guide=(MSET9, "III")),
    Step("save-luma", "you", "Save the Luma3DS settings",
         do=("Don't change anything in this menu",
             "Press (Start) to save and restart"),
         expect="The console starts up to the HOME Menu. It looks normal, but it's now running custom firmware. "
                "(Your games are still hidden until the next steps.)",
         if_not="If the light turns white and the console shuts off, boot.firm is missing from the SD card. "
                "Put the card in this computer and use Back to repeat \"Copying the MSET9 files\".",
         where="console", guide=(MSET9, "III")),
    Step("check-b9s", "auto", "Checking custom firmware is on the card", action="check_b9s",
         where="pc", guide=(MSET9, "III")),

    # --- MSET9 Section IV: remove MSET9, get your data back ---------------------------------
    Step("mset9-remove", "auto", "Removing MSET9 and bringing your data back", action="mset9_remove",
         where="pc", guide=(MSET9, "IV")),
    Step("check-removed", "auto", "Checking your data is back in place", action="check_mset9_removed",
         where="pc", guide=(MSET9, "IV")),

    # --- Finalizing Setup Section I: files for the setup script -----------------------------
    Step("copy-finalize", "auto", "Copying the setup script to the SD card", action="copy_finalize",
         where="pc", guide=(FINALIZE, "I")),

    # --- Finalizing Setup Section II: updates (safe now) ------------------------------------
    Step("system-update", "you", "Check for system updates",
         do=("Put the SD card in the console and power it on",
             "Open System Settings",
             "Tap Other Settings",
             "Go all the way to the right and tap System Update"),
         expect="It says your system is up to date (or it updates). With custom firmware installed now, this is "
                "safe and won't remove it.",
         if_not="If you get an error: in System Settings > Internet Settings > Connection Settings, open your "
                "connection, Change Settings, set DNS to Auto and Proxy Settings to No, then try again. "
                "An error here doesn't stop the setup; you can carry on.",
         where="console", guide=(FINALIZE, "II")),

    # --- Finalizing Setup Section III: clock and sound --------------------------------------
    Step("open-rosalina", "you", "Open the Rosalina menu",
         do=("On the HOME Menu, press (L) + D-Pad Down + (Select) at the same time",),
         expect="The Rosalina menu appears on the top screen.",
         if_not="Press all three together and hold for a moment. If one of those buttons is broken, see the "
                "guide page for a different combination.",
         where="console", guide=(FINALIZE, "III")),
    Step("dsp-clock", "you", "Save the sound firmware and fix the clock",
         do=("Select \"Miscellaneous options\"",
             "Select \"Dump DSP firmware\", then press (B)",
             "Select \"Nullify user time offset\", then press (B)",
             "Press (B) twice to close the menu"),
         expect="You're back on the HOME Menu.",
         where="console", guide=(FINALIZE, "III")),

    # --- Finalizing Setup Section IV: the setup script --------------------------------------
    Step("start-helper", "you", "Start the setup helper",
         do=("Power off the console",
             "Hold (X), and while holding it, power on the console"),
         expect="After a few seconds GodMode9 opens.",
         if_not="If it starts to the HOME Menu instead, power off and try again holding (X) before you press "
                "power. If it still doesn't, the setup files may be missing: put the card in this computer and "
                "use Back to repeat \"Copying the setup script\".",
         where="console", guide=(FINALIZE, "IV")),
    Step("gm9-first", "you", "Answer GodMode9's first questions",
         do=("If it asks for a language: pick English with the D-Pad and press (A)",
             "If it asks to create an essential files backup: press (A), then (A) again",
             "If it asks to fix the RTC date & time: press (A), set the date and time, then press (A)"),
         expect="You see [root] and a list of drives.",
         where="console", guide=(FINALIZE, "IV")),
    Step("run-finalize", "you", "Run the setup script",
         do=("Press (HOME) to open the action menu",
             "Select \"Scripts...\" (not \"Lua scripts\")",
             "Select \"finalize\"",
             "Answer its questions as they come up",
             "If you see \"Information #05: No title database\": press (A), then enter the button combination "
             "shown on the bottom screen"),
         expect="The script says \"Setup complete!\"",
         if_not="If it stops with an error, follow what the message says. To run it again: power off, hold "
                "(Start) while powering on to open GodMode9, then repeat this step.",
         where="console", guide=(FINALIZE, "IV")),
    Step("finalize-off", "you", "Turn the console off",
         do=("Press (A) to power off",),
         expect="The console turns off.",
         where="console", guide=(FINALIZE, "IV")),
    Step("check-backups", "auto", "Checking your backups are on the card", action="check_backups",
         where="pc", guide=(FINALIZE, "IV")),
    Step("backup", "auto", "Copying your backups to this computer", action="backup",
         where="pc", guide=(FINALIZE, "IV")),
)


def steps() -> list[Step]:
    return list(STEPS)


def section_fingerprint(snapshot, slug: str, number: str) -> str | None:
    try:
        sec = next(s for s in snapshot.page(slug)["sections"] if s["number"] == number)
    except Exception:
        return None
    return hashlib.sha256("\n".join(st["text"] for st in sec["steps"]).encode()).hexdigest()[:16]


def guide_drift(snapshot) -> list[str]:
    """Guide sections that changed since these steps were written (empty when they match)."""
    from guide_parser import page_url

    changed = []
    for (slug, number), expected in FINGERPRINTS.items():
        if section_fingerprint(snapshot, slug, number) != expected:
            changed.append(f"{page_url(slug)} (Section {number})")
    return changed
