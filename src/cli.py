"""Plain command-line flow (`--no-tui`): intake, method selection, SD checks, downloads, staging.

IO is injectable (`inp`, `out`) so the whole flow can be scripted in tests. Modules are
imported inside functions so each stage only needs what it uses.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

Input = Callable[[str], str]
Output = Callable[[str], None]


class Stop(Exception):
    """End the run with this exit code (after the message was already printed)."""

    def __init__(self, code: int):
        super().__init__(code)
        self.code = code


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="luxinstaller", description="LuxInstaller: 3DS custom firmware (Luma3DS or a fork) setup assistant")
    p.add_argument("--no-tui", action="store_true", help="use the plain command-line flow")
    p.add_argument("--guide-dir", type=Path, help="build the guide snapshot from a local Guide_3DS checkout")
    p.add_argument("--dry-run", action="store_true", help="stop after showing what would be copied to the SD card")
    p.add_argument("--backup-dir", type=Path, default=Path.home() / "3DS-Backups",
                   help="where NAND and essential backups are copied (default: ~/3DS-Backups)")
    p.add_argument("--restart", action="store_true", help="ignore saved progress and start from the beginning")
    return p.parse_args(argv)


def _client():
    try:
        import downloader

        return downloader.make_client()
    except ImportError:
        import httpx

        return httpx.Client(timeout=30, follow_redirects=True)


def _ask(inp: Input, prompt: str) -> str:
    try:
        return inp(prompt)
    except (EOFError, StopIteration):
        raise Stop(130) from None


def _save_state(state, **fields) -> None:
    if state is None:
        return
    for k, v in fields.items():
        setattr(state, k, v)
    state.save()


def _load_state():
    try:
        import state as state_mod
    except ImportError:
        return None
    return state_mod.State.load()


# --- steps ------------------------------------------------------------------------------

def load_snapshot(args, inp: Input, out: Output):
    import guide_parser
    import paths

    if args.guide_dir:
        docs = args.guide_dir / "docs" if (args.guide_dir / "docs").is_dir() else args.guide_dir
        snap = guide_parser.build_snapshot(docs, "local")
        out(f"Guide: local checkout {docs}")
        return snap

    import httpx

    snap = guide_parser.GuideSnapshot.load(paths.SNAPSHOT_PATH) if paths.SNAPSHOT_PATH.exists() else None
    client = _client()
    try:
        if snap is None:
            out("Downloading and parsing the guide from GitHub...")
            snap = guide_parser.refresh_snapshot(client, paths.GUIDE_DIR, paths.SNAPSHOT_PATH)
        elif newer := guide_parser.check_for_update(snap, client):
            answer = _ask(inp, f"The guide has changed ({snap.commit[:7]} -> {newer[:7]}). Re-parse it now? [Y/n] ")
            if answer.strip().lower() in ("", "y", "yes"):
                snap = guide_parser.refresh_snapshot(client, paths.GUIDE_DIR, paths.SNAPSHOT_PATH)
    except (httpx.HTTPError, OSError) as e:
        if snap is None:
            out("No saved guide snapshot and GitHub could not be reached. Connect to the internet, "
                "or pass --guide-dir with a local Guide_3DS checkout.")
            raise Stop(2) from e
        out(f"Couldn't check for guide updates ({e}); using the saved snapshot.")
    out(f"Guide: hacks-guide/Guide_3DS @ {snap.commit[:7]}")
    return snap


def choose_model(inp: Input, out: Output) -> str:
    import methods

    keys = list(methods.MODELS)
    out("Which console do you have?")
    for i, key in enumerate(keys, 1):
        out(f"  {i}) {methods.MODELS[key][0]}")
    while True:
        answer = _ask(inp, "Number: ").strip()
        if answer.isdigit() and 1 <= int(answer) <= len(keys):
            return keys[int(answer) - 1]
        out(f"Enter a number from 1 to {len(keys)}.")


def choose_method(snap, model: str, inp: Input, out: Output, state):
    import methods

    out("Open System Settings on the console. The version is at the bottom right of the top screen.")
    while True:
        text = _ask(inp, "System version (e.g. Ver. 11.17.0-50U): ")
        try:
            result = methods.select_method(snap, model, text)
            break
        except methods.VersionFormatError as e:
            out(str(e))
    _save_state(state, model=model, version=str(methods.parse_version(text, snap)), guide_commit=snap.commit)

    if result.outcome != "method":
        out(result.message)
        out(f"See: {result.url}")
        raise Stop(1)

    out(f"Method: {result.message}")
    out(f"Guide page: {result.url}  (guide commit {snap.commit[:7]})")
    if result.explanation:
        out("Why this method (from the guide's compatibility notes):")
        for line in result.explanation.splitlines():
            out(f"  {line}")
    slug = methods.resolve_os_variant(snap, result.page)
    if slug != result.page:
        import guide_parser

        out(f"Instructions for this computer: {guide_parser.page_url(slug)}")
    _save_state(state, method=slug)
    return slug


def choose_drive(inp: Input, out: Output):
    import sdcard

    while True:
        drives = sdcard.removable_only(sdcard.list_drives())
        if drives:
            break
        out("No removable drives found. Insert the SD card into this computer.")
        if _ask(inp, "Press Enter to look again (q to quit): ").strip().lower() == "q":
            raise Stop(1)

    out("Removable drives:")
    for i, d in enumerate(drives, 1):
        out(f"  {i}) {d.mount}  label={d.label or '(none)'}  {d.filesystem}  {d.total_bytes / 1e9:.1f}GB")
        for c in sdcard.evaluate(d):
            if c.level != "ok":
                out(f"       [{c.level}] {c.message}")
    while True:
        answer = _ask(inp, "Which drive is the 3DS SD card? Number: ").strip()
        if answer.isdigit() and 1 <= int(answer) <= len(drives):
            drive = drives[int(answer) - 1]
            if sdcard.usable(sdcard.evaluate(drive)):
                break
            out(f"{drive.mount} can't be used (see the problems listed above).")
        else:
            out(f"Enter a number from 1 to {len(drives)}.")

    target = sdcard.confirmation_target(drive)
    for _ in range(3):
        typed = _ask(inp, f"To confirm, type the drive label exactly ({target}): ")
        try:
            return drive, sdcard.confirm_drive(drive, typed)
        except sdcard.ConfirmationError as e:
            out(str(e))
    out("Drive not confirmed. Nothing was written.")
    raise Stop(1)


LUMA_VERSION_PROMPT = ("Which Luma3DS version does that menu show (e.g. 13.4)? "
                       "Type home if the console just boots to the HOME Menu: ")


def _fetch_specs(specs: list) -> list:
    """Resolve and download the GitHub files an update needs (verified like every download)."""
    import downloader
    import paths

    client = _client()
    return [downloader.fetch(downloader.resolve(s, client), client, paths.CACHE_DIR)
            for s in specs if s.kind == "github"]


def existing_cfw(snap, confirmed, inp: Input, out: Output) -> None:
    """The guide's "Checking for CFW" flow, then the Luma3DS / GodMode9 updates it leads to."""
    import guide_parser
    import sdcard
    import stager
    import updates
    from stages import prep

    out("This card already has luma/ and boot.firm, so custom firmware may already be installed.")
    out(f"Checking for CFW: {guide_parser.page_url(updates.CHECK_PAGE)}")
    page = snap.page(updates.CHECK_PAGE)
    steps = next((b["steps"] for b in page["blocks"] if b["heading"].casefold() == "instructions" and b["steps"]), [])
    if not steps:
        out(f"Couldn't read the steps from the guide; follow {guide_parser.page_url(updates.CHECK_PAGE)}")
        raise Stop(2)
    for n, step in enumerate(steps, 1):
        out(f"  {n}. {step['text']}")
    for warning in updates.wrong_menu_warnings(snap):
        out(f"  [WARNING] {warning}")
    slug, message = updates.route_existing_cfw(snap, _ask(inp, LUMA_VERSION_PROMPT))
    out(message)
    out(f"See: {guide_parser.page_url(slug)}")
    if slug != updates.LUMA_PAGE:
        raise Stop(0)

    installed = updates.detect_installed(confirmed.root)
    wanted = [updates.luma_update_info(snap)]
    if installed.godmode9_firm:
        gm9 = updates.gm9_update_info(snap)
        if gm9.message:
            out(gm9.message)
        elif _ask(inp, "GodMode9 is installed too. Update it as well? [y/N] ").strip().lower() in ("y", "yes"):
            wanted.append(gm9)
    if _ask(inp, "Update Luma3DS on this card now? [Y/n] ").strip().lower() in ("n", "no"):
        raise Stop(0)
    for info in wanted:
        if info.message:
            out(info.message)
            raise Stop(2)
    downloads = []
    for info in wanted:
        downloads += _fetch_specs(info.specs)
    planners = {"Luma3DS update": updates.plan_luma_update, "GodMode9 update": updates.plan_gm9_update}
    plans = [planners[info.name](snap, downloads, confirmed.root) for info in wanted]
    for p in plans:
        out(f"{p.name} - planned changes (dry run, nothing written yet):")
        out(p.plan.render() if p.plan else "(no plan)")
        if not p.ready:
            out(p.message or f"Follow the guide page instead: {p.url}")
            raise Stop(2)
    if _ask(inp, "Type WRITE to copy these files: ").strip() != "WRITE":
        out("Not confirmed. Nothing was written.")
        raise Stop(1)
    import paths

    for p in plans:
        current = next((d for d in sdcard.list_drives()
                        if d.serial == confirmed.drive.serial and d.label == confirmed.drive.label), None)
        log_path = paths.APP_DIR / "logs" / f"update-{datetime.now():%Y%m%d-%H%M%S}.log"
        stager.apply_stage(p.plan, confirmed, current, log_path)
        checks = prep.verify_staged(confirmed.root, p.plan, log_path)
        _print_checks(out, checks)
        if any(c.level == "fail" for c in checks):
            raise Stop(2)
        out(f"{p.name} done. Every write is logged in {log_path}.")
    out("Put the SD card back in the console and power it on. Luma3DS will use the new files.")
    raise Stop(0)


