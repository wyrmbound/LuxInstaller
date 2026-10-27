"""The Textual app. cli.run does all the work in a worker thread; this only shows its output
and hands it the user's answers, so the TUI and `--no-tui` can never drift apart.
"""
from __future__ import annotations

import queue
import threading
from typing import Callable

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.widgets import Footer, Header, Input, Label, RichLog

import cli

# Injection point for tests: (argv, inp, out) -> exit code.
cli_run: Callable[..., int] = cli.run

_CLOSING = object()


def style_line(line: str) -> Text:
    stripped = line.lstrip()
    if line.startswith("Stage "):
        return Text(line, style="bold cyan")
    if stripped.startswith("[fail]") or stripped.startswith("!!!") or line.startswith("!" * 10):
        return Text(line, style="bold red")
    if stripped.startswith("[warn]") or stripped.startswith("[WARNING]") or stripped.startswith("[DANGER]"):
        return Text(line, style="yellow")
    if stripped.startswith("[ok]"):
        return Text(line, style="green")
    if line.startswith("=" * 10):
        return Text(line, style="cyan")
    return Text(line)


class AssistantApp(App):
    TITLE = "LuxInstaller"
    CSS = """
    #log { height: 1fr; border: round $primary; }
    #prompt-box { height: auto; padding: 0 1; }
    #prompt { color: $accent; text-style: bold; }
    #status { color: $text-muted; }
    """
    BINDINGS = [Binding("ctrl+q", "quit_flow", "Save and quit", priority=True)]

    def __init__(self, argv: list[str] | None = None):
        super().__init__()
        self.argv = list(argv or [])
        self.answers: queue.Queue = queue.Queue()
        self.exit_code: int | None = None
        self.waiting = False
        self._quit_requested = False
        self._flow_closing = threading.Event()

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        yield RichLog(id="log", wrap=True, markup=False, highlight=False)
        with Vertical(id="prompt-box"):
            yield Label("Starting...", id="prompt")
            yield Input(placeholder="Type your answer and press Enter", id="answer")
            yield Label("", id="status")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#answer", Input).disabled = True
        self.run_worker(self._run_flow, thread=True, exclusive=True, name="flow")

    # --- worker side (runs in a thread) ---

    def _out(self, line: str) -> None:
        if not self._flow_closing.is_set():
            self.call_from_thread(self._write, str(line))

    def _inp(self, prompt: str) -> str:
        if self._flow_closing.is_set():
            raise EOFError
        self.call_from_thread(self._ask, prompt)
        answer = self.answers.get()
        if answer is _CLOSING:
            raise EOFError
        return answer

    def _run_flow(self) -> None:
        try:
            code = cli_run(self.argv, inp=self._inp, out=self._out)
        except EOFError:
            code = 130
        except Exception as e:  # show unexpected errors instead of a silent dead UI
            self._out(f"Unexpected error: {e!r}")
            code = 2
        if not self._flow_closing.is_set():
            self.call_from_thread(self._finished, code)

    # --- UI side ---

    def _write(self, line: str) -> None:
        self.query_one("#log", RichLog).write(style_line(line))

    def _ask(self, prompt: str) -> None:
        if self._quit_requested:  # the flow kept asking after "q": close instead
            self._flow_closing.set()
            self.answers.put(_CLOSING)
            self.exit(130)
            return
        self.waiting = True
        self.query_one("#prompt", Label).update(prompt.strip() or "Press Enter to continue")
        box = self.query_one("#answer", Input)
        box.disabled = False
        box.value = ""
        box.focus()

    def _finished(self, code: int) -> None:
        self.exit_code = code
        self.waiting = False
        meaning = {0: "finished", 1: "stopped", 2: "stopped with an error", 130: "interrupted; progress saved"}
        self.query_one("#prompt", Label).update(
            f"Exit code {code} ({meaning.get(code, 'done')}). Press Enter or Escape to close.")
        box = self.query_one("#answer", Input)
        box.value = ""
        box.disabled = False
        box.focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if self.exit_code is not None:
            self.exit(self.exit_code)
            return
        if not self.waiting:
            return
        self.waiting = False
        event.input.disabled = True
        self.query_one("#status", Label).update(f"> {event.value}")
        self.query_one("#prompt", Label).update("Working...")
        self.answers.put(event.value)

    def on_key(self, event) -> None:
        if event.key == "escape" and self.exit_code is not None:
            self.exit(self.exit_code)

    def action_quit_flow(self) -> None:
        """At a prompt, answer "q" so the CLI saves progress; otherwise stop the flow and close."""
        if self.exit_code is not None:
            self.exit(self.exit_code)
        elif self.waiting:
            self.waiting = False
            self._quit_requested = True
            self.query_one("#answer", Input).disabled = True
            self.answers.put("q")
        else:
            self._flow_closing.set()
            self.answers.put(_CLOSING)
            self.exit(130)

    def on_unmount(self) -> None:
        self._flow_closing.set()
        self.answers.put(_CLOSING)


def run_tui(argv: list[str] | None = None) -> int:
    app = AssistantApp(argv)
    result = app.run()
    if result is None:
        return app.exit_code if app.exit_code is not None else 130
    return int(result)
