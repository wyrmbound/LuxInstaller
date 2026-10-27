"""Build single-file executables with PyInstaller.

    .venv/Scripts/python scripts/build_exe.py [--only console|gui|all]   (Windows)
    .venv/bin/python scripts/build_exe.py [--only console|gui|all]       (macOS / Linux)

Outputs in dist/:
- luxinstaller-cli(.exe): console program (src/main.py). Opens the GUI by default; `--tui` and
  `--no-tui` give the terminal interfaces.
- LuxInstaller(.exe): windowed program (src/gui_main.py), GUI only, no console window.
(Different names, not just different case: Windows file names ignore case.)

Most modules are imported lazily inside functions, so every one of them is listed as a hidden
import. The executables are built for the CPU of the Python running this script: on Windows
ARM64, use the x64 Python venv (QuickJS has no ARM64 wheel), which gives x64 exes that also run
on ARM64 Windows.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
PACKAGES = ("stages", "tui", "gui")
ENTRY_SCRIPTS = ("main", "gui_main")  # run as __main__, never imported

TARGETS = {
    "console": {"entry": "main.py", "name": "luxinstaller-cli", "mode": "--console"},
    "gui": {"entry": "gui_main.py", "name": "LuxInstaller", "mode": "--windowed"},
}
ICO = ROOT / "assets" / "luxinstaller.ico"  # scripts/make_icon.py
WINDOW_ICON = SRC / "gui" / "icon.png"


def hidden_imports() -> list[str]:
    mods = [p.stem for p in SRC.glob("*.py") if p.stem not in ENTRY_SCRIPTS]
    for pkg in PACKAGES:
        if (SRC / pkg / "__init__.py").is_file():
            mods.append(pkg)
            mods += [f"{pkg}.{p.stem}" for p in (SRC / pkg).glob("*.py") if p.stem != "__init__"]
    return sorted(mods)


def pyinstaller_args(target: str = "console") -> list[str]:
    t = TARGETS[target]
    args = [
        str(SRC / t["entry"]),
        "--name", t["name"],
        "--onefile",
        t["mode"],
        "--noconfirm",
        "--clean",
        "--paths", str(SRC),
        "--distpath", str(ROOT / "dist"),
        "--workpath", str(ROOT / "build" / target),
        "--specpath", str(ROOT / "build" / target),
        "--collect-submodules", "stages",
        "--collect-all", "quickjs",  # native extension module
        "--collect-data", "textual",  # TCSS and other package data (the console exe's TUI)
    ]
    if ICO.is_file():
        args += ["--icon", str(ICO)]
    if WINDOW_ICON.is_file():
        args += ["--add-data", f"{WINDOW_ICON}{os.pathsep}gui"]
    for pkg in ("tui", "gui"):
        if (SRC / pkg / "__init__.py").is_file():
            args += ["--collect-submodules", pkg]
    for mod in hidden_imports():
        args += ["--hidden-import", mod]
    return args


def exe_path(target: str) -> Path:
    name = TARGETS[target]["name"]
    return ROOT / "dist" / (f"{name}.exe" if sys.platform == "win32" else name)


def build(target: str) -> bool:
    import PyInstaller.__main__

    if target == "gui" and not (SRC / "gui" / "__init__.py").is_file():
        print("Skipping the windowed exe: src/gui is missing", file=sys.stderr)
        return False
    args = pyinstaller_args(target)
    print("pyinstaller " + " ".join(args))
    PyInstaller.__main__.run(args)
    exe = exe_path(target)
    if not exe.is_file():
        print(f"Build failed: no executable produced for {target}", file=sys.stderr)
        return False
    print(f"Built {exe} ({exe.stat().st_size / 1e6:.1f} MB)")
    return True


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Build the LuxInstaller executables with PyInstaller.")
    p.add_argument("--only", choices=("console", "gui", "all"), default="all",
                   help="which executable to build (default: all)")
    args = p.parse_args(argv)
    targets = ("console", "gui") if args.only == "all" else (args.only,)
    ok = [build(t) for t in targets]
    return 0 if all(ok) else 1


if __name__ == "__main__":
    sys.exit(main())