def check_card(confirmed, out: Output, snap=None, inp: Input | None = None) -> None:
    import guide_parser
    import sdcard

    if sdcard.detect_cfw(confirmed.root).already_modded:
        if snap is not None and inp is not None:
            existing_cfw(snap, confirmed, inp, out)
        out("This card already has luma/ and boot.firm, so custom firmware may already be installed.")
        out(f"Follow Checking for CFW instead: {guide_parser.page_url('checking-for-cfw')}")
        raise Stop(0)
    for issue in sdcard.scan_3ds_folder(confirmed.root).issues:
        out(f"[{issue.level}] {issue.message}" + (f"  ({guide_parser.page_url(issue.guide)})" if issue.guide else ""))


def get_downloads(snap, slug: str, inp: Input, out: Output) -> list:
    import downloader
    import paths

    client = _client()
    plan = downloader.plan_downloads(snap, [slug, "finalizing-setup"], client)
    out("Files to download:")
    for r in plan.resolved:
        check = "published sha256" if r.digest else "no published checksum (recorded on first download)"
        out(f"  {r.spec.name}: {r.asset_name} {r.tag} ({r.size:,} bytes, {r.published_at}) - {check}")

    prerequisites = list(getattr(plan, "prerequisites", []))
    prerequisites += [s for s in plan.manual if getattr(s, "kind", "") == "prerequisite"]
    for spec in prerequisites:
        out(f"Prerequisite: {spec.name}")
        out(f"  {spec.requirement}")
        if spec.url:
            out(f"  Get it from: {spec.url}")
        if _ask(inp, f"  Is {spec.name} installed on this computer? [y/N] ").strip().lower() not in ("y", "yes"):
            out(f"Stopping: install {spec.name} first, then run the assistant again. Nothing was written to the SD card.")
            raise Stop(1)

    downloads = list(downloader.download_all(plan, client, paths.CACHE_DIR, progress=None))
    for spec in plan.manual:
        if getattr(spec, "kind", "") == "prerequisite":
            continue
        out(f"Needed: {spec.requirement}")
        if spec.url:
            out(f"  Get it from: {spec.url}")
        path = _ask(inp, "  Path to the downloaded file (blank to stop): ").strip().strip('"')
        if not path:
            out(f"Stopping: {spec.name} is required. Nothing was written to the SD card.")
            raise Stop(1)
        downloads.append(downloader.import_manual(spec, Path(path), paths.CACHE_DIR))
    for d in downloads:
        out(f"  ok {d.name}: {Path(d.path).name} sha256={d.sha256[:16]}... ({d.verified}"
            + (", cached" if d.from_cache else "") + ")")
    return downloads


