"""Downloads, Backup and Done pages."""
from __future__ import annotations

import tkinter as tk
from tkinter import filedialog, ttk

from gui.widgets import LEVEL_COLOR, PAD, Link, Page, ProgressRow, heading, paragraph


def _size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return str(n)


class DownloadsPage(Page):
    name = "downloads"
    title = "Downloads"

    def build(self) -> None:
        heading(self.body, "Files from the guide").pack(anchor="w")
        paragraph(self.body, "These are the files the guide's \"What You Need\" lists for your method and for "
                  "Finalizing Setup. Each is checked against GitHub's published SHA-256 when there is one; "
                  "older files without one are recorded on first download, and any later change stops everything."
                  ).pack(anchor="w", pady=(4, PAD // 2))
        self.build_luma_choice()
        self.tree = ttk.Treeview(self.body, columns=("name", "file", "version", "size", "check", "state"),
                                 show="headings", height=6)
        for col, text, w in (("name", "Item", 150), ("file", "File", 220), ("version", "Version", 110),
                             ("size", "Size", 70), ("check", "Checksum", 150), ("state", "", 90)):
            self.tree.heading(col, text=text)
            self.tree.column(col, width=w, anchor="w")
        self.tree.pack(fill="x")
        self.extra = ttk.Frame(self.body)  # prerequisites and hand-downloaded files
        self.extra.pack(fill="x", pady=(PAD // 2, 0))
        self.overall = ProgressRow(self.body, "")
        self.current = ProgressRow(self.body, "")
        self.download_btn = ttk.Button(self.extra_nav, text="Download and verify", command=self.start_download)
        self.download_btn.pack(side="right")
        self.plan = None
        self.prereq_vars: dict[str, tk.BooleanVar] = {}
        self.manual_files: dict[str, str] = {}
        self.manual_labels: dict[str, ttk.Label] = {}

    # --- which Luma -----------------------------------------------------------------------

    def build_luma_choice(self) -> None:
        box = ttk.LabelFrame(self.body, text="Which Luma3DS do you want?", padding=6)
        box.pack(fill="x", pady=(0, PAD // 2))
        self.luma_kind = tk.StringVar(value="official")
        ttk.Radiobutton(box, text="Luma3DS, the official release (recommended)", value="official",
                        variable=self.luma_kind, command=self.luma_changed).grid(row=0, column=0, columnspan=3, sticky="w")
        ttk.Radiobutton(box, text="A fork's GitHub release:", value="github", variable=self.luma_kind,
                        command=self.luma_changed).grid(row=1, column=0, sticky="w")
        self.luma_repo = tk.StringVar(value="")
        self.luma_repo_entry = ttk.Entry(box, textvariable=self.luma_repo, width=32)
        self.luma_repo_entry.grid(row=1, column=1, sticky="w", padx=4)
        ttk.Label(box, text="owner/name, e.g. wyrmbound/LuxiOS", foreground="#666666").grid(row=1, column=2, sticky="w")
        ttk.Radiobutton(box, text="A build file on this computer:", value="file", variable=self.luma_kind,
                        command=self.luma_changed).grid(row=2, column=0, sticky="w")
        self.luma_file = tk.StringVar(value="")
        ttk.Button(box, text="Choose .zip or boot.firm...", command=self.choose_luma_file).grid(row=2, column=1, sticky="w", padx=4)
        ttk.Label(box, textvariable=self.luma_file, foreground="#444444").grid(row=2, column=2, sticky="w")
        self.luma_note = paragraph(box, "", foreground=LEVEL_COLOR["warn"])
        self.luma_note.grid(row=3, column=0, columnspan=3, sticky="w", pady=(4, 0))
        self.luma_repo.trace_add("write", lambda *a: self.luma_changed())

    def choose_luma_file(self) -> None:
        path = filedialog.askopenfilename(parent=self, title="Choose a Luma3DS build",
                                          filetypes=[("Luma3DS build", "*.zip *.firm"), ("All files", "*.*")])
        if path:
            self.set_luma_file(path)

    def set_luma_file(self, path: str) -> None:
        self.luma_file.set(path)
        self.luma_kind.set("file")
        self.luma_changed()

    def luma_changed(self) -> None:
        kind = self.luma_kind.get()
        if kind == "official":
            self.luma_note.config(text="")
        else:
            self.luma_note.config(text="Setup installs official Luma3DS first and finishes with it, NAND backup "
                                       "included. Your build replaces boot.firm only at the very end, and you can "
                                       "switch back with one click if it doesn't boot.")
        if self.plan is not None:
            self.refresh_buttons()

    def luma_choice(self):
        """The LumaChoice from the controls, or raise LumaBuildError with what's missing."""
        import luma_build

        kind = self.luma_kind.get()
        if kind == "github":
            return luma_build.LumaChoice("github", repo=luma_build.parse_repo(self.luma_repo.get()))
        if kind == "file":
            if not self.luma_file.get():
                raise luma_build.LumaBuildError("Choose the build file first.")
            return luma_build.LumaChoice("file", path=self.luma_file.get())
        return luma_build.LumaChoice("official")

    def enter(self, card_issues: list | None = None, **kw) -> None:
        from gui.widgets import ChecksView

        current = self.core.luma_choice
        self.luma_kind.set(current.kind)
        self.luma_repo.set(current.repo)
        self.luma_file.set(current.path)
        self.luma_changed()
        self.enable_next(False)
        self.download_btn.config(state="disabled")
        self.tree.delete(*self.tree.get_children())
        for w in self.extra.winfo_children():
            w.destroy()
        if card_issues:  # e.g. no Nintendo 3DS folder yet; the CLI shows these too
            box = ttk.LabelFrame(self.extra, text="About your SD card", padding=6)
            box.pack(fill="x", pady=(0, 4))
            view = ChecksView(box)
            view.pack(fill="x")
            view.show(card_issues)
        self.overall.pack_forget()
        self.current.pack_forget()
        self.set_status("Checking the latest versions on GitHub...")
        self.wizard.run_bg(self.core.plan_downloads, self.show_plan, self.failed)

    def failed(self, e) -> None:
        self.set_status(f"Couldn't prepare the downloads: {getattr(e, 'reason', None) or e}", "fail")
        self.download_btn.config(state="normal", text="Try again", command=self.enter)
        self.wizard.show_error(e)

    def show_plan(self, plan) -> None:
        self.plan = plan
        for r in plan.resolved:
            check = "published SHA-256" if r.digest else "none published (recorded)"
            self.tree.insert("", "end", iid=r.spec.name, values=(
                r.spec.name, r.asset_name, r.tag, _size(r.size), check, "waiting"))
        prereqs = list(getattr(plan, "prerequisites", [])) + [s for s in plan.manual if s.kind == "prerequisite"]
        self.prereq_vars = {}
        for spec in prereqs:
            var = tk.BooleanVar(value=False)
            self.prereq_vars[spec.name] = var
            row = ttk.Frame(self.extra)
            row.pack(fill="x", anchor="w", pady=2)
            ttk.Checkbutton(row, text=f"{spec.name} is installed on this computer", variable=var,
                            command=self.refresh_buttons).pack(side="left")
            if spec.url:
                Link(row, spec.url, "get it").pack(side="left", padx=6)
            paragraph(self.extra, spec.requirement.replace("**", ""), foreground="#555555").pack(anchor="w", padx=24)
        self.manual_files, self.manual_labels = {}, {}
        for spec in plan.manual:
            if spec.kind == "prerequisite":
                continue
            box = ttk.LabelFrame(self.extra, text=f"Download this one yourself: {spec.name}", padding=6)
            box.pack(fill="x", pady=4)
            paragraph(box, spec.requirement.replace("**", "")).pack(anchor="w")
            row = ttk.Frame(box)
            row.pack(fill="x", pady=(4, 0))
            if spec.url:
                Link(row, spec.url).pack(side="left")
            ttk.Button(row, text="Choose the downloaded file...",
                       command=lambda s=spec: self.choose_file(s.name)).pack(side="right")
            self.manual_labels[spec.name] = ttk.Label(box, text="No file chosen yet", foreground="#666666")
            self.manual_labels[spec.name].pack(anchor="w")
        self.set_status(f"{len(plan.resolved)} files to download.")
        self.download_btn.config(text="Download and verify", command=self.start_download)
        self.refresh_buttons()

    def choose_file(self, name: str) -> None:
        path = filedialog.askopenfilename(parent=self, title=f"Choose the {name} file")
        if path:
            self.set_manual_file(name, path)

    def set_manual_file(self, name: str, path: str) -> None:
        self.manual_files[name] = path
        self.manual_labels[name].config(text=path, foreground="#000000")
        self.refresh_buttons()

    def ready(self) -> bool:
        manual_needed = [s.name for s in self.plan.manual if s.kind != "prerequisite"] if self.plan else []
        return (self.plan is not None and all(v.get() for v in self.prereq_vars.values())
                and all(n in self.manual_files for n in manual_needed))

    def refresh_buttons(self) -> None:
        self.download_btn.config(state="normal" if self.ready() else "disabled")
        missing = [n for n, v in self.prereq_vars.items() if not v.get()]
        if missing:
            self.set_status(f"Install {', '.join(missing)} first, then tick the box (the guide needs it).", "warn")

    def start_download(self) -> None:
        import luma_build

        if not self.ready():
            return
        try:
            self.core.set_luma_choice(self.luma_choice())
        except luma_build.LumaBuildError as e:
            self.set_status(str(e), "fail")
            return
        self.download_btn.config(state="disabled")
        self.overall.pack(fill="x", pady=(PAD // 2, 0))
        self.current.pack(fill="x", pady=(4, 0))
        total = sum(r.size for r in self.plan.resolved) or 1
        done_by_name: dict[str, int] = {}

        def on_progress(name, done, size):
            done_by_name[name] = done
            self.current.update_progress(done, size, f"{name}: {_size(done)} of {_size(size)}")
            self.overall.update_progress(sum(done_by_name.values()), total,
                                         f"All files: {_size(sum(done_by_name.values()))} of {_size(total)}")
            if self.tree.exists(name):
                self.tree.set(name, "state", "downloading")

        progress = self.wizard.progress_cb(on_progress)
        self.set_status("Downloading and checking...")
        self.wizard.run_bg(lambda: self.core.download(self.plan, dict(self.manual_files), progress),
                           self.downloaded, self.download_failed)

    def downloaded(self, got) -> None:
        for d in got:
            if self.tree.exists(d.name):
                label = {"published": "✔ matches", "first-use": "✔ recorded",
                         "recorded": "✔ matches record"}.get(d.verified, "✔ " + d.verified)
                self.tree.set(d.name, "state", label + (" (cached)" if d.from_cache else ""))
        self.overall.update_progress(1, 1, "All files downloaded and verified.")
        luma = self.core.state.luma or {}
        extra = f" {self.core.luma_choice.label} {luma.get('version', '')} is ready too." if luma.get("firm") else ""
        self.set_status("Everything downloaded and verified. Nothing has been written to the SD card yet." + extra, "ok")
        self.enable_next(True)
        if self.core.walk_mode:  # straight on to the walkthrough
            self.wizard.after(600, self.on_next)

    def download_failed(self, e) -> None:
        self.set_status(f"Stopped: {getattr(e, 'reason', None) or e}", "fail")
        self.download_btn.config(state="normal", text="Try again")
        self.wizard.show_error(e)

    def on_next(self) -> None:
        if self.wizard.current is not self:
            return
        if self.core.walk_mode:
            self.wizard.go_walk()
            return
        if self.core.args.dry_run:
            index = self.core.first_copy_stage()
            if index is None:
                self.set_status("Dry run: there are no file copies left to show.", "info")
                return
        else:
            index = self.core.first_pending()
        self.wizard.show("stage", index=index)


class BackupPage(Page):
    name = "backup"
    title = "Backup"

    def build(self) -> None:
        heading(self.body, "Copy your backups to this computer").pack(anchor="w")
        paragraph(self.body, "The finalize script saved your NAND backup and essential.exefs on the SD card. "
                  "These are what can recover your console from a brick. The assistant copies them to this "
                  "computer and checks every copy, including the NAND backup against the console's own .sha file."
                  ).pack(anchor="w", pady=(4, PAD // 2))
        self.where = ttk.Label(self.body, text="")
        self.where.pack(anchor="w")
        self.progress = ProgressRow(self.body, "")
        self.alarm = tk.Label(self.body, text="", fg="white", bg=LEVEL_COLOR["fail"], font=("Segoe UI", 10, "bold"),
                              justify="left", wraplength=760, padx=10, pady=8)
        self.result = ttk.Frame(self.body)
        self.result.pack(fill="x", pady=(PAD // 2, 0))
        self.copy_btn = ttk.Button(self.extra_nav, text="Copy backups", command=self.start)
        self.copy_btn.pack(side="right")
        self.dest = None

    def enter(self, **kw) -> None:
        self.stage = self.core.stages()[-1]
        self.core.begin(self.stage)
        self.where.config(text=f"Power off the console, put the SD card in this computer, then click Copy backups.\n"
                               f"Destination: {self.core.args.backup_dir}")
        self.alarm.pack_forget()
        self.progress.pack_forget()
        for w in self.result.winfo_children():
            w.destroy()
        self.copy_btn.config(state="normal")
        self.enable_next(False, "Finish →")
        self.set_status("")

    def start(self) -> None:
        self.copy_btn.config(state="disabled")
        self.alarm.pack_forget()
        self.progress.pack(fill="x", pady=(PAD // 2, 0))
        progress = self.wizard.progress_cb(
            lambda done, total, label: self.progress.update_progress(done, total, f"{label}  ({done * 100 // max(total, 1)}%)"))
        self.set_status("Copying and checking...")
        self.wizard.run_bg(lambda: self.core.backup(progress), self.done, self.failed)

    def done(self, dest) -> None:
        self.dest = dest
        self.progress.update_progress(1, 1, "Done: every copy matches.")
        ttk.Label(self.result, text=f"Backups copied and verified: {dest}", foreground=LEVEL_COLOR["ok"]).pack(anchor="w")
        for p in sorted(dest.iterdir()):
            ttk.Label(self.result, text=f"   {p.name}   {_size(p.stat().st_size)}").pack(anchor="w")
        paragraph(self.result, "Copy this folder to at least one more place too (cloud storage, an external drive). "
                  "The guide's last step: you may now delete the two SysNAND files from /gm9/backups/ on the SD "
                  "card (keep essential.exefs). The assistant doesn't delete anything from your card.",
                  foreground="#444444").pack(anchor="w", pady=(PAD // 2, 0))
        self.set_status("Backups are safe.", "ok")
        self.enable_next(True)

    def failed(self, e) -> None:
        self.alarm.config(text=str(e))
        self.alarm.pack(fill="x", pady=(PAD // 2, 0))
        self.set_status("The backup is not complete. Do not continue until this is fixed.", "fail")
        self.copy_btn.config(state="normal", text="Try again")
        self.wizard.show_error(e, "NAND backup incomplete")

    def on_next(self) -> None:
        self.core.complete(self.stage)
        self.wizard.show("done")


class DonePage(Page):
    name = "done"
    title = "Done"

    def build(self) -> None:
        self.head = heading(self.body, "Setup complete!")
        self.head.pack(anchor="w")
        self.text = paragraph(self.body, "")
        self.text.pack(anchor="w", pady=(PAD // 2, 0))
        self.link = Link(self.body, "https://3ds.hacks.guide/", "3ds.hacks.guide")
        self.link.pack(anchor="w", pady=(PAD, 0))
        self.restore_btn = ttk.Button(self.body, text="Put official Luma3DS back", command=self.restore)
        self.restore_msg = paragraph(self.body, "")
        self.restore_note = paragraph(self.body, "If your build doesn't start (white light, or a black screen), put "
                                      "the SD card in this computer and click the button above.", foreground="#555555")

    def enter(self, message: str | None = None, **kw) -> None:
        if message:
            self.head.config(text="Done")
            self.text.config(text=message)
        else:
            backups = self.core.state.backup_dir
            self.head.config(text="Setup complete!")
            cfw = self.core.luma_choice.label if self.core.luma_choice.custom else "Luma3DS"
            self.text.config(text=f"Your console is running {cfw} custom firmware.\n\n"
                                  f"Your backups are at:\n{backups}\n\nKeep a second copy somewhere safe.")
        if self.core.luma_choice.custom:
            self.restore_btn.pack(anchor="w", pady=(PAD, 0))
            self.restore_msg.pack(anchor="w")
            self.restore_note.pack(anchor="w")
        self.enable_next(True, "Close")

    def restore(self) -> None:
        self.restore_btn.config(state="disabled")
        self.restore_msg.config(text="Putting official Luma3DS back...")

        def done(result):
            self.restore_msg.config(text=result.message, foreground=LEVEL_COLOR["ok" if result.ok else "fail"])
            self.restore_btn.config(state="normal")

        self.wizard.run_bg(self.core.restore_official, done)

    def on_next(self) -> None:
        self.wizard.destroy()


PAGES = [DownloadsPage, BackupPage, DonePage]
