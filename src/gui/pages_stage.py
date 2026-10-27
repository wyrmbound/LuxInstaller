"""One page per guide stage: copy the section's files (if any), show its steps, check the SD card.

Same rules as cli.run_stage: files are copied only after the plan is shown and WRITE is typed,
then read back; a stage with SD checks only completes when they pass (warnings can be
overridden, failures can't); a dry run shows the copy plan and never completes anything.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from gui.core import stage_title
from gui.widgets import (LEVEL_COLOR, ChecksView, Link, Page, PlanView, ProgressRow, ScrollText, heading,
                         paragraph)


class StagePage(Page):
    name = "stage"
    title = "Stage"

    def build(self) -> None:
        self.next_btn.config(text="I've finished these steps")
        self.check_again_btn = ttk.Button(self.extra_nav, text="Check again", command=self._check_again)
        self.continue_btn = ttk.Button(self.extra_nav, text="Continue anyway", command=self._continue_anyway)
        self.index = 0
        self.stage = None
        self.gen = 0
        self.phase = ""
        self._reset_refs()

    def _reset_refs(self) -> None:
        self.drive = None
        self.plan = None
        self.copied = False
        self.copy_box = self.copy_msg = self.label_var = self.confirm_btn = None
        self.plan_view = self.write_var = self.copy_btn = self.progress = self.copy_checks = None
        self.steps_text = self.checks = None
        self._check_action = None
        self._can_continue = False

    # --- entering ----------------------------------------------------------------------

    def enter(self, index: int = 0, **kw) -> None:
        self.gen += 1
        for w in self.body.winfo_children():
            w.destroy()
        self._reset_refs()
        self._hide_nav_extras()
        self.set_status("")
        self.enable_next(False, "I've finished these steps")
        stages = self.core.stages()
        if index >= len(stages):
            self.wizard.show("done")
            return
        self.index, self.stage = index, stages[index]
        if self.stage.key == "backup":
            self.wizard.show("backup")
            return
        if not self.core.args.dry_run:
            self.core.begin(self.stage)

        st = self.stage
        heading(self.body, f"Stage {index + 1} of {len(stages)}").pack(anchor="w")
        paragraph(self.body, stage_title(st), font=("Segoe UI", 11, "bold")).pack(anchor="w", pady=(2, 4))
        links = ttk.Frame(self.body)
        links.pack(fill="x", anchor="w")
        Link(links, st.url, "Open this section in the guide").pack(side="left")
        ttk.Label(links, text="   If something goes wrong:").pack(side="left")
        for url in st.troubleshooting:
            Link(links, url, url.rsplit("/", 1)[-1]).pack(side="left", padx=4)

        if st.stages_files and not self.core.files_done(st):
            self._copy_phase()
        else:
            self._show_steps()

    def _hide_nav_extras(self) -> None:
        self.check_again_btn.pack_forget()
        self.continue_btn.pack_forget()

    def _bg(self, fn, on_done, on_error=None) -> None:
        """run_bg, ignoring results that arrive after the user moved to another stage."""
        gen = self.gen

        def done(result):
            if gen == self.gen:
                on_done(result)

        def error(e):
            if gen == self.gen:
                (on_error or self._error)(e)

        self.wizard.run_bg(fn, done, error)

    def _error(self, e) -> None:
        self.set_status(f"Stopped: {getattr(e, 'reason', None) or e}", "fail")
        self.wizard.show_error(e)

    # --- copying files -----------------------------------------------------------------

    def _copy_phase(self) -> None:
        self.copy_box = ttk.LabelFrame(self.body, text="Files the assistant copies to the SD card", padding=8)
        self.copy_box.pack(fill="both", expand=True, pady=(8, 0))
        self.copy_msg = paragraph(self.copy_box, "Looking for the SD card...")
        self.copy_msg.pack(anchor="w")
        self._find_card()

    def _find_card(self) -> None:
        self.phase = "card"
        self._hide_nav_extras()
        self._check_action = self._find_card
        self._bg(self.core.find_card, self._card_found)

    def _card_found(self, drive) -> None:
        if drive is None:
            self.copy_msg.config(text="Put the SD card in this computer, then click Check again.")
            self.set_status("The SD card isn't connected.", "warn")
            self.check_again_btn.pack(side="left")
            return
        self._hide_nav_extras()
        self.set_status("")
        self.drive = drive
        if self.core.needs_label_again(drive):
            self._ask_label()
        else:
            self._plan()

    def _ask_label(self) -> None:
        self.phase = "label"
        target = self.core.confirmation_target(self.drive)
        self.copy_msg.config(text=f"Before writing to {self.drive.mount}, type the drive label exactly ({target}).")
        row = ttk.Frame(self.copy_box)
        row.pack(fill="x", pady=4)
        self.label_var = tk.StringVar()
        entry = ttk.Entry(row, textvariable=self.label_var, width=30)
        entry.pack(side="left")
        entry.bind("<Return>", lambda e: self._confirm_label())
        self.confirm_btn = ttk.Button(row, text="Confirm", command=self._confirm_label)
        self.confirm_btn.pack(side="left", padx=6)
        self._label_row = row

    def _confirm_label(self) -> None:
        import sdcard

        try:
            self.core.reconfirm(self.drive, self.label_var.get())
        except sdcard.ConfirmationError as e:
            self.set_status(str(e), "fail")
            return
        self._label_row.destroy()
        self.set_status("")
        self._plan()

    def _plan(self) -> None:
        self.phase = "planning"
        self.copy_msg.config(text=f"Planned changes to {self.drive.mount} (nothing has been written yet):")
        self._bg(lambda: self.core.plan_files(self.stage, self.drive), self._show_plan)

    def _show_plan(self, plan) -> None:
        self.plan = plan
        self.phase = "plan"
        self.plan_view = PlanView(self.copy_box, height=7)
        self.plan_view.pack(fill="both", expand=True, pady=4)
        self.plan_view.show(plan)
        if plan.unparsed:
            box = ScrollText(self.copy_box, height=4)
            box.pack(fill="x")
            box.set([("The assistant can't do these steps by itself, so it won't write anything:\n", "danger")]
                     + [(f"• {u}\n", "") for u in plan.unparsed])
            Link(self.copy_box, self.stage.url, "Follow this section in the guide instead").pack(anchor="w")
            self.set_status("Nothing will be written. Follow the guide page for this section.", "fail")
            return
        if self.core.args.dry_run:
            paragraph(self.copy_box, "Dry run: nothing will be written.", foreground=LEVEL_COLOR["info"]).pack(anchor="w")
            self.set_status("Dry run: this is what would be copied. Nothing was written.")
            return
        n = len(plan.ops)
        row = ttk.Frame(self.copy_box)
        row.pack(fill="x", pady=4)
        ttk.Label(row, text=f"Type WRITE to copy these {n} items to {self.drive.mount}:").pack(side="left")
        self.write_var = tk.StringVar()
        self.write_entry = ttk.Entry(row, textvariable=self.write_var, width=10)
        self.write_entry.pack(side="left", padx=6)
        self.copy_btn = ttk.Button(row, text="Copy files", command=self._copy, state="disabled")
        self.copy_btn.pack(side="left")
        self.write_var.trace_add("write", lambda *a: self.copy_btn.config(
            state="normal" if self.write_var.get() == "WRITE" else "disabled"))
        self.progress = ProgressRow(self.copy_box, "")
        self.progress.pack(fill="x", pady=(4, 0))
        self.copy_checks = ChecksView(self.copy_box)
        self.copy_checks.pack(fill="x")

    def _copy(self) -> None:
        if self.write_var.get() != "WRITE" or self.core.args.dry_run:
            return
        self.phase = "copying"
        self.copy_btn.config(state="disabled")
        self.write_entry.config(state="disabled")
        total = len(self.plan.ops)
        self.progress.update_progress(0, total, "Copying...")
        cb = self.wizard.progress_cb(
            lambda done, tot, dest: self.progress.update_progress(done, tot, f"Copied /{dest} ({done} of {tot})"))
        self._bg(lambda: self.core.write_files(self.stage, self.plan, cb), self._copied, self._copy_failed)

    def _copy_failed(self, e) -> None:
        self.phase = "plan"
        self._error(e)

    def _copied(self, checks) -> None:
        self.copy_checks.show(checks)
        if any(c.level == "fail" for c in checks):
            self.phase = "copy-failed"
            self.set_status("Some files didn't read back correctly. Don't continue; see the links above.", "fail")
            return
        self.copied = True
        self.progress.update_progress(1, 1, "All files copied and read back.")
        self.set_status("Files copied and checked. Now follow the steps below.", "ok")
        self._show_steps()

    # --- steps and checks --------------------------------------------------------------

    def _show_steps(self) -> None:
        self.phase = "steps"
        st = self.stage
        sec = st.section or {"intro": [], "notes": [], "steps": []}
        done_steps = {op.step for op in self.plan.ops} if (self.plan is not None and self.copied) else set()
        chunks: list = []
        for line in sec["intro"]:
            chunks.append((line + "\n", ""))
        for note in sec["notes"]:
            if note["kind"] in ("warning", "danger"):
                chunks.append((f"⚠ {note['text']}\n", note["kind"]))
        if chunks:
            chunks.append(("\n", ""))
        for n, step in enumerate(sec["steps"], 1):
            chunks.append((f"{n}. {step['text']}", ""))
            if step["text"].strip() in done_steps:
                chunks.append(("   ✔ done by the assistant", "done"))
            chunks.append(("\n", ""))
            for d in step["details"]:
                chunks.append((f"      {d}\n", "muted"))
            if note := st.annotate(step["text"]):
                chunks.append((f"      >>> {note}\n", "warning"))
        self.steps_text = ScrollText(self.body, height=14)
        self.steps_text.pack(fill="both", expand=True, pady=(8, 0))
        self.steps_text.set(chunks)
        self.checks = ChecksView(self.body)
        self.checks.pack(fill="x", pady=(6, 0))
        self.enable_next(not self.core.args.dry_run, "I've finished these steps")

    def on_next(self) -> None:
        if self.core.args.dry_run or self.phase not in ("steps", "verify"):
            return
        if self.stage.verifiers:
            self._verify()
        else:
            self._complete()

    def _verify(self) -> None:
        self.phase = "verify"
        self._check_action = self._verify
        self._can_continue = False
        self.enable_next(False)
        self._hide_nav_extras()
        self.set_status("Checking the SD card...")
        self._bg(lambda: self.core.verify(self.stage), self._verified)

    def _verified(self, checks) -> None:
        # Only a result with warnings and no failures may be overridden.
        self._can_continue = checks is not None and not any(c.level == "fail" for c in checks) \
            and any(c.level == "warn" for c in checks)
        if checks is None:
            self.set_status("Put the SD card in this computer, then click Check again.", "warn")
            self.check_again_btn.pack(side="left")
            return
        self.checks.show(checks)
        if all(c.level == "ok" for c in checks):
            self._complete()
        elif any(c.level == "fail" for c in checks):
            self.set_status("Fix this using the links, then click Check again.", "fail")
            self.check_again_btn.pack(side="left")
        else:
            self.set_status("Check the warning above. Click Check again, or Continue anyway if you're sure.", "warn")
            self.check_again_btn.pack(side="left")
            self.continue_btn.pack(side="left", padx=(6, 0))

    def _check_again(self) -> None:
        if self._check_action:
            self._check_action()

    def _continue_anyway(self) -> None:
        if self.phase == "verify" and self._can_continue:
            self._complete()

    def _complete(self) -> None:
        if self.core.args.dry_run:
            return
        self.core.complete(self.stage)
        self.wizard.show("stage", index=self.index + 1)


PAGES = [StagePage]