STEPS_DONE = "When you've finished these steps, press Enter (q = save and quit): "
CARD_IN = "Put the SD card in this computer, then press Enter (q = save and quit): "
FIX_PROMPT = "Fix this using the links above, then press Enter to check again (q = save and quit): "
WARN_PROMPT = "Press Enter to check again, or type continue to go on anyway (q = save and quit): "
RESUME_PROMPT = "Resume from there? [Y/n] "
START_OVER_PROMPT = "Start over and forget the saved progress? Files on the SD card are not changed. [y/N] "


@dataclass
class Session:
    snap: object
    state: object
    args: argparse.Namespace
    inp: Input
    out: Output
    slug: str | None = None
    downloads: list = field(default_factory=list)
    confirmed: object = None

    def ask(self, prompt: str) -> str:
        answer = _ask(self.inp, prompt)
        if answer.strip().lower() == "q":
            self.state.save()
            self.out("Progress saved. Run the assistant again to continue from this stage.")
            raise Stop(0)
        return answer


def find_card(sess: Session, prompt: str | None = CARD_IN):
    """The confirmed card, found again by its serial (the drive letter may change)."""
    import sdcard

    if prompt:
        sess.ask(prompt)
    st = sess.state
    while True:
        drive = next((d for d in sdcard.list_drives()
                      if d.serial == st.drive_serial and d.label == (st.drive_label or "")), None)
        if drive:
            return drive
        sess.ask(f"The SD card ({st.drive_label or st.drive_serial}) isn't connected. "
                 "Insert it and press Enter (q = save and quit): ")


