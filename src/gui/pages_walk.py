"""The baby-step walkthrough page: one instruction at a time, the PC side done automatically.

"you" steps show the button presses, what should happen, and ask "Did that happen?".
"auto" steps run Core.walk_action in the background and move on by themselves when they work.
Between steps that need the SD card in a different place, a short "Move the SD card" screen
comes first.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from gui.widgets import LEVEL_COLOR, PAD, Link, Page, ProgressRow

TITLE_FONT = ("Segoe UI", 18, "bold")
STEP_FONT = ("Segoe UI", 13)
BODY_FONT = ("Segoe UI", 11)
BUTTON_FONT = ("Segoe UI", 12, "bold")
EXPECT_BG = "#e8f1fb"
BANNER_BG = "#fff4c2"
AUTO_PAUSE_MS = 800

MOVE_TO = {
    "console": ("Move the SD card to the console", [
        "Take the SD card out of this computer (eject it first if Windows asks)",
        "Put it in the console",
    ]),
    "pc": ("Move the SD card to this computer", [
        "Power off the console (hold the power button, then tap Power off)",
        "Take the SD card out of the console",
        "Put it in this computer",
    ]),
}
CHECK_WHERE = {
    "console": ("Put the SD card in the console", ["Make sure the SD card is in the console"]),
    "pc": ("Put the SD card in this computer", ["Make sure the SD card is in this computer"]),
}


def mentions_sd_card(step) -> bool:
    return any("sd card" in line.casefold() for line in step.do)


def guide_url(step) -> str | None:
    from guide_parser import page_url

    slug = step.guide[0] if step.guide else ""
    return page_url(slug) if slug else None


class WalkPage(Page):
    name = "walk"
    title = "Walkthrough"

    def build(self) -> None:
        self.next_btn.pack_forget()  # this page has its own big buttons
        top = ttk.Frame(self.body)
        top.pack(fill="x")
        self.counter = ttk.Label(top, text="", font=BODY_FONT, foreground="#555555")
        self.counter.pack(side="left")
        self.overall = ttk.Progressbar(self.body, mode="determinate", maximum=1000)
        self.overall.pack(fill="x", pady=(4, 0))
        self.banner = tk.Label(self.body, text="The guide has changed since these steps were written — "
                               "if something looks different, use the guide link",
                               bg=BANNER_BG, fg="#5c4700", anchor="w", padx=8, pady=3, font=("Segoe UI", 9))
        self.area = ttk.Frame(self.body)
        self.area.pack(fill="both", expand=True, pady=(PAD, 0))
        self.steps: list = []
        self.index = 0
        self.current_id: str | None = None  # step id on screen, or "move" for the SD card screen
        self.primary = None  # what Enter does
        self.gen = 0
        self._reset_widgets()
        self.wizard.bind("<Return>", self._on_return, add="+")

    def _reset_widgets(self) -> None:
        self.yes_btn = self.no_btn = self.retry_btn = self.again_btn = None
        self.back_btn = self.move_btn = self.next_done_btn = None
        self.help_frame = self.help_text = self.help_link = None
        self.progress = self.result_label = None

    # --- navigation --------------------------------------------------------------------

    def enter(self, **kw) -> None:
        self.steps = list(self.core.walk_steps())
        drift = self.core.walk_drift()
        if drift:
            self.banner.pack(fill="x", pady=(6, 0), before=self.area)
        else:
            self.banner.pack_forget()
        first = next((i for i, s in enumerate(self.steps) if not self.core.walk_done(s.id)), None)
        if first is None:
            self.wizard.show("done")
            return
        step = self.steps[first]
        if mentions_sd_card(step):
            self.show_step(first)
        else:
            title, lines = CHECK_WHERE.get(step.where, CHECK_WHERE["console"])
            self._show_move(first, title, lines)

    def go(self, index: int, came_from: int | None = None) -> None:
        if index >= len(self.steps):
            self.wizard.show("done")
            return
        step = self.steps[index]
        if came_from is not None and self.steps[came_from].where != step.where and not mentions_sd_card(step):
            title, lines = MOVE_TO.get(step.where, MOVE_TO["console"])
            self._show_move(index, title, lines)
        else:
            self.show_step(index)

    def _advance(self, step) -> None:
        self.core.walk_mark(step.id)
        self.go(self.index + 1, came_from=self.index)

    def _on_return(self, event=None) -> None:
        if self.wizard.current is self and self.primary is not None:
            target = self.primary
            if isinstance(target, (ttk.Button, tk.Button)):
                if str(target["state"]) != "disabled":
                    target.invoke()
            else:
                target()

    # --- rendering ---------------------------------------------------------------------

    def _clear(self) -> None:
        self.gen += 1
        for w in self.area.winfo_children():
            w.destroy()
        self._reset_widgets()
        self.primary = None
        self.set_status("")

    def _header(self, index: int) -> None:
        n = len(self.steps)
        self.counter.config(text=f"Step {index + 1} of {n}")
        self.overall["value"] = 1000 * index / max(n, 1)

    def _big_button(self, master, text: str, command, primary: bool = False) -> tk.Button:
        btn = tk.Button(master, text=text, command=command, font=BUTTON_FONT, padx=16, pady=6,
                        bg="#1a5fb4" if primary else "#e6e6e6", fg="white" if primary else "#222222",
                        activebackground="#174f96" if primary else "#d6d6d6", relief="flat", cursor="hand2")
        return btn

    def _show_move(self, index: int, title: str, lines: list[str]) -> None:
        self._clear()
        self._header(index)
        self.index = index
        self.current_id = "move"
        ttk.Label(self.area, text=title, font=TITLE_FONT).pack(anchor="w")
        steps = ttk.Frame(self.area)
        steps.pack(anchor="w", pady=(PAD, 0))
        for n, line in enumerate(lines, 1):
            ttk.Label(steps, text=f"{n}.  {line}", font=STEP_FONT, wraplength=760, justify="left").pack(
                anchor="w", pady=2)
        row = ttk.Frame(self.area)
        row.pack(anchor="w", pady=(PAD * 2, 0))
        self.move_btn = self._big_button(row, "Done", lambda: self.show_step(index), primary=True)
        self.move_btn.pack(side="left")
        self.primary = self.move_btn

    def show_step(self, index: int) -> None:
        self._clear()
        self._header(index)
        self.index = index
        step = self.steps[index]
        self.current_id = step.id
        ttk.Label(self.area, text=step.title, font=TITLE_FONT, wraplength=780, justify="left").pack(anchor="w")
        if step.kind == "auto":
            self._render_auto(step)
        else:
            self._render_you(step)
        if index > 0:
            nav = ttk.Frame(self.area)
            nav.pack(side="bottom", anchor="w", pady=(PAD, 0))
            self.back_btn = ttk.Button(nav, text="← Back", command=lambda: self.show_step(index - 1))
            self.back_btn.pack(side="left")

    def _render_you(self, step) -> None:
        if step.do:
            box = ttk.Frame(self.area)
            box.pack(anchor="w", pady=(PAD, 0))
            for n, line in enumerate(step.do, 1):
                ttk.Label(box, text=f"{n}.  {line}", font=STEP_FONT, wraplength=760, justify="left").pack(
                    anchor="w", pady=2)
        if step.warning:
            tk.Label(self.area, text=step.warning, fg=LEVEL_COLOR["fail"], font=("Segoe UI", 12, "bold"),
                     wraplength=760, justify="left").pack(anchor="w", pady=(PAD // 2, 0))
        if step.expect:
            exp = tk.Frame(self.area, bg=EXPECT_BG, padx=12, pady=8)
            exp.pack(fill="x", pady=(PAD, 0))
            tk.Label(exp, text="You should see:", bg=EXPECT_BG, font=("Segoe UI", 11, "bold")).pack(anchor="w")
            tk.Label(exp, text=step.expect, bg=EXPECT_BG, font=STEP_FONT, wraplength=740,
                     justify="left").pack(anchor="w")
        row = ttk.Frame(self.area)
        row.pack(anchor="w", pady=(PAD, 0))
        self.yes_btn = self._big_button(row, "Yes, that happened", lambda: self._advance(step), primary=True)
        self.yes_btn.pack(side="left")
        self.no_btn = self._big_button(row, "No / something else", lambda: self._show_help(step))
        self.no_btn.pack(side="left", padx=(10, 0))
        self.primary = self.yes_btn

        self.help_frame = ttk.Frame(self.area)
        self.help_text = ttk.Label(self.help_frame, text=step.if_not or "Go back over the steps above, slowly.",
                                   font=BODY_FONT, wraplength=760, justify="left")
        self.help_text.pack(anchor="w")
        url = guide_url(step)
        if url:
            self.help_link = Link(self.help_frame, url, "Open this part of the guide")
            self.help_link.pack(anchor="w", pady=(4, 0))
        self.again_btn = ttk.Button(self.help_frame, text="Try this step again", command=self._hide_help)
        self.again_btn.pack(anchor="w", pady=(6, 0))

    def _show_help(self, step) -> None:
        self.help_frame.pack(fill="x", anchor="w", pady=(PAD, 0))
        self.set_status("Take your time. Nothing is lost by trying the step again.", "info")

    def _hide_help(self) -> None:
        if self.help_frame is not None:
            self.help_frame.pack_forget()
        self.set_status("")

    def _render_auto(self, step) -> None:
        if self.core.walk_done(step.id):  # e.g. Back to a finished step: never run it again
            ttk.Label(self.area, text="✔  Done.", font=STEP_FONT, foreground=LEVEL_COLOR["ok"]).pack(
                anchor="w", pady=(PAD, 0))
            row = ttk.Frame(self.area)
            row.pack(anchor="w", pady=(PAD, 0))
            self.next_done_btn = self._big_button(row, "Next", lambda: self.go(self.index + 1, self.index),
                                                  primary=True)
            self.next_done_btn.pack(side="left")
            self.primary = self.next_done_btn
            return
        ttk.Label(self.area, text="The assistant is doing this now…", font=STEP_FONT).pack(
            anchor="w", pady=(PAD, 0))
        self.progress = ProgressRow(self.area, "")
        self.progress.pack(fill="x", pady=(PAD // 2, 0))
        self.result_label = tk.Label(self.area, text="", font=STEP_FONT, wraplength=760, justify="left")
        self.result_label.pack(anchor="w", pady=(PAD // 2, 0))
        self.fail_box = ttk.Frame(self.area)
        self._run_auto(step)

    def _run_auto(self, step) -> None:
        gen = self.gen
        self.result_label.config(text="", fg="#222222")
        for w in self.fail_box.winfo_children():
            w.destroy()
        self.fail_box.pack_forget()
        self.retry_btn = None
        self.primary = None
        progress = self.wizard.progress_cb(
            lambda done, total, label="": gen == self.gen and self.progress.update_progress(done, total, label))

        def done(result):
            if gen == self.gen:
                self._auto_finished(step, result)

        def failed(exc):
            if gen == self.gen:
                from walkthrough import ActionResult

                self._auto_finished(step, ActionResult(False, str(getattr(exc, "reason", None) or exc)))

        self.wizard.run_bg(lambda: self.core.walk_action(step.action, progress), done, failed)

    def _auto_finished(self, step, result) -> None:
        if result.ok:
            self.progress.update_progress(1, 1)
            self.result_label.config(text=f"✔  {result.message}", fg=LEVEL_COLOR["ok"])
            gen = self.gen
            self.wizard.after(AUTO_PAUSE_MS, lambda: gen == self.gen and self._advance(step))
            return
        self.result_label.config(text=f"✖  {result.message}", fg=LEVEL_COLOR["fail"])
        box = self.fail_box
        box.pack(fill="x", anchor="w", pady=(PAD // 2, 0))
        for line in result.details:
            ttk.Label(box, text=f"•  {line}", font=BODY_FONT, wraplength=740, justify="left").pack(anchor="w")
        if result.retry_hint:
            ttk.Label(box, text=result.retry_hint, font=("Segoe UI", 11, "bold"), wraplength=740,
                      justify="left").pack(anchor="w", pady=(6, 0))
        row = ttk.Frame(box)
        row.pack(anchor="w", pady=(PAD // 2, 0))
        self.retry_btn = self._big_button(row, "Try again", lambda: self._run_auto(step), primary=True)
        self.retry_btn.pack(side="left")
        url = guide_url(step)
        if url:
            Link(row, url, "Open this part of the guide").pack(side="left", padx=(12, 0))
        self.primary = self.retry_btn
        self.set_status("Nothing moves on until this works.", "fail")


PAGES = [WalkPage]
