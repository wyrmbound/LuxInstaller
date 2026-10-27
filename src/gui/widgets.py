"""Shared building blocks for the GUI pages."""
from __future__ import annotations

import tkinter as tk
import webbrowser
from tkinter import ttk

LEVEL_ICON = {"ok": "✔", "info": "ℹ", "warn": "⚠", "fail": "✖"}
LEVEL_COLOR = {"ok": "#1b7f3b", "info": "#35618f", "warn": "#9a6200", "fail": "#b3261e"}
PAD = 12


class Page(ttk.Frame):
    """One wizard page. Subclasses set `name` and `title`, build widgets in build(), and
    refresh them in enter(**kw), which the wizard calls each time the page is shown."""

    name = ""
    title = ""

    def __init__(self, wizard):
        super().__init__(wizard.content, padding=PAD)
        self.wizard = wizard
        self.core = wizard.core
        self.body = ttk.Frame(self)
        self.body.pack(fill="both", expand=True)
        nav = ttk.Frame(self)
        nav.pack(fill="x", pady=(PAD, 0))
        self.status = ttk.Label(nav, text="", foreground=LEVEL_COLOR["info"], wraplength=600, justify="left")
        self.status.pack(side="left", fill="x", expand=True)
        self.next_btn = ttk.Button(nav, text="Next →", command=self.on_next, state="disabled")
        self.next_btn.pack(side="right")
        self.extra_nav = ttk.Frame(nav)  # pages may add buttons left of Next
        self.extra_nav.pack(side="right", padx=(0, 8))
        self.build()

    def build(self) -> None:
        pass

    def enter(self, **kw) -> None:
        pass

    def on_next(self) -> None:
        pass

    def set_status(self, text: str, level: str = "info") -> None:
        self.status.config(text=text, foreground=LEVEL_COLOR.get(level, LEVEL_COLOR["info"]))

    def enable_next(self, on: bool = True, text: str | None = None) -> None:
        self.next_btn.config(state="normal" if on else "disabled")
        if text:
            self.next_btn.config(text=text)


class Link(ttk.Label):
    """A clickable URL."""

    def __init__(self, master, url: str, text: str | None = None, **kw):
        super().__init__(master, text=text or url, foreground="#1a5fb4", cursor="hand2", **kw)
        self.url = url
        self.bind("<Button-1>", lambda e: webbrowser.open(self.url))


class ScrollText(ttk.Frame):
    """Read-only text with a scrollbar and a few tags: h (bold), warning, danger, done, muted, mono."""

    def __init__(self, master, height: int = 12, **kw):
        super().__init__(master)
        self.text = tk.Text(self, wrap="word", height=height, relief="flat", padx=8, pady=6,
                            background="#fbfbfb", **kw)
        sb = ttk.Scrollbar(self, command=self.text.yview)
        self.text.config(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.text.pack(side="left", fill="both", expand=True)
        self.text.tag_configure("h", font=("Segoe UI", 10, "bold"))
        self.text.tag_configure("warning", foreground=LEVEL_COLOR["warn"])
        self.text.tag_configure("danger", foreground=LEVEL_COLOR["fail"], font=("Segoe UI", 9, "bold"))
        self.text.tag_configure("done", foreground=LEVEL_COLOR["ok"])
        self.text.tag_configure("muted", foreground="#666666")
        self.text.tag_configure("mono", font=("Consolas", 9))
        self.text.config(state="disabled")

    def set(self, chunks: list[tuple[str, str | tuple]]) -> None:
        """Replace the text with [(text, tag-or-tags)]."""
        self.text.config(state="normal")
        self.text.delete("1.0", "end")
        for chunk, tags in chunks:
            self.text.insert("end", chunk, tags)
        self.text.config(state="disabled")

    def get(self) -> str:
        return self.text.get("1.0", "end-1c")


class ChecksView(ttk.Frame):
    """A list of sdcard.Check results with icons; failing ones show their guide link."""

    def __init__(self, master):
        super().__init__(master)

    def show(self, checks: list) -> None:
        from guide_parser import page_url

        for w in self.winfo_children():
            w.destroy()
        for c in checks:
            row = ttk.Frame(self)
            row.pack(fill="x", anchor="w")
            ttk.Label(row, text=LEVEL_ICON.get(c.level, "?"), foreground=LEVEL_COLOR.get(c.level),
                      width=2).pack(side="left", anchor="n")
            ttk.Label(row, text=c.message, wraplength=640, justify="left").pack(side="left", anchor="w")
            if c.guide and c.level in ("warn", "fail"):
                Link(row, page_url(c.guide), "guide").pack(side="left", padx=6, anchor="n")

    def levels(self) -> list[str]:
        return [w.cget("text") for row in self.winfo_children() for w in row.winfo_children()[:1]]


class ProgressRow(ttk.Frame):
    """A label over a determinate progress bar."""

    def __init__(self, master, text: str = ""):
        super().__init__(master)
        self.label = ttk.Label(self, text=text)
        self.label.pack(fill="x", anchor="w")
        self.bar = ttk.Progressbar(self, mode="determinate", maximum=1000)
        self.bar.pack(fill="x")

    def update_progress(self, done: float, total: float, text: str | None = None) -> None:
        self.bar["value"] = 0 if not total else min(1000, done * 1000 / total)
        if text is not None:
            self.label.config(text=text)

    @property
    def fraction(self) -> float:
        return float(self.bar["value"]) / 1000


class PlanView(ttk.Frame):
    """A stager.StagePlan as a table: action, path on the SD card, source."""

    def __init__(self, master, height: int = 8):
        super().__init__(master)
        self.tree = ttk.Treeview(self, columns=("action", "path", "from"), show="headings", height=height)
        for col, w in (("action", 90), ("path", 360), ("from", 220)):
            self.tree.heading(col, text=col.capitalize())
            self.tree.column(col, width=w, anchor="w")
        sb = ttk.Scrollbar(self, command=self.tree.yview)
        self.tree.config(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.tree.pack(side="left", fill="both", expand=True)

    def show(self, plan) -> None:
        from pathlib import Path

        self.tree.delete(*self.tree.get_children())
        for op in plan.ops:
            if op.kind == "mkdir":
                action = "Exists" if op.overwrite else "New folder"
                origin = ""
            else:
                action = "Overwrite" if op.overwrite else "New"
                origin = Path(op.archive).name if op.archive else Path(op.source).name
            self.tree.insert("", "end", values=(action, "/" + op.dest + ("/" if op.kind == "mkdir" else ""), origin))

    def rows(self) -> list[tuple]:
        return [tuple(self.tree.item(i, "values")) for i in self.tree.get_children()]


def heading(master, text: str) -> ttk.Label:
    return ttk.Label(master, text=text, font=("Segoe UI", 14, "bold"))


def paragraph(master, text: str, **kw) -> ttk.Label:
    return ttk.Label(master, text=text, wraplength=700, justify="left", **kw)
