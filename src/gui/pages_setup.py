"""Setup pages: loading the guide, resuming, the console, the SD card, and cards with CFW."""
from __future__ import annotations

import tkinter as tk
import types
from tkinter import messagebox, ttk

from gui.widgets import (LEVEL_COLOR, PAD, ChecksView, Link, Page, PlanView, ProgressRow, ScrollText,
                         heading, paragraph)

LEVEL_RANK = {"ok": 0, "info": 1, "warn": 2, "fail": 3}


def _quiet(page: Page) -> bool:
    return getattr(page.wizard, "quiet", False)


def _set_header(page: Page) -> None:
    if page.core.snap is not None:
        page.wizard.guide_label.config(text=f"Guide: hacks-guide/Guide_3DS @ {page.core.guide_commit}")


class LoadPage(Page):
    name = "load"
    title = "Loading the guide"

    def build(self):
        heading(self.body, "Getting the guide").pack(anchor="w")
        self.msg = paragraph(self.body, "Loading the guide…")
        self.msg.pack(anchor="w", pady=(PAD, PAD // 2))
        self.bar = ttk.Progressbar(self.body, mode="indeterminate")
        self.bar.pack(fill="x")
        self.retry_btn = ttk.Button(self.extra_nav, text="Retry", command=self.enter)

    def enter(self, **kw):
        self.retry_btn.pack_forget()
        self.msg.config(text="Loading the guide…", foreground="")
        self.bar.start(15)
        self.wizard.run_bg(self.core.load_guide, self._loaded, self._failed)

    def _loaded(self, newer):
        if newer and not _quiet(self) and messagebox.askyesno(
                "Guide updated", f"The guide has changed ({self.core.guide_commit} → {newer[:7]}). "
                                 "Load the new version now?", parent=self.wizard):
            self.msg.config(text="Loading the new version of the guide…")
            self.wizard.run_bg(self.core.refresh_guide, lambda _: self._continue(), self._failed)
            return
        self._continue()

    def _continue(self):
        self.bar.stop()
        _set_header(self)
        try:
            info = self.core.resume_info()
        except Exception as e:  # saved progress refers to a page the guide no longer has
            self.wizard.show_error(e)
            info = None
        self.wizard.show("resume" if info else "console")

    def _failed(self, e):
        self.bar.stop()
        reason = getattr(e, "reason", None) or str(e) or type(e).__name__
        self.msg.config(text=f"Couldn't load the guide: {reason}\n\nCheck your internet connection, then retry. "
                             "(Or start the assistant with --guide-dir pointing at a local Guide_3DS copy.)",
                        foreground=LEVEL_COLOR["fail"])
        self.retry_btn.pack(side="right")


class ResumePage(Page):
    name = "resume"
    title = "Saved progress"

    def build(self):
        heading(self.body, "Welcome back").pack(anchor="w")
        self.summary = paragraph(self.body, "")
        self.summary.pack(anchor="w", pady=PAD)
        self.start_over_btn = ttk.Button(self.extra_nav, text="Start over", command=self.on_start_over)
        self.start_over_btn.pack(side="right")

    def enter(self, **kw):
        self.info = info = self.core.resume_info()
        if info is None:
            self.wizard.show("console")
            return
        lines = [f"Console: {info.console}, {info.version}",
                 f"Method: {info.method}",
                 f"Progress: {info.done} of {info.total} stages done"]
        if info.next_title is None:
            lines.append(f"\nSetup is already finished. Backups: {info.backup_dir}")
            self.enable_next(False, "Resume →")
        else:
            lines.append(f"Next: {info.next_title}")
            self.enable_next(True, "Resume →")
        self.summary.config(text="\n".join(lines))
        self.set_status("")

    def on_start_over(self):
        if not _quiet(self) and not messagebox.askyesno(
                "Start over", "Forget the saved progress? Files on the SD card are not changed.", parent=self.wizard):
            return
        self.core.start_over()
        self.wizard.show("console")

    def on_next(self):
        self.enable_next(False)
        self.set_status("Checking the downloaded files…")
        self.wizard.run_bg(self.core.resume, self._resumed)

    def _resumed(self, cache_ok: bool):
        if not cache_ok:
            self.wizard.show("downloads")
            return
        if self.core.walk_mode:
            self.wizard.go_walk()
            return
        if self.core.args.dry_run:
            index = self.core.first_copy_stage()
            if index is None:
                self.set_status("Dry run: there are no file copies left to show.")
                return
        else:
            index = self.core.first_pending()
        self.wizard.show("stage", index=index)


class ConsolePage(Page):
    name = "console"
    title = "Your console"

    def build(self):
        heading(self.body, "Your console").pack(anchor="w")
        paragraph(self.body, "Which console do you have?").pack(anchor="w", pady=(PAD, 4))
        models = ttk.Frame(self.body)
        models.pack(anchor="w")
        self.model_var = tk.StringVar(value="")
        for i, (key, label) in enumerate(self.core.models()):
            ttk.Radiobutton(models, text=label, value=key, variable=self.model_var).grid(
                row=i // 3, column=i % 3, sticky="w", padx=(0, 18), pady=2)

        paragraph(self.body, "Open System Settings on the console. The version is at the bottom right of the top "
                             "screen (e.g. Ver. 11.17.0-50U).").pack(anchor="w", pady=(PAD, 4))
        row = ttk.Frame(self.body)
        row.pack(anchor="w", fill="x")
        self.version_var = tk.StringVar(value="")
        self.version_entry = ttk.Entry(row, textvariable=self.version_var, width=28)
        self.version_entry.pack(side="left")
        self.find_btn = ttk.Button(row, text="Find my method", command=self.on_find)
        self.find_btn.pack(side="left", padx=8)
        self.version_entry.bind("<Return>", lambda e: self.on_find())
        for var in (self.model_var, self.version_var):
            var.trace_add("write", lambda *a: self._changed())

        self.result = ttk.Frame(self.body)
        self.result.pack(fill="both", expand=True, pady=(PAD, 0))
        self.method_label = ttk.Label(self.result, text="", font=("Segoe UI", 12, "bold"))
        self.method_label.pack(anchor="w")
        self.message = paragraph(self.result, "")
        self.message.pack(anchor="w")
        self.links = ttk.Frame(self.result)
        self.links.pack(anchor="w", pady=4)
        self.explain = ScrollText(self.result, height=7)

    def enter(self, **kw):
        self.set_status("")

    def _changed(self):
        # The method shown must always match what's typed: any change needs a new lookup.
        if str(self.next_btn.cget("state")) == "normal":
            self.enable_next(False)
            self.set_status("Press Find my method again.")

    def _clear_result(self):
        self.method_label.config(text="")
        self.message.config(text="")
        for w in self.links.winfo_children():
            w.destroy()
        self.explain.pack_forget()

    def on_find(self):
        model, text = self.model_var.get(), self.version_var.get()
        self._clear_result()
        self.enable_next(False)
        if not model:
            self.set_status("Choose your console model first.", "fail")
            return
        self.set_status("Asking the guide which method applies…")
        self.find_btn.config(state="disabled")
        self.wizard.run_bg(lambda: self.core.choose(model, text), self._routed, self._failed)

    def _failed(self, e):
        import methods

        self.find_btn.config(state="normal")
        if isinstance(e, methods.VersionFormatError):
            self.set_status(str(e), "fail")
        else:
            self.set_status("The guide's method selection stopped. See the message.", "fail")
            self.wizard.show_error(e)

    def _routed(self, result):
        self.find_btn.config(state="normal")
        if result.outcome == "method":
            self.method_label.config(text=f"Method: {result.message}")
            Link(self.links, self.core.method_url(), "Instructions for this computer").pack(side="left")
            Link(self.links, result.url, "Guide page").pack(side="left", padx=PAD)
            chunks = []
            if getattr(result, "note", ""):
                chunks += [("Why this method\n\n", "h"), (result.note + "\n\n", "")]
            if result.explanation:
                chunks += [("From the guide's compatibility notes\n\n", "h"), (result.explanation, "")]
            if chunks:
                self.explain.set(chunks)
                self.explain.pack(fill="both", expand=True)
            self.set_status(f"Guide commit {self.core.guide_commit}. Next, pick the SD card.", "ok")
            self.enable_next(True)
        else:
            self.message.config(text=result.message, foreground=LEVEL_COLOR["warn"])
            Link(self.links, result.url).pack(side="left")
            self.set_status("The assistant can't continue for this console; follow the guide link.", "warn")
            self.enable_next(False)

    def on_next(self):
        self.wizard.show("drive")


class DrivePage(Page):
    name = "drive"
    title = "SD card"

    COLUMNS = (("drive", 120), ("label", 140), ("fs", 90), ("size", 80), ("status", 90))

    def build(self):
        heading(self.body, "Your SD card").pack(anchor="w")
        paragraph(self.body, "Put the 3DS SD card in this computer and pick it below. Only removable drives "
                             "are listed; system drives are never shown.").pack(anchor="w", pady=(PAD // 2, 4))
        top = ttk.Frame(self.body)
        top.pack(fill="x")
        self.tree = ttk.Treeview(top, columns=[c for c, _ in self.COLUMNS], show="headings", height=4,
                                 selectmode="browse")
        for (col, width), title in zip(self.COLUMNS, ("Drive", "Label", "File system", "Size", "Status")):
            self.tree.heading(col, text=title)
            self.tree.column(col, width=width, anchor="w")
        self.tree.pack(side="left", fill="x", expand=True)
        self.refresh_btn = ttk.Button(top, text="Refresh", command=self.refresh)
        self.refresh_btn.pack(side="left", padx=8, anchor="n")
        self.tree.bind("<<TreeviewSelect>>", lambda e: self._selected())

        self.checks = ChecksView(self.body)
        self.checks.pack(fill="x", pady=PAD // 2)
        self.prompt = paragraph(self.body, "")
        self.prompt.pack(anchor="w", pady=(PAD // 2, 2))
        self.label_var = tk.StringVar(value="")
        self.label_entry = ttk.Entry(self.body, textvariable=self.label_var, width=30)
        self.label_entry.pack(anchor="w")
        self.label_entry.bind("<Return>", lambda e: self.next_btn.invoke())
        self.label_var.trace_add("write", lambda *a: self._update_next())
        self.issues = ChecksView(self.body)
        self.issues.pack(fill="x", pady=(PAD, 0))

        self.items: list = []
        self.failures = 0
        self.confirmed_with_issues = False

    def enter(self, reconfirm: bool = False, **kw):
        # reconfirm: resuming the walkthrough in a new session; only the typed label is needed
        # (the card already has custom firmware by then, so skip the "already modded" check).
        self.reconfirm = reconfirm
        if reconfirm:
            self.set_status("Confirm the SD card again before the assistant writes to it.")
        self.refresh()

    def refresh(self):
        self.failures = 0
        self.confirmed_with_issues = False
        self.label_entry.config(state="normal")
        self.label_var.set("")
        self.issues.show([])
        self.checks.show([])
        self.prompt.config(text="")
        self.enable_next(False, "Next →")
        self.set_status("Looking for removable drives…")
        self.refresh_btn.config(state="disabled")
        self.wizard.run_bg(self.core.drives, self._fill, self._fill_failed)

    def _fill_failed(self, e):
        self.refresh_btn.config(state="normal")
        self.wizard.show_error(e)

    def _fill(self, items):
        self.refresh_btn.config(state="normal")
        self.items = items
        self.tree.delete(*self.tree.get_children())
        for i, (d, checks) in enumerate(items):
            worst = max((c.level for c in checks), key=LEVEL_RANK.get, default="ok")
            status = {"ok": "Ready", "info": "Ready", "warn": "Warning", "fail": "Can't use"}[worst]
            self.tree.insert("", "end", iid=str(i), values=(
                d.mount, d.label or "(none)", d.filesystem, f"{d.total_bytes / 1e9:.1f} GB", status))
        if not items:
            self.set_status("No removable drives found. Insert the SD card, then press Refresh.", "warn")
        else:
            self.set_status("Select your SD card.")
            if len(items) == 1:
                self.tree.selection_set("0")
                self._selected()

    def selected(self):
        sel = self.tree.selection()
        return self.items[int(sel[0])] if sel else (None, [])

    def _selected(self):
        drive, checks = self.selected()
        if drive is None:
            return
        self.checks.show([c for c in checks if c.level != "ok"] or checks)
        self.confirmed_with_issues = False
        self.issues.show([])
        if any(c.level == "fail" for c in checks):
            self.prompt.config(text="")
            self.set_status(f"{drive.mount} can't be used (see the problems above).", "fail")
        else:
            target = self.core.confirmation_target(drive)
            self.prompt.config(text=f"Type the drive label exactly to confirm: {target}")
            self.set_status("")
        self._update_next()

    def _usable(self) -> bool:
        drive, checks = self.selected()
        return drive is not None and not any(c.level == "fail" for c in checks)

    def _update_next(self):
        if self.confirmed_with_issues:
            return
        ok = self._usable() and bool(self.label_var.get().strip()) and self.failures < 3
        self.enable_next(ok)

    def on_next(self):
        import sdcard

        if self.confirmed_with_issues:
            self.wizard.show("downloads", card_issues=self.card_issues)
            return
        drive, _ = self.selected()
        try:
            self.core.confirm(drive, self.label_var.get())
        except sdcard.ConfirmationError as e:
            self.failures += 1
            if self.failures >= 3:
                self.label_entry.config(state="disabled")
                self.enable_next(False)
                self.set_status("Drive not confirmed three times. Nothing was written. "
                                "Press Refresh to start again.", "fail")
            else:
                self.set_status(str(e), "fail")
            return
        if getattr(self, "reconfirm", False):
            self.wizard.show("walk")
            return
        self.enable_next(False)
        self.set_status("Reading the card…")
        self.wizard.run_bg(self.core.card_status, self._checked)

    def _checked(self, result):
        already_modded, issues = result
        if already_modded:
            self.wizard.show("cfw")
            return
        self.card_issues = [i for i in issues if i.level in ("warn", "fail")]
        if self.card_issues:
            self.issues.show(self.card_issues)
            self.confirmed_with_issues = True
            self.set_status("Check the notes about the card above before continuing.", "warn")
            self.enable_next(True, "Continue →")
            return
        self.wizard.show("downloads", card_issues=[])


class CfwPage(Page):
    name = "cfw"
    title = "Custom firmware found"

    def build(self):
        from guide_parser import page_url

        heading(self.body, "This card already has custom firmware").pack(anchor="w")
        top = ttk.Frame(self.body)
        top.pack(fill="x", pady=(4, 0))
        paragraph(top, "It has luma/ and boot.firm. Follow the guide's Checking for CFW steps:").pack(side="left")
        Link(top, page_url("checking-for-cfw"), "Checking for CFW").pack(side="left", padx=6)
        self.steps = ScrollText(self.body, height=6)
        self.steps.pack(fill="x", pady=4)

        row = ttk.Frame(self.body)
        row.pack(anchor="w", fill="x")
        ttk.Label(row, text="Luma3DS version shown (or type home):").pack(side="left")
        self.version_var = tk.StringVar(value="")
        entry = ttk.Entry(row, textvariable=self.version_var, width=12)
        entry.pack(side="left", padx=6)
        entry.bind("<Return>", lambda e: self.on_check())
        self.check_btn = ttk.Button(row, text="Check", command=self.on_check)
        self.check_btn.pack(side="left")

        self.result = ttk.Frame(self.body)
        self.result.pack(fill="x", pady=4)
        self.message = paragraph(self.result, "")
        self.message.pack(anchor="w")
        self.link_box = ttk.Frame(self.result)
        self.link_box.pack(anchor="w")

        self.update_frame = ttk.Frame(self.body)
        self.gm9_var = tk.BooleanVar(value=False)
        self.gm9_check = ttk.Checkbutton(self.update_frame, text="Also update GodMode9", variable=self.gm9_var)
        self.prepare_btn = ttk.Button(self.update_frame, text="Prepare update", command=self.on_prepare)
        self.prepare_btn.pack(anchor="w", pady=(0, 4))
        self.plan_view = PlanView(self.update_frame, height=5)
        self.plan_view.pack(fill="x")
        self.plan_note = paragraph(self.update_frame, "")
        self.plan_note.pack(anchor="w")
        wrow = ttk.Frame(self.update_frame)
        wrow.pack(anchor="w", pady=4)
        ttk.Label(wrow, text="Type WRITE to allow copying:").pack(side="left")
        self.write_var = tk.StringVar(value="")
        self.write_entry = ttk.Entry(wrow, textvariable=self.write_var, width=10, state="disabled")
        self.write_entry.pack(side="left", padx=6)
        self.copy_btn = ttk.Button(wrow, text="Copy files", command=self.on_copy, state="disabled")
        self.copy_btn.pack(side="left")
        self.write_var.trace_add("write", lambda *a: self._update_copy())
        self.progress = ProgressRow(self.update_frame, "")
        self.progress.pack(fill="x")
        self.checks = ChecksView(self.update_frame)
        self.checks.pack(fill="x", pady=4)

        self.plans: list = []
        self.done_message = ""

    def enter(self, **kw):
        steps, warnings = self.core.cfw_steps()
        chunks = [(f"{i}. {s}\n", "") for i, s in enumerate(steps, 1)]
        chunks += [(f"⚠ {w}\n", "warning") for w in warnings]
        self.steps.set(chunks)
        self._reset_update()
        self.message.config(text="")
        for w in self.link_box.winfo_children():
            w.destroy()
        self.enable_next(False, "Finish")
        self.set_status("Hold (Select) while powering on the console and read the Luma3DS version.")

    def _reset_update(self):
        self.update_frame.pack_forget()
        self.gm9_check.pack_forget()
        self.gm9_var.set(False)
        self.plans = []
        self.plan_view.tree.delete(*self.plan_view.tree.get_children())
        self.plan_note.config(text="")
        self.write_var.set("")
        self.write_entry.config(state="disabled")
        self.copy_btn.config(state="disabled")
        self.prepare_btn.config(state="normal")
        self.progress.update_progress(0, 1, "")
        self.checks.show([])

    def on_check(self):
        from guide_parser import page_url

        self._reset_update()
        slug, message, updatable = self.core.route_cfw(self.version_var.get())
        self.message.config(text=message)
        for w in self.link_box.winfo_children():
            w.destroy()
        url = page_url(slug)
        Link(self.link_box, url).pack(side="left")
        if updatable:
            self.update_frame.pack(fill="both", expand=True, pady=(PAD // 2, 0))
            if self.core.gm9_installed():
                self.gm9_check.pack(anchor="w", before=self.prepare_btn)
            self.enable_next(False)
            self.set_status("The assistant can do this update for you.")
        else:
            self.done_message = f"Follow the guide from here: {url}"
            self.enable_next(True, "Finish")
            self.set_status("Follow the guide page linked above.")

    def on_prepare(self):
        self.prepare_btn.config(state="disabled")
        self.set_status("Downloading and checking the update files…")
        with_gm9 = self.gm9_var.get()  # Tk variables may only be read on the UI thread
        self.wizard.run_bg(lambda: self.core.plan_updates(with_gm9), self._planned, self._prepare_failed)

    def _prepare_failed(self, e):
        self.prepare_btn.config(state="normal")
        self.set_status("Couldn't prepare the update.", "fail")
        self.wizard.show_error(e)

    def _planned(self, plans):
        self.plans = plans
        ops = [op for p in plans if p.plan is not None for op in p.plan.ops]
        self.plan_view.show(types.SimpleNamespace(ops=ops))
        not_ready = [p for p in plans if not p.ready]
        if not_ready:
            self.plan_note.config(text="\n".join(p.message or f"Follow the guide instead: {p.url}"
                                                 for p in not_ready), foreground=LEVEL_COLOR["fail"])
            self.set_status("The assistant can't do every step of this update; nothing will be written.", "fail")
            return
        self.plan_note.config(text="Dry run: these are the changes. Nothing has been written yet.", foreground="")
        self.write_entry.config(state="normal")
        self.set_status("Type WRITE to allow copying these files.")

    def _update_copy(self):
        ready = bool(self.plans) and all(p.ready for p in self.plans)
        self.copy_btn.config(state="normal" if ready and self.write_var.get().strip() == "WRITE" else "disabled")

    def on_copy(self):
        self.copy_btn.config(state="disabled")
        self.write_entry.config(state="disabled")
        self.set_status("Copying…")
        cb = self.wizard.progress_cb(lambda done, total, dest: self.progress.update_progress(
            done, total, f"Copied {done} of {total}: /{dest}"))
        self.wizard.run_bg(lambda: self.core.write_updates(self.plans, cb), self._written, self._copy_failed)

    def _copy_failed(self, e):
        self.set_status("Copying stopped.", "fail")
        self.wizard.show_error(e)

    def _written(self, checks):
        self.checks.show(checks)
        if any(c.level == "fail" for c in checks):
            self.set_status("Some files didn't read back correctly.", "fail")
            return
        names = " and ".join(p.name.removesuffix(" update") for p in self.plans)
        self.done_message = (f"{names} updated. Put the SD card back in the console and power it on; "
                             "Luma3DS will use the new files.")
        self.set_status(self.done_message, "ok")
        self.enable_next(True, "Finish")

    def on_next(self):
        self.wizard.show("done", message=self.done_message)


PAGES = [LoadPage, ResumePage, ConsolePage, DrivePage, CfwPage]
