"""Render assets/luxinstaller.svg to PNGs and a Windows .ico with headless Microsoft Edge.

Each size is rendered from the SVG (not downscaled), then packed into one .ico with PNG
entries (supported since Windows Vista). Usage: python scripts/make_icon.py
"""
from __future__ import annotations

import os
import struct
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SVG = ROOT / "assets" / "luxinstaller.svg"
ICO = ROOT / "assets" / "luxinstaller.ico"
PNG = ROOT / "assets" / "luxinstaller.png"
WINDOW_ICON = ROOT / "src" / "gui" / "icon.png"  # the title-bar icon, bundled into the exes
SIZES = (16, 24, 32, 48, 64, 128, 256)
EDGE = [Path(os.environ.get(v, "")) / "Microsoft/Edge/Application/msedge.exe"
        for v in ("ProgramFiles(x86)", "ProgramFiles")]


def render(size: int, out: Path) -> bytes:
    edge = next(p for p in EDGE if p.is_file())
    with tempfile.TemporaryDirectory() as tmp:
        page = Path(tmp) / "icon.html"
        page.write_text(f'<html><body style="margin:0;background:transparent">'
                        f'<img src="{SVG.as_uri()}" width="{size}" height="{size}" style="display:block">'
                        f'</body></html>', encoding="utf-8")
        subprocess.run([str(edge), "--headless", "--disable-gpu", "--hide-scrollbars",
                        "--default-background-color=00000000", f"--window-size={size},{size}",
                        f"--user-data-dir={tmp}/profile", f"--screenshot={out}", page.as_uri()],
                       check=True, capture_output=True, timeout=60)
    return out.read_bytes()


def write_ico(images: list[tuple[int, bytes]], path: Path) -> None:
    header = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    entries, blobs = b"", b""
    for size, data in images:
        dim = 0 if size >= 256 else size
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(data), offset + len(blobs))
        blobs += data
    path.write_bytes(header + entries + blobs)


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        images = [(s, render(s, Path(tmp) / f"{s}.png")) for s in SIZES]
    write_ico(images, ICO)
    PNG.write_bytes(render(512, PNG))
    WINDOW_ICON.write_bytes(render(64, WINDOW_ICON))
    print(f"wrote {ICO} ({', '.join(map(str, SIZES))}), {PNG} and {WINDOW_ICON}")


if __name__ == "__main__":
    main()
