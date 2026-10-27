"""Run the official MSET9 script (mset9.py on the SD card) for the user, non-interactively.

The script is menu-driven through input(); each action here feeds it the same numbers the
guide tells the user to type, captures what it prints, and reads its own state line
("Current MSET9 state: ..."). It runs in-process (runpy-style exec) so it also works in the
PyInstaller exe, where there is no separate python.exe.

Safety: the script's dig_for_root() MOVES its files to the drive root when it isn't run from a
mount point, so a run is refused unless the SD root is a mount point holding mset9.py. The
script on the card is never modified.
"""
from __future__ import annotations

import io
import os
import re
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

TROUBLE = "https://3ds.hacks.guide/troubleshooting-mset9"
ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
STATE_RE = re.compile(r"Current MSET9 state: (.+)")
CONSOLE_RE = re.compile(r'(\d+)\s*:\s*"(Old|New) 3DS/2DS, (\d+)\.(\d+)\.\d+ to (\d+)\.(\d+)\.\d+')
REPORT_RE = re.compile(r"(HOME menu extdata|Mii Maker extdata|Title database): (OK!|Missing!|Not initialized!)")
ERROR_CODE_RE = re.compile(r"Error (\d+):")
BACKUP_TAG = "_user-id1"  # the script's realID1BackupTag

FRIENDLY = {
    "01": "MSET9 couldn't find the 'Nintendo 3DS' folder. Put the card in the console, power it on once, then try again.",
    "02": "The SD card is write-protected. Slide the card's lock switch up (towards the contacts) and try again.",
    "03": "The console model/version doesn't match the one MSET9 was set up with.",
    "04": "The card doesn't have exactly one ID0 folder inside 'Nintendo 3DS'.",
    "05": "The card doesn't have exactly one ID1 folder.",
    "06": "The SD card needs at least 16MB of free space.",
    "07": "Some MSET9 files on the card are missing or damaged. Copy the MSET9 files to the card again.",
    "08": "MSET9 lost track of the SD card. Make sure it's still inserted and try again.",
    "18": "Windows' language/locale settings are broken for MSET9.",
}

ANSWERS = {
    "status": "{i}\n0\n",
    "create": "{i}\n1\n1\n\n",
    "check": "{i}\n2\n\n",
    "inject": "{i}\n3\n\n",
    "remove": "{i}\n4\n5\n\n\n",
}

_lock = threading.Lock()
_is_mount = os.path.ismount  # tests patch this; the SD root must be a real mount point


class Mset9Error(Exception):
    pass


@dataclass
class Mset9Result:
    ok: bool
    state: str | None = None
    message: str = ""
    report: dict[str, bool] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    output: str = ""


def _script_path(sd_root) -> Path:
    path = Path(sd_root) / "mset9.py"
    if not path.is_file():
        raise Mset9Error(f"mset9.py isn't on the SD card at {sd_root}. Copy the MSET9 files to the card first.")
    return path


def console_index(sd_root, family: str, major: int, minor: int) -> int:
    """The script's own menu number for this console, read from its consoleNames table."""
    text = _script_path(sd_root).read_text(encoding="utf-8")
    entries = CONSOLE_RE.findall(text)
    if not entries:
        raise Mset9Error("couldn't read the console list from mset9.py")
    want = (major, minor)
    hits = [int(n) for n, fam, a, b, c, d in entries
            if fam.lower() == family.lower() and (int(a), int(b)) <= want <= (int(c), int(d))]
    hits = sorted(set(hits))
    if len(hits) != 1:
        raise Mset9Error(f"mset9.py has {'no' if not hits else 'more than one'} entry for a "
                         f"{family} console on {major}.{minor}")
    return hits[0]


def _exit(code=0):
    raise SystemExit(code)


def _open_std_fds() -> None:
    """Give fds 1 and 2 a target (os.devnull) when there are none, as in the windowed exe.

    mset9.py's remove() tests `os.path.exists(abs(realID1Path) and realID1Path.endswith(...))`,
    which is os.path.exists(True), i.e. "is fd 1 open?". Without fd 1 it skips renaming the
    user's ID1 back, yet still prints "Successfully removed MSET9!".
    """
    for fd in (1, 2):
        try:
            os.fstat(fd)
        except OSError:
            null = os.open(os.devnull, os.O_RDWR)
            if null != fd:
                os.dup2(null, fd)
                os.close(null)


def _restore_user_id1(sd_root) -> str:
    """The last step of the guide's manual removal (troubleshooting-mset9): rename the remaining
    `<ID1>_user-id1` back to `<ID1>`. Only when no MSET9 ID1 is left beside it and the plain name
    is free. Returns what was done, or "" when there was nothing to do."""
    n3ds = Path(sd_root) / "Nintendo 3DS"
    done = []
    for id0 in (p for p in n3ds.iterdir() if p.is_dir() and _is_id(p.name)) if n3ds.is_dir() else ():
        entries = [p for p in id0.iterdir() if p.is_dir()]
        backups = [p for p in entries if p.name[32:] == BACKUP_TAG and _is_id(p.name[:32])]
        if not backups:
            continue
        if any("sdmc" in p.name and len(p.name) == 32 for p in entries):
            raise Mset9Error("MSET9's own ID1 folder is still on the card, so your data folder was left "
                             f"as is (see {TROUBLE}).")
        if len(backups) != 1:
            raise Mset9Error(f"Found {len(backups)} '_user-id1' folders; fix this by hand (see {TROUBLE}).")
        target = id0 / backups[0].name[:32]
        if target.exists():
            raise Mset9Error(f"Both {backups[0].name} and {target.name} exist, so neither was touched "
                             f"(see {TROUBLE}).")
        backups[0].rename(target)
        done.append(f"renamed {backups[0].name} back to {target.name}")
    return "; ".join(done)


