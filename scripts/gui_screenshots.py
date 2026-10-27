"""Capture README screenshots of the GUI (Windows only), using the test harness's fake SD card.

    .venv/Scripts/python scripts/gui_screenshots.py

Writes docs/screenshots/gui-console.png and gui-stage.png. Uses only ctypes (GDI) and zlib,
so no imaging library is needed.
"""
from __future__ import annotations

import ctypes
import struct
import sys
import tempfile
import time
import zlib
from ctypes import wintypes
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tests")]
OUT = ROOT / "docs" / "screenshots"


def grab(x: int, y: int, w: int, h: int) -> bytes:
    """Screen pixels as raw RGB rows (top to bottom)."""
    user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32
    screen = user32.GetDC(0)
    mem = gdi32.CreateCompatibleDC(screen)
    bmp = gdi32.CreateCompatibleBitmap(screen, w, h)
    gdi32.SelectObject(mem, bmp)
    gdi32.BitBlt(mem, 0, 0, w, h, screen, x, y, 0x00CC0020)  # SRCCOPY

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                    ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                    ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                    ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]

    info = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
    buf = ctypes.create_string_buffer(w * h * 4)
    gdi32.GetDIBits(mem, bmp, 0, h, buf, ctypes.byref(info), 0)
    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(mem)
    user32.ReleaseDC(0, screen)
    raw = buf.raw
    rows = []
    for r in range(h):
        line = raw[r * w * 4:(r + 1) * w * 4]
        rgb = bytearray(w * 3)
        rgb[0::3], rgb[1::3], rgb[2::3] = line[2::4], line[1::4], line[0::4]  # BGRA -> RGB
        rows.append(bytes(rgb))
    return b"".join(b"\x00" + row for row in rows)


def write_png(path: Path, w: int, h: int, scanlines: bytes) -> None:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(scanlines, 9)) + chunk(b"IEND", b"")
    path.write_bytes(png)


def shoot(app, name: str) -> Path:
    # Keep the pointer off the window so no hover popup (e.g. Snap Layouts) ends up in the picture.
    ctypes.windll.user32.SetCursorPos(app.winfo_rootx() + app.winfo_width() + 300, app.winfo_rooty() + 300)
    app.lift()
    app.attributes("-topmost", True)
    for _ in range(20):
        app.update()
        time.sleep(0.03)
    x, y = app.winfo_rootx(), app.winfo_rooty()
    w, h = app.winfo_width(), app.winfo_height()
    path = OUT / name
    write_png(path, w, h, grab(x, y, w, h))
    app.attributes("-topmost", False)
    return path


def main() -> int:
    ctypes.windll.shcore.SetProcessDpiAwareness(1)  # so Tk's coordinates are real screen pixels
    import flow_harness as fh
    import gui_helpers as gh
    from test_gui_files import fake_download, fake_plan

    OUT.mkdir(parents=True, exist_ok=True)
    world = fh.make_world(Path(tempfile.mkdtemp()))
    fh.install(world)
    import dataclasses
    import sdcard
    fake_drives = sdcard.list_drives
    app = gh.make_wizard(world)
    app.deiconify()
    app.geometry("900x660+40+40")
    import downloader
    # Under Core.plan_downloads, so its walkthrough filtering (no Python needed) still runs.
    downloader.plan_downloads = lambda snap, slugs, client: fake_plan(world, with_python=True)
    fetch = fake_download(world)

    def download(plan, manual, progress=None):
        app.core.downloads = fetch(plan, manual, progress)
        return app.core.downloads

    app.core.download = download
    # The fake card isn't a drive root, so the PC-side steps (copying, running MSET9) are stubbed.
    from walkthrough import ActionResult
    app.core.walk_action = lambda name, progress=None: ActionResult(True, "Done and checked.")
    app.show("load")
    gh.pump_until(app, lambda: app.current.name == "console")
    c = app.pages["console"]
    c.model_var.set("old2ds")
    c.version_var.set("Ver. 11.17.0-50U")
    c.find_btn.invoke()
    gh.pump_until(app, lambda: str(c.next_btn["state"]) == "normal")
    print(shoot(app, "gui-console.png"))

    c.next_btn.invoke()
    d = app.pages["drive"]
    gh.pump_until(app, lambda: d.tree.get_children())
    d.tree.selection_set(d.tree.get_children()[0])
    gh.pump(app)
    # For the picture only, show the fake card as E:\ instead of its temp folder (which has the user name).
    sdcard.list_drives = lambda: [dataclasses.replace(x, mount="E:\\") for x in fake_drives()]
    d.refresh()
    gh.pump_until(app, lambda: d.tree.get_children() and d.tree.item(d.tree.get_children()[0], "values")[0] == "E:\\")
    d.tree.selection_set(d.tree.get_children()[0])
    gh.pump(app)
    d.label_var.set("3DS")
    gh.pump(app)
    print(shoot(app, "gui-drive.png"))
    sdcard.list_drives = fake_drives
    d.refresh()
    gh.pump_until(app, lambda: d.tree.get_children() and d.tree.item(d.tree.get_children()[0], "values")[0] != "E:\\")
    d.tree.selection_set(d.tree.get_children()[0])
    gh.pump(app)
    d.label_var.set("")
    d.label_var.set("3DS")
    gh.pump_until(app, lambda: str(d.next_btn["state"]) == "normal")
    d.next_btn.invoke()
    gh.pump_until(app, lambda: app.current.name == "downloads")
    dl = app.pages["downloads"]
    gh.pump_until(app, lambda: dl.plan is not None)
    dl.luma_kind.set("github")
    dl.luma_repo.set("wyrmbound/LuxiOS")
    gh.pump(app)
    print(shoot(app, "gui-downloads.png"))

    dl.luma_kind.set("official")
    dl.download_btn.invoke()
    gh.pump_until(app, lambda: app.current.name == "walk", timeout=60)
    w = app.pages["walk"]
    kinds = {s.id: s.kind for s in app.core.walk_steps()}
    while w.current_id != "mii-maker":  # a step with button presses to show
        gh.pump_until(app, lambda: w.current_id == "move" or kinds.get(w.current_id) == "you", timeout=60)
        (w.move_btn if w.current_id == "move" else w.yes_btn).invoke()
        gh.pump(app)
    print(shoot(app, "gui-walk.png"))

    # The last page, as after a real run that installed a fork (example backup path).
    from luma_build import LumaChoice
    app.core.state.luma = LumaChoice("github", repo="wyrmbound/LuxiOS").to_dict()
    app.core.state.backup_dir = r"C:\Users\you\3DS-Backups\2026-10-27_183228"
    app.show("done")
    gh.pump(app)
    print(shoot(app, "gui-done.png"))
    app.destroy()
    return 0


if __name__ == "__main__":
    sys.exit(main())