def ensure_confirmed(sess: Session, drive):
    """A ConfirmedDrive for this card: reuse this session's, or have the user type the label again."""
    import sdcard

    if sess.confirmed is not None:
        try:
            sess.confirmed.revalidate(drive)
            return sess.confirmed
        except sdcard.ConfirmationError:
            pass
    target = sdcard.confirmation_target(drive)
    for _ in range(3):
        typed = _ask(sess.inp, f"Before writing to {drive.mount}, type the drive label exactly ({target}): ")
        try:
            sess.confirmed = sdcard.confirm_drive(drive, typed)
            return sess.confirmed
        except sdcard.ConfirmationError as e:
            sess.out(str(e))
    sess.out("Drive not confirmed. Nothing was written.")
    raise Stop(1)


def stage_files(sess: Session, st, drive):
    """Copy this section's files: dry-run plan, typed WRITE, apply, then read everything back."""
    import paths
    import sdcard
    import stager
    from stages import prep

    out = sess.out
    confirmed = ensure_confirmed(sess, drive)
    plan = stager.plan_stage(sess.snap, st.slug, sess.downloads, confirmed.root, section=st.number)
    out("Planned changes to the SD card (dry run, nothing written yet):")
    out(plan.render())
    if getattr(plan, "unparsed", None):
        out("The assistant can't do every file step in this section by itself, so it won't write anything. "
            f"Follow the guide page instead: {st.url}")
        raise Stop(2)
    if sess.args.dry_run:
        out("Dry run: stopping before any write.")
        raise Stop(0)
    if _ask(sess.inp, "Type WRITE to copy these files: ").strip() != "WRITE":
        out("Not confirmed. Nothing was written.")
        raise Stop(1)
    current = next((d for d in sdcard.list_drives() if d.serial == drive.serial and d.label == drive.label), None)
    log_path = paths.APP_DIR / "logs" / f"stage-{datetime.now():%Y%m%d-%H%M%S}.log"
    stager.apply_stage(plan, confirmed, current, log_path)
    out(f"Files copied. Every write is logged in {log_path}.")
    checks = prep.verify_staged(confirmed.root, plan, log_path)
    _print_checks(out, checks)
    if any(c.level == "fail" for c in checks):
        raise Stop(2)
    sess.state.mark_done(f"{st.key}:files")
    sess.state.save()
    return plan


def _print_checks(out: Output, checks) -> None:
    import guide_parser

    for c in checks:
        link = f"  ({guide_parser.page_url(c.guide)})" if c.guide and c.level != "ok" else ""
        out(f"  [{c.level}] {c.message}{link}")


def print_header(sess: Session, st, index: int, total: int) -> None:
    out = sess.out
    out("")
    out("=" * 72)
    out(f"Stage {index}/{total}: {st.page_title}" + (f" - Section {st.number}: {st.title}" if st.number else f" - {st.title}"))
    out("=" * 72)
    out(f"Guide: {st.url}")
    out("If something goes wrong: " + "  ".join(st.troubleshooting))


