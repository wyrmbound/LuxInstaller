"""Baby-step walkthroughs: one instruction at a time, with the PC side done automatically.

A walkthrough is a list of Steps. Two kinds:

- "you" steps: the person does something (usually on the console). The page shows the button
  presses one by one, what should happen, and asks "Did that happen?". Yes moves on; No shows
  `if_not` help plus the guide link for that part.
- "auto" steps: the assistant does it (copy files, run the MSET9 script, check the card, back
  up). The page runs `action` through Core.walk_action(action) in the background, shows
  progress, and moves on by itself when it succeeds; on failure it shows the message and offers
  Try again.

Every step carries `where` ("pc" or "console"): where the SD card must be. When it changes
between steps, the page first shows a short "move the SD card" step.

The wording is written for people, from the guide's own pages (`guide` points at the section
it comes from). guide_drift() reports sections whose text changed since the wording was written,
so the page can warn that the guide may say something newer.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Step:
    id: str  # stable key; progress is saved as "wt:<id>" in state.completed
    kind: str  # "you" or "auto"
    title: str  # short headline, e.g. "Open Mii Maker"
    do: tuple[str, ...] = ()  # "you": one short instruction per line, in order
    expect: str = ""  # "you": what should happen ("You should see ...")
    if_not: str = ""  # "you": what to do when it didn't happen
    action: str = ""  # "auto": action name for Core.walk_action
    where: str = "console"  # where the SD card must be during this step: "pc" or "console"
    guide: tuple[str, str | None] = ("", None)  # (page slug, section number) this step follows
    warning: str = ""  # optional extra caution shown in bold (e.g. "don't touch the screen")


@dataclass
class ActionResult:
    ok: bool
    message: str  # one sentence for the person
    details: list[str] = field(default_factory=list)  # extra lines (e.g. what is missing)
    retry_hint: str = ""  # what to do before Try again (e.g. "Open Mii Maker on the console first")


# Action names a walkthrough may use (implemented in gui.core.Core.walk_action):
ACTIONS = {
    "copy_mset9": "Copy the MSET9 files to the SD card (dry-run plan, read back)",
    "mset9_create": "Run the MSET9 script: create the MSET9 ID1",
    "mset9_ready": "Run the MSET9 script: check it says Ready (status report if not)",
    "mset9_inject": "Run the MSET9 script: inject the trigger file",
    "check_b9s": "Check boot.firm and luma/config.ini are on the card",
    "mset9_remove": "Run the MSET9 script: remove the trigger file and MSET9",
    "check_mset9_removed": "Check only your own ID1 is left",
    "copy_finalize": "Copy finalize.romfs and luma/payloads/x_finalize_helper.firm",
    "check_backups": "Check the finalize script's backups are on the card",
    "backup": "Copy the backups to this computer and verify them",
}