def _is_id(name: str) -> bool:
    return len(name) == 32 and all(c in "0123456789abcdefABCDEF" for c in name)


def _execute(sd_root, answers: str, _patch: Callable[[str], str] | None = None) -> tuple[str, bool]:
    """Run the script with `answers` as stdin. Returns (ANSI-stripped output, ran_out_of_input)."""
    sd_root = Path(sd_root)
    path = _script_path(sd_root)
    if not _is_mount(str(sd_root)):
        raise Mset9Error(f"{sd_root} isn't the root of a drive. MSET9 must run from the SD card's root, "
                         "otherwise it moves its files around.")
    _open_std_fds()
    source = path.read_text(encoding="utf-8")
    if _patch is not None:
        source = _patch(source)
    code = compile(source, str(path), "exec")
    glb = {"__name__": "__main__", "__file__": str(path), "__builtins__": __builtins__, "exit": _exit}

    out = io.StringIO()
    eof = False
    with _lock:
        saved = (sys.stdin, sys.stdout, sys.stderr, os.getcwd(), os.system, time.sleep)
        try:
            sys.stdin, sys.stdout, sys.stderr = io.StringIO(answers), out, out
            os.system = lambda cmd: 0  # the script only uses it to clear the screen
            time.sleep = lambda s: None
            try:
                exec(code, glb)
            except SystemExit:
                pass
            except EOFError:
                eof = True
        finally:
            sys.stdin, sys.stdout, sys.stderr, cwd, os.system, time.sleep = saved
            os.chdir(cwd)
    return ANSI_RE.sub("", out.getvalue()), eof


def _parse(output: str) -> Mset9Result:
    states = STATE_RE.findall(output)
    report = {name: value == "OK!" for name, value in REPORT_RE.findall(output)}
    errors = [l.strip() for l in output.splitlines() if ERROR_CODE_RE.search(l) or l.lstrip().startswith("[XX]")]
    return Mset9Result(ok=False, state=states[-1].strip() if states else None, report=report,
                       errors=errors, output=output)


def _friendly(errors: list[str]) -> str:
    for line in errors:
        m = ERROR_CODE_RE.search(line)
        if m and m.group(1) in FRIENDLY:
            return f"{FRIENDLY[m.group(1)]} (MSET9 {m.group(0)[:-1]}; see {TROUBLE})"
    return f"{errors[0]} (see {TROUBLE})" if errors else ""


def run(sd_root, index: int, action: str, *, _patch: Callable[[str], str] | None = None) -> Mset9Result:
    if action == "ready":
        status = run(sd_root, index, "status", _patch=_patch)
        if status.state == "Ready":
            status.message = "MSET9 is ready."
            return status
        if not status.ok and status.state is None:
            return status
        check = run(sd_root, index, "check", _patch=_patch)
        check.state = check.state or status.state
        missing = [k for k, v in check.report.items() if not v]
        check.ok = False
        check.message = ("Not ready yet: " + ", ".join(missing) + " missing.") if missing else \
            (check.message or f"MSET9 state: {status.state}")
        return check
    if action not in ANSWERS:
        raise ValueError(f"unknown MSET9 action {action!r}")

    output, eof = _execute(sd_root, ANSWERS[action].format(i=index), _patch)
    r = _parse(output)
    if action == "status":
        r.ok = r.state is not None
    elif action == "create":
        if "Created hacked ID1." in output:
            r.ok, r.message = True, "Created the MSET9 ID1."
        elif "Hacked ID1 already exists." in output:
            r.ok, r.message = True, "The MSET9 ID1 was already there."
    elif action == "check":
        r.ok = len(r.report) == 3 and all(r.report.values())
    elif action == "inject":
        if "MSET9 successfully injected!" in output:
            r.ok, r.message = True, "MSET9 injected."
        elif r.state == "Injected":
            r.ok, r.message = True, "MSET9 was already injected."
    elif action == "remove":
        if "Successfully removed MSET9!" in output:
            r.ok, r.message = True, "MSET9 removed; your data is back."
        elif r.state == "ID1 not created" or "Nothing to do." in output:
            r.ok, r.message = True, "MSET9 was already removed."
        if r.ok:  # trust the card, not the script's message
            try:
                fixed = _restore_user_id1(sd_root)
            except Mset9Error as e:
                r.ok, r.message = False, str(e)
            else:
                if fixed:
                    r.message = f"MSET9 removed; {fixed} (the guide's manual removal step)."

    if not r.ok:
        if r.errors:
            r.message = _friendly(r.errors)
        elif eof:
            r.message = f"MSET9 asked for something unexpected (see {TROUBLE})."
        elif not r.message:
            r.message = f"MSET9 didn't confirm the '{action}' step (state: {r.state or 'unknown'}; see {TROUBLE})."
    return r