def print_steps(sess: Session, st, plan=None) -> None:
    out = sess.out
    if st.section is None:
        return
    for line in st.section["intro"]:
        out(line)
    for note in st.section["notes"]:
        if note["kind"] in ("warning", "danger"):
            out(f"[{note['kind'].upper()}] {note['text']}")
    done_steps = {op.step for op in getattr(plan, "ops", [])} if plan is not None else set()
    copied = plan is not None or f"{st.key}:files" in sess.state.completed
    for n, step in enumerate(st.section["steps"], 1):
        mark = ""
        if copied and step["text"].strip() in done_steps:
            mark = "   <- done by the assistant"
        out(f"{n:>3}. {step['text']}{mark}")
        for d in step["details"]:
            out(f"       {d}")
        if note := st.annotate(step["text"]):
            out(f"       >>> {note}")


def run_stage(sess: Session, st, index: int, total: int) -> None:
    sess.state.stage = st.key
    sess.state.save()
    print_header(sess, st, index, total)
    if st.key == "backup":
        run_backup(sess)
    else:
        plan = None
        if st.stages_files and f"{st.key}:files" not in sess.state.completed:
            sess.out("The assistant copies this section's files to the SD card first.")
            drive = find_card(sess, None if index == 1 else "Power off the console. " + CARD_IN)
            plan = stage_files(sess, st, drive)
            sess.out("Now follow this section. Steps marked as done were done by the assistant:")
        print_steps(sess, st, plan)
        sess.ask(STEPS_DONE)
        if st.verifiers:
            verify_stage(sess, st)
    sess.state.mark_done(st.key)
    sess.state.save()


def verify_stage(sess: Session, st) -> None:
    from pathlib import Path as _P

    prompt = CARD_IN
    while True:
        drive = find_card(sess, prompt)
        checks = [c for v in st.verifiers for c in v(_P(drive.mount))]
        sess.out("Checking the SD card:")
        _print_checks(sess.out, checks)
        if all(c.level == "ok" for c in checks):
            return
        if any(c.level == "fail" for c in checks):
            prompt = FIX_PROMPT
            continue
        if sess.ask(WARN_PROMPT).strip().lower() == "continue":
            return
        prompt = None


def run_backup(sess: Session) -> None:
    import backup

    drive = find_card(sess, "Power off the console. " + CARD_IN)
    try:
        dest = backup.backup_to_pc(Path(drive.mount), sess.args.backup_dir)
    except backup.BackupError as e:
        bar = "!" * 72
        sess.out(bar)
        sess.out(str(e))
        sess.out(bar)
        sess.state.save()
        raise Stop(2) from e
    sess.state.backup_dir = str(dest)
    sess.out(f"Backups copied and verified: {dest}")
    for p in sorted(dest.iterdir()):
        sess.out(f"  {p.name}  {p.stat().st_size:,} bytes")
    sess.out("Copy this folder to at least one more place too (cloud storage, an external drive).")
    sess.out("The guide's last step: you may now delete the two SysNAND files from /gm9/backups/ on the SD "
             "card (keep essential.exefs). The assistant doesn't delete anything from your card.")


# --- setup and resume -------------------------------------------------------------------

def _remember_downloads(state, downloads) -> None:
    state.downloads = [{"name": d.name, "path": str(d.path), "sha256": d.sha256, "verified": d.verified}
                       for d in downloads]


def _restore_downloads(state) -> list | None:
    import types

    from stages.prep import _sha256

    if not state.downloads:  # quit before downloading: there is nothing to restore
        return None
    restored = []
    for d in state.downloads:
        p = Path(d["path"])
        if not p.is_file() or _sha256(p) != d["sha256"]:
            return None
        restored.append(types.SimpleNamespace(name=d["name"], path=p, sha256=d["sha256"],
                                              verified=d.get("verified", "recorded"), from_cache=True))
    return restored


