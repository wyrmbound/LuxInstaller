"""The windowed wizard: one Core, a header, and one visible Page at a time.

Blocking work runs in a thread via run_bg(); its results and any progress updates come back
to the Tk thread through a queue drained every 30 ms (Tk is not thread-safe).

Page names and the order they lead to each other:
  load -> resume | console;  resume -> stage | downloads | console;  console -> drive;
  drive -> cfw | downloads;  downloads -> stage;  stage -> stage | backup;  backup -> done;
  cfw -> done.
"""
from __future__ import annotations

import gc
import queue
import threading
import tkinter as tk
import traceback
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Callable

from gui.core import Core
from gui.widgets import PAD, Link

TITLE = "LuxInstaller"
ICON = Path(__file__).with_name("icon.png")


class Wizard(tk.Tk):
    def __init__(self, core: Core, page_classes: list | None = None):
        super().__init__()
        self.core = core
        self.title(TITLE)
        self.geometry("860x640")
        if ICON.is_file():
            self._icon = tk.PhotoImage(file=str(ICON))
            self.iconphoto(True, self._icon)
        self.minsize(720, 520)
        self._ui_queue: queue.Queue = queue.Queue()
        self.busy = 0
        try:
            ttk.Style(self).theme_use("vista")
        except tk.TclError:
            pass

        top = ttk.Frame(self, padding=(PAD, PAD, PAD, 0))
        top.pack(fill="x")
        self.header = ttk.Label(top, text=TITLE, font=("Segoe UI", 16, "bold"))
        self.header.pack(side="left")
        self.guide_label = ttk.Label(top, text="", foreground="#666666")
        self.guide_label.pack(side="right")
        ttk.Separator(self).pack(fill="x", pady=(PAD // 2, 0))
        self.content = ttk.Frame(self)
        self.content.pack(fill="both", expand=True)

        self.pages = {}
        for cls in page_classes if page_classes is not None else all_pages():
            self.pages[cls.name] = cls(self)
        self.current = None
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self._drain_id = self.after(30, self._drain)

    # --- navigation --------------------------------------------------------------------

    def show(self, name: str, **kw) -> None:
        # Tk objects must be freed on the Tk thread; collect leftovers here, not in a worker.
        gc.collect()
        if self.current is not None:
            self.current.pack_forget()
        page = self.pages[name]
        self.current = page
        page.pack(fill="both", expand=True)
        self.refresh_header()
        page.enter(**kw)

    def go_walk(self) -> None:
        """Into the baby-step walkthrough; writes need the drive label typed once per session."""
        if self.core.confirmed is None:
            self.show("drive", reconfirm=True)
        else:
            self.show("walk")

    def refresh_header(self) -> None:
        if self.core.snap is not None:
            self.guide_label.config(text=f"Guide: hacks-guide/Guide_3DS @ {self.core.guide_commit}")

    # --- threads -----------------------------------------------------------------------

    def run_bg(self, fn: Callable, on_done: Callable | None = None, on_error: Callable | None = None) -> None:
        """Run fn() in a thread; on_done(result) / on_error(exc) run on the Tk thread afterwards."""
        # Collect now so the worker doesn't end up freeing Tk objects (that aborts the process).
        gc.collect()
        self.busy += 1

        def work():
            try:
                result = fn()
            except BaseException as e:  # noqa: BLE001 - every failure must reach the UI
                # Bind now: Python unbinds `e` when the except block ends.
                self.call_ui(lambda e=e, tb=traceback.format_exc(): self._finish_error(e, tb, on_error))
                return
            self.call_ui(lambda: self._finish_ok(result, on_done))

        threading.Thread(target=work, daemon=True).start()

    def _finish_ok(self, result, on_done):
        self.busy -= 1
        if on_done:
            on_done(result)

    def _finish_error(self, e, tb, on_error):
        self.busy -= 1
        if on_error:
            on_error(e)
        else:
            self.show_error(e)

    def call_ui(self, fn: Callable) -> None:
        """Schedule fn() on the Tk thread (safe from any thread)."""
        self._ui_queue.put(fn)

    def progress_cb(self, fn: Callable) -> Callable:
        """Wrap a UI progress handler so a worker thread can call it."""
        return lambda *a: self.call_ui(lambda: fn(*a))

    def _drain(self) -> None:
        try:
            while True:
                fn = self._ui_queue.get_nowait()
                try:
                    fn()
                except Exception:  # one bad callback must not stop every later update
                    self.last_error = traceback.format_exc()
                    traceback.print_exc()
        except queue.Empty:
            pass
        finally:
            self._drain_id = self.after(30, self._drain)

    def destroy(self) -> None:
        try:
            self.after_cancel(self._drain_id)
        except (tk.TclError, AttributeError):
            pass
        super().destroy()

    # --- errors ------------------------------------------------------------------------

    def show_error(self, e: BaseException, title: str = "Stopped") -> None:
        """A dialog with the error and, when it has one, the guide link to follow instead."""
        reason = getattr(e, "reason", None) or str(e) or type(e).__name__
        url = getattr(e, "url", None)
        if url:
            reason += f"\n\nSee: {url}"
        if type(e).__name__ == "ChecksumMismatch":
            reason += "\n\nA download did not match its checksum. Nothing was written to the SD card."
        self.last_error = reason
        if not getattr(self, "quiet", False):
            messagebox.showerror(title, reason, parent=self)

    def on_close(self) -> None:
        if self.busy and not messagebox.askyesno(
                TITLE, "Something is still running (a download or copy). Close anyway?", parent=self):
            return
        self.destroy()


def all_pages() -> list:
    from gui import pages_files, pages_setup, pages_stage, pages_walk

    return [*pages_setup.PAGES, *pages_files.PAGES, *pages_stage.PAGES, *pages_walk.PAGES]


def create_wizard(core: Core, page_classes: list | None = None, attempts: int = 3) -> Wizard:
    """Create the window, retrying a Tk start-up that can't read its own .tcl files.

    On Windows that happens now and then while another process (often antivirus) holds a
    Tk library file open; the files are there and the next attempt works.
    """
    import time

    for attempt in range(attempts):
        try:
            return Wizard(core, page_classes)
        except tk.TclError as e:
            if "couldn't read file" not in str(e) or attempt == attempts - 1:
                raise
            time.sleep(0.3)
    raise AssertionError("unreachable")


def run_gui(argv: list[str] | None = None) -> int:
    import cli

    args = cli.parse_args(argv or [])
    app = create_wizard(Core(args))
    app.show("load")
    app.mainloop()
    return 0


__all__ = ["Wizard", "run_gui", "Link"]
