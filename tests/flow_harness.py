"""A fake SD card, fake downloads and a simulated console for driving the real CLI flow.

Used in-process by tests and as a subprocess entry point (`python flow_harness.py <dir>`)
for the kill-and-resume test. Only the drive list and the network are faked; the guide,
routing, stager, stages, state and backup code are the real modules.
"""
from __future__ import annotations

import hashlib
import io
import sys
import types
import zipfile
from pathlib import Path

HERE = Path(__file__).parent
sys.path[:0] = [str(HERE.parent / "src"), str(HERE)]

GUIDE = HERE / "fixtures" / "guide_63013be"
ID0 = "0123456789abcdef0123456789abcdef"
ID1 = "fedcba9876543210fedcba9876543210"
HAX_ID1 = "sdmc_hax_ééé_id1"
OLD3DS = "1"
VERSION = "Ver. 11.17.0-50E"  # Old 3DS -> MSET9 -> mset9-cli on a PC


def make_world(base: Path) -> dict:
    sd = base / "SD"
    (sd / "Nintendo 3DS" / ID0 / ID1 / "dbs").mkdir(parents=True, exist_ok=True)
    dl = base / "downloads"
    dl.mkdir(parents=True, exist_ok=True)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("mset9.py", "print('mset9')")
        z.writestr("boot.firm", b"B" * 256)
        z.writestr("boot9strap/boot9strap.firm", b"9" * 128)
        z.writestr("SafeB9S.bin", b"S" * 64)
    (dl / "MSET9-v2.2.zip").write_bytes(buf.getvalue())
    (dl / "x_finalize_helper.firm").write_bytes(b"F" * 100)
    (dl / "finalize.romfs").write_bytes(b"R" * 300)
    return {"base": base, "sd": sd, "dl": dl, "app": base / "app", "backups": base / "pc-backups"}


def install(world: dict, set_attr=setattr) -> None:
    import cli
    import paths
    import sdcard

    app = world["app"]
    set_attr(paths, "APP_DIR", app)
    set_attr(paths, "CACHE_DIR", app / "cache")
    set_attr(paths, "STATE_PATH", app / "state.json")
    set_attr(paths, "SNAPSHOT_PATH", app / "guide_snapshot.json")
    drive = sdcard.DriveInfo(
        mount=str(world["sd"]), label="3DS", filesystem="FAT32", cluster_size=32768,
        total_bytes=31_900_000_000, free_bytes=20_000_000_000, removable=True, system=False,
        partition_style="MBR", device=str(world["sd"]), serial="1234-ABCD")
    set_attr(sdcard, "list_drives", lambda: [drive] if world["sd"].exists() else [])

    def get_downloads(snap, slug, inp, out):
        def d(name, file):
            p = world["dl"] / file
            return types.SimpleNamespace(name=name, path=p, sha256=hashlib.sha256(p.read_bytes()).hexdigest(),
                                         verified="published", from_cache=False)
        return [d("MSET9", "MSET9-v2.2.zip"), d("x_finalize_helper.firm", "x_finalize_helper.firm"),
                d("finalize.romfs", "finalize.romfs")]

    set_attr(cli, "get_downloads", get_downloads)


class Console:
    """Answers the CLI's prompts and does what the console (and the MSET9 script) would do to the SD."""

    def __init__(self, world: dict, stop_at: str | None = None, extra: list[str] | None = None):
        self.world = world
        self.sd = world["sd"]
        self.stage = ""
        self.stop_at = stop_at
        self.prompts: list[str] = []
        self.lines: list[str] = []
        self.extra = list(extra or [])
        self.skip_effects: set[str] = set()

    def out(self, line: str) -> None:
        self.lines.append(line)
        if line.startswith("Stage "):
            self.stage = line

    def __call__(self, prompt: str) -> str:
        import cli

        self.prompts.append(prompt)
        if len(self.prompts) > 300:  # a check that never passes must fail the test, not hang it
            raise EOFError
        if self.extra:
            return self.extra.pop(0)
        if self.stop_at and self.stop_at in self.stage and prompt == cli.STEPS_DONE:
            raise EOFError
        if prompt.startswith("Which console"):
            return OLD3DS
        if prompt == "Number: ":
            return OLD3DS
        if prompt.startswith("System version"):
            return VERSION
        if "Which drive" in prompt:
            return "1"
        if "type the drive label" in prompt:
            return "3DS"
        if prompt.startswith("Type WRITE"):
            return "WRITE"
        if prompt in (cli.RESUME_PROMPT,):
            return "y"
        if prompt == cli.STEPS_DONE:
            self.console_effects()
        return ""

    def console_effects(self) -> None:
        id0 = self.sd / "Nintendo 3DS" / ID0
        page, _, s = self.stage.partition(" - Section ")  # s: "IV: Removing MSET9"
        if any(k in self.stage for k in self.skip_effects):
            return
        if "(MSET9" in page and "Prep Work" in s:  # the MSET9 script: "Created hacked ID1."
            (id0 / ID1).rename(id0 / f"{ID1}_user-id1")
            (id0 / HAX_ID1).mkdir()
        elif "Installing boot9strap" in s:  # Luma3DS saves its config on (Start)
            (self.sd / "luma").mkdir(exist_ok=True)
            (self.sd / "luma" / "config.ini").write_text("[boot]\n")
        elif "Removing MSET9" in s:  # MSET9 script options 4 then 5
            (id0 / HAX_ID1).rmdir()
            (id0 / f"{ID1}_user-id1").rename(id0 / ID1)
        elif "Setup Script" in s:  # the finalize script in GodMode9
            b = self.sd / "gm9" / "backups"
            b.mkdir(parents=True, exist_ok=True)
            nand = b"N" * 4096
            (b / "20261027_0123456789_sysnand_00.bin").write_bytes(nand)
            (b / "20261027_0123456789_sysnand_00.bin.sha").write_bytes(hashlib.sha256(nand).digest())
            (b / "essential.exefs").write_bytes(b"E" * 512)


def run(world: dict, console: Console, *extra_args: str) -> int:
    import cli

    return cli.run(["--no-tui", "--guide-dir", str(GUIDE), "--backup-dir", str(world["backups"]), *extra_args],
                   inp=console, out=console.out)


if __name__ == "__main__":  # subprocess entry for the kill test: answers come from stdin
    base = Path(sys.argv[1])
    world = make_world(base)
    install(world)
    import cli

    def inp(prompt):
        print(prompt, flush=True)
        line = sys.stdin.readline()
        if not line:
            raise EOFError
        return line.rstrip("\n")

    sys.exit(cli.run(["--no-tui", "--guide-dir", str(GUIDE), "--backup-dir", str(world["backups"])],
                     inp=inp, out=lambda s: print(s, flush=True)))