def resume(sess: Session) -> bool:
    """Continue saved progress if there is some and the user wants to; False to start fresh."""
    import methods
    import stages

    st, out = sess.state, sess.out
    if sess.args.restart or not st.method or not st.drive_serial:
        return False
    all_stages = stages.build_stages(sess.snap, st.method)
    pending = [s for s in all_stages if s.key not in st.completed]
    label = methods.MODELS.get(st.model, (st.model,))[0]
    out(f"Saved progress: {label}, {st.version}, {st.method} ({len(all_stages) - len(pending)} of "
        f"{len(all_stages)} stages done).")
    if not pending:
        out(f"Setup is already finished. Backups: {st.backup_dir}. Use --restart to set up another console.")
        raise Stop(0)
    nxt = pending[0]
    out(f"Next: {nxt.page_title}" + (f" - Section {nxt.number}: {nxt.title}" if nxt.number else f" - {nxt.title}"))
    if _ask(sess.inp, RESUME_PROMPT).strip().lower() in ("n", "no"):
        if _ask(sess.inp, START_OVER_PROMPT).strip().lower() in ("y", "yes"):
            st.reset()
            st.save()
            return False
        raise Stop(0)
    if st.guide_commit and st.guide_commit != sess.snap.commit:
        out(f"Note: the guide has changed since you started ({st.guide_commit[:7]} -> {sess.snap.commit[:7]}). "
            "The steps shown come from the current guide.")
    sess.slug = st.method
    sess.downloads = _restore_downloads(st)
    if sess.downloads is None:
        out("Some downloaded files are missing from the cache; fetching them again.")
        sess.downloads = get_downloads(sess.snap, sess.slug, sess.inp, out)
        _remember_downloads(st, sess.downloads)
        st.save()
    return True


def fresh_setup(sess: Session) -> None:
    st = sess.state
    st.reset()
    model = choose_model(sess.inp, sess.out)
    sess.slug = choose_method(sess.snap, model, sess.inp, sess.out, st)
    drive, sess.confirmed = choose_drive(sess.inp, sess.out)
    check_card(sess.confirmed, sess.out, sess.snap, sess.inp)
    _save_state(st, drive_label=drive.label, drive_serial=drive.serial)
    sess.downloads = get_downloads(sess.snap, sess.slug, sess.inp, sess.out)
    _remember_downloads(st, sess.downloads)
    st.save()


def run_all_stages(sess: Session) -> None:
    import stages

    all_stages = stages.build_stages(sess.snap, sess.slug)
    if sess.args.dry_run:
        # Show the next file-copy plan only: no console steps, nothing marked as done.
        pending = [s for s in all_stages if s.stages_files and f"{s.key}:files" not in sess.state.completed]
        if not pending:
            sess.out("Dry run: there are no file copies left to show.")
            raise Stop(0)
        st = pending[0]
        print_header(sess, st, all_stages.index(st) + 1, len(all_stages))
        stage_files(sess, st, find_card(sess, None))  # stops before any write
    for i, st in enumerate(all_stages, 1):
        if st.key not in sess.state.completed:
            run_stage(sess, st, i, len(all_stages))
    sess.state.stage = None
    sess.state.save()
    sess.out("")
    sess.out("Setup complete! Your console is running Luma3DS custom firmware, and your backups are safe at:")
    sess.out(f"  {sess.state.backup_dir}")


# --- entry ------------------------------------------------------------------------------

def _guide_error_link(e: Exception) -> str | None:
    return getattr(e, "url", None)


def run(argv: list[str] | None = None, inp: Input = input, out: Output = print) -> int:
    args = parse_args(argv)
    try:
        import state as state_mod

        try:
            state = state_mod.State.load()
        except state_mod.StateError as e:
            out(f"Stopped: {e}")
            return 2
        snap = load_snapshot(args, inp, out)
        sess = Session(snap, state, args, inp, out)
        if not resume(sess):
            fresh_setup(sess)
        run_all_stages(sess)
        return 0
    except Stop as s:
        return s.code
    except KeyboardInterrupt:
        out("\nInterrupted. Progress up to the last finished stage is saved; run the assistant again to resume.")
        return 130
    except Exception as e:
        link = _guide_error_link(e)
        known = type(e).__name__ in {"GuideParseError", "RoutingError", "DownloadError", "ChecksumMismatch",
                                     "StageError", "ConfirmationError", "ProtectedPathError"}
        if not known:
            raise
        out(f"Stopped: {getattr(e, 'reason', None) or e}")
        if link:
            out(f"See: {link}")
        if type(e).__name__ == "ChecksumMismatch":
            out("A download did not match its checksum. Nothing was written to the SD card.")
        return 2
