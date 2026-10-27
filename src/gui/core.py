"""Everything the GUI pages do, without any UI: one Core per window.

Each method mirrors a step of the CLI flow (cli.py) and calls the same modules, so the safety
rules live in one place: confirm_drive() for the typed label, assert_writable() and
ConfirmedDrive.revalidate() inside apply_stage(), checksums in downloader, read-back checks
in stages.prep, SD checks in the stage verifiers, and backup.backup_to_pc().

Methods may block (network, SD card); pages call them through Wizard.run_bg().
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

import paths

Progress = Callable[..., None]
WALK_METHOD = "installing-boot9strap-(mset9-cli)"  # the route that has a baby-step walkthrough


@dataclass
class ResumeInfo:
    console: str
    version: str
    method: str
    done: int
    total: int
    next_title: str | None  # None when everything is finished
    backup_dir: str | None


class Core:
    def __init__(self, args):
        """`args` is cli.parse_args(...): guide_dir, dry_run, backup_dir, restart."""
        import state as state_mod

        self.args = args
        self.state = state_mod.State.load()
        self.snap = None
        self.slug: str | None = None
        self.route = None  # methods.RouteResult of the last choose()
        self.downloads: list = []
        self.drive = None  # sdcard.DriveInfo picked by the user
        self.confirmed = None  # sdcard.ConfirmedDrive for this window session
        self.stage_list: list = []

    # --- guide -------------------------------------------------------------------------

    def load_guide(self) -> str | None:
        """Load the guide snapshot. Returns a newer commit hash if the guide changed, else None."""
        import guide_parser
        import httpx

        if self.args.guide_dir:
            d = Path(self.args.guide_dir)
            docs = d / "docs" if (d / "docs").is_dir() else d
            self.snap = guide_parser.build_snapshot(docs, "local")
            return None
        snap = guide_parser.GuideSnapshot.load(paths.SNAPSHOT_PATH) if paths.SNAPSHOT_PATH.exists() else None
        from cli import _client

        try:
            if snap is None:
                snap = guide_parser.refresh_snapshot(_client(), paths.GUIDE_DIR, paths.SNAPSHOT_PATH)
                self.snap = snap
                return None
            self.snap = snap
            return guide_parser.check_for_update(snap, _client())
        except (httpx.HTTPError, OSError):
            if snap is None:
                raise
            self.snap = snap
            return None

    def refresh_guide(self) -> None:
        import guide_parser
        from cli import _client

        self.snap = guide_parser.refresh_snapshot(_client(), paths.GUIDE_DIR, paths.SNAPSHOT_PATH)

    @property
    def guide_commit(self) -> str:
        return self.snap.commit[:7] if self.snap else "?"

    # --- resume ------------------------------------------------------------------------

    def resume_info(self) -> ResumeInfo | None:
        import methods
        import stages

        st = self.state
        if self.args.restart or not st.method or not st.drive_serial:
            return None
        all_stages = stages.build_stages(self.snap, st.method)
        pending = [s for s in all_stages if s.key not in st.completed]
        nxt = pending[0] if pending else None
        return ResumeInfo(
            console=methods.MODELS.get(st.model, (st.model,))[0], version=st.version or "", method=st.method,
            done=len(all_stages) - len(pending), total=len(all_stages),
            next_title=(stage_title(nxt) if nxt else None), backup_dir=st.backup_dir)

    def resume(self) -> bool:
        """Continue saved progress. True if the cached downloads are intact (else download again)."""
        from cli import _restore_downloads

        self.slug = self.state.method
        self.stage_list = self._build_stages()
        restored = _restore_downloads(self.state)
        self.downloads = restored or []
        return restored is not None

    def start_over(self) -> None:
        self.state.reset()
        self.state.save()
        self.slug = self.route = self.drive = self.confirmed = None
        self.downloads = []

    # --- console -----------------------------------------------------------------------

    @staticmethod
    def models() -> list[tuple[str, str]]:
        import methods

        return [(key, label) for key, (label, _) in methods.MODELS.items()]

    def choose(self, model: str, version_text: str):
        """Route with the guide's own script. Raises methods.VersionFormatError / RoutingError."""
        import methods

        # The near-universal route (MSET9 for 11.4+, soundhax for older), from the guide's own
        # compatibility checks; the guide's full routing only when neither applies.
        result = methods.select_simple(self.snap, model, version_text)
        version = str(methods.parse_version(version_text, self.snap))
        luma = self.state.luma
        self.state.reset()
        self._save(model=model, version=version, guide_commit=self.snap.commit, luma=luma)
        self.route = result
        self.slug = None
        if result.outcome == "method":
            self.slug = methods.resolve_os_variant(self.snap, result.page)
            self._save(method=self.slug, walk=self.slug == WALK_METHOD and not self.args.dry_run)
        return result

    @property
    def walk_mode(self) -> bool:
        return bool(self.state.walk) and self.slug == WALK_METHOD and not self.args.dry_run

    def method_url(self) -> str:
        import guide_parser

        return guide_parser.page_url(self.slug)

    # --- SD card -----------------------------------------------------------------------

    def drives(self) -> list[tuple[object, list]]:
        """Removable drives with their checks: [(DriveInfo, [Check])]."""
        import sdcard

        return [(d, sdcard.evaluate(d)) for d in sdcard.removable_only(sdcard.list_drives())]

    @staticmethod
    def confirmation_target(drive) -> str:
        import sdcard

        return sdcard.confirmation_target(drive)

    def confirm(self, drive, typed: str):
        """The typed-label check. Raises sdcard.ConfirmationError."""
        import sdcard

        self.confirmed = sdcard.confirm_drive(drive, typed)
        self.drive = drive
        self._save(drive_label=drive.label, drive_serial=drive.serial)
        return self.confirmed

    def card_status(self) -> tuple[bool, list]:
        """(already has CFW, Nintendo 3DS folder issues) for the confirmed card."""
        import sdcard

        root = self.confirmed.root
        return sdcard.detect_cfw(root).already_modded, sdcard.scan_3ds_folder(root).issues

    def find_card(self):
        """The saved card, found again by serial and label (the drive letter may change), or None."""
        import sdcard

        st = self.state
        return next((d for d in sdcard.list_drives()
                     if d.serial == st.drive_serial and d.label == (st.drive_label or "")), None)

    def needs_label_again(self, drive) -> bool:
        """True if writing to `drive` needs the label typed again (new session or new mount)."""
        import sdcard

        if self.confirmed is None:
            return True
        try:
            self.confirmed.revalidate(drive)
            return False
        except sdcard.ConfirmationError:
            return True

    def reconfirm(self, drive, typed: str):
        import sdcard

        self.confirmed = sdcard.confirm_drive(drive, typed)
        self.drive = drive
        return self.confirmed

    # --- downloads ---------------------------------------------------------------------

    def plan_downloads(self):
        """downloader.DownloadPlan for the method page and Finalizing Setup (resolves versions)."""
        import downloader
        from cli import _client

        plan = downloader.plan_downloads(self.snap, [self.slug, "finalizing-setup"], _client())
        if self.walk_mode:  # the assistant runs the MSET9 script itself, so no Python install is needed
            plan.prerequisites = []
            plan.manual = [s for s in plan.manual if s.kind != "prerequisite"]
        return plan

    def download(self, plan, manual_files: dict[str, str], progress: Progress | None = None) -> list:
        """Fetch and verify everything; `manual_files` maps a manual item's name to a local path.

        progress(name, done_bytes, total_bytes) per chunk. Raises downloader errors (checksum
        mismatch aborts everything).
        """
        import downloader
        from cli import _client, _remember_downloads

        client = _client()
        got = [downloader.fetch(r, client, paths.CACHE_DIR, progress) for r in plan.resolved]
        for spec in plan.manual:
            if spec.kind == "prerequisite":
                continue
            got.append(downloader.import_manual(spec, Path(manual_files[spec.name]), paths.CACHE_DIR))
        self.downloads = got
        _remember_downloads(self.state, got)
        self.state.save()
        if self.luma_choice.custom:
            self.fetch_luma_build()
        return got

    # --- which Luma ----------------------------------------------------------------------

    @property
    def luma_choice(self):
        import luma_build

        return luma_build.LumaChoice.from_dict(self.state.luma)

    def set_luma_choice(self, choice) -> None:
        self.state.luma = None if not choice.custom else choice.to_dict()
        self.state.save()

    def fetch_luma_build(self):
        """Fetch/import the chosen fork's boot.firm (checked) and remember where it is."""
        import luma_build
        from cli import _client

        build = luma_build.resolve(self.luma_choice, _client(), paths.CACHE_DIR)
        self.state.luma = {**self.luma_choice.to_dict(), "firm": str(build.firm), "version": build.version,
                           "sha256": build.sha256, "verified": build.verified}
        self.state.save()
        return build

    # --- stages ------------------------------------------------------------------------

    def _build_stages(self) -> list:
        import stages

        return stages.build_stages(self.snap, self.slug)

    def stages(self) -> list:
        if not self.stage_list:
            self.stage_list = self._build_stages()
        return self.stage_list

    def first_pending(self) -> int:
        """Index of the first stage not done (len(stages) when finished)."""
        done = self.state.completed
        return next((i for i, s in enumerate(self.stages()) if s.key not in done), len(self.stages()))

    def first_copy_stage(self) -> int | None:
        """For a dry run: index of the first stage that still has files to copy."""
        done = self.state.completed
        return next((i for i, s in enumerate(self.stages())
                     if s.stages_files and f"{s.key}:files" not in done), None)

    def files_done(self, stage) -> bool:
        return f"{stage.key}:files" in self.state.completed

    def begin(self, stage) -> None:
        self.state.stage = stage.key
        self.state.save()

    def plan_files(self, stage, drive):
        """The dry-run plan (stager.StagePlan) for a file-copy stage on `drive`."""
        import stager

        return stager.plan_stage(self.snap, stage.slug, self.downloads, Path(drive.mount), section=stage.number)

    def write_files(self, stage, plan, progress: Progress | None = None) -> list:
        """Copy the plan to the confirmed card and read everything back. Returns the checks.

        progress(done_items, total_items, dest). Needs self.confirmed for this card.
        """
        import stager
        from stages import prep

        if self.args.dry_run:
            raise RuntimeError("Dry run: the assistant never writes to the SD card in a dry run.")
        current = self.find_card()
        log_path = paths.APP_DIR / "logs" / f"stage-{datetime.now():%Y%m%d-%H%M%S}.log"
        stager.apply_stage(plan, self.confirmed, current, log_path, progress)
        checks = prep.verify_staged(self.confirmed.root, plan, log_path)
        if not any(c.level == "fail" for c in checks):
            self.state.mark_done(f"{stage.key}:files")
            self.state.save()
        self.last_log = log_path
        return checks

    def verify(self, stage) -> list | None:
        """Run the stage's SD checks on the card; None if the card isn't connected."""
        drive = self.find_card()
        if drive is None:
            return None
        return [c for v in stage.verifiers for c in v(Path(drive.mount))]

    def complete(self, stage) -> None:
        self.state.mark_done(stage.key)
        if stage.key == self.state.stage:
            self.state.stage = None
        self.state.save()

    def backup(self, progress: Progress | None = None) -> Path:
        """Copy and verify the backups to args.backup_dir. Raises backup.BackupError(s) loudly.

        progress(done_bytes, total_bytes, label). Returns the timestamped folder.
        """
        import backup

        drive = self.find_card()
        if drive is None:
            raise backup.BackupError("The SD card isn't connected. Put it in this computer and try again.")
        dest = backup.backup_to_pc(Path(drive.mount), self.args.backup_dir, progress=progress)
        self.state.backup_dir = str(dest)
        self.state.save()
        return dest

    # --- cards that already have CFW ---------------------------------------------------

    def cfw_steps(self) -> tuple[list[str], list[str]]:
        """(the guide's Checking for CFW steps, its warnings)."""
        import updates

        page = self.snap.page(updates.CHECK_PAGE)
        steps = next((b["steps"] for b in page["blocks"]
                      if b["heading"].casefold() == "instructions" and b["steps"]), [])
        return [s["text"] for s in steps], updates.wrong_menu_warnings(self.snap)

    def route_cfw(self, luma_version_text: str) -> tuple[str, str, bool]:
        """(guide slug, message, whether the assistant can update from here)."""
        import updates

        slug, message = updates.route_existing_cfw(self.snap, luma_version_text)
        return slug, message, slug == updates.LUMA_PAGE

    def gm9_installed(self) -> bool:
        import updates

        return updates.detect_installed(self.confirmed.root).godmode9_firm

    def plan_updates(self, with_gm9: bool) -> list:
        """Download what the guide's update steps need and plan them (updates.UpdatePlan list)."""
        import updates
        from cli import _fetch_specs

        infos = [updates.luma_update_info(self.snap)]
        if with_gm9:
            infos.append(updates.gm9_update_info(self.snap))
        for info in infos:
            if info.message:
                raise RuntimeError(info.message)
        downloads = []
        for info in infos:
            downloads += _fetch_specs(info.specs)
        planners = {"Luma3DS update": updates.plan_luma_update, "GodMode9 update": updates.plan_gm9_update}
        return [planners[i.name](self.snap, downloads, self.confirmed.root) for i in infos]

    def write_updates(self, plans: list, progress: Progress | None = None) -> list:
        import stager
        from stages import prep

        if self.args.dry_run:
            raise RuntimeError("Dry run: the assistant never writes to the SD card in a dry run.")
        checks = []
        for p in plans:
            log_path = paths.APP_DIR / "logs" / f"update-{datetime.now():%Y%m%d-%H%M%S}.log"
            stager.apply_stage(p.plan, self.confirmed, self.find_card() or self.drive, log_path, progress)
            checks += prep.verify_staged(self.confirmed.root, p.plan, log_path)
        return checks

    # --- baby-step walkthrough -----------------------------------------------------------

    def walk_steps(self) -> list:
        from walkthrough import Step, mset9

        steps = mset9.steps()
        if self.luma_choice.custom:
            name = self.luma_choice.label
            steps += [
                Step("install-fork", "auto", f"Switching to {name}", action="install_luma_build", where="pc"),
                Step("try-fork", "you", f"Start the console with {name}",
                     do=("Put the SD card in the console", "Power on the console"),
                     expect=f"The console starts up to the HOME Menu, now running {name}.",
                     if_not="If the light turns white and it shuts off, or the screen stays black: power off, put "
                            "the SD card in this computer, finish here, and click \"Put official Luma3DS back\" "
                            "on the last page.",
                     where="console"),
            ]
        return steps

    def walk_done(self, step_id: str) -> bool:
        return f"wt:{step_id}" in self.state.completed

    def walk_mark(self, step_id: str) -> None:
        self.state.mark_done(f"wt:{step_id}")
        self.state.save()

    def walk_drift(self) -> list[str]:
        from walkthrough import mset9

        return mset9.guide_drift(self.snap)

    def _card_for_walk(self):
        """(drive, ConfirmedDrive) for the saved card, or an ActionResult saying what's wrong."""
        import sdcard
        from walkthrough import ActionResult

        drive = self.find_card()
        if drive is None:
            return ActionResult(False, "The SD card isn't in this computer.",
                                retry_hint="Put the SD card in this computer, then click Try again.")
        if self.needs_label_again(drive):
            if self.confirmed is None:
                return ActionResult(False, "The drive hasn't been confirmed in this session.",
                                    retry_hint="Close and reopen the assistant, then confirm the drive label.")
            # Same card (serial and label match the one typed this session), new drive letter.
            self.confirmed = sdcard.confirm_drive(drive, sdcard.confirmation_target(drive))
        return drive, self.confirmed

    def _mset9_index(self, root: Path) -> int:
        import methods
        import mset9_runner

        v = methods.parse_version(self.state.version, self.snap)
        return mset9_runner.console_index(root, methods.model_family(self.state.model), int(v.major), int(v.minor))

    def _copy_stage(self, slug: str, progress):
        from walkthrough import ActionResult

        got = self._card_for_walk()
        if isinstance(got, ActionResult):
            return got
        drive, _ = got
        stage = next(s for s in self._stages_for(slug) if s.stages_files)
        plan = self.plan_files(stage, drive)
        if plan.unparsed:
            return ActionResult(False, "Some of the guide's file steps couldn't be done automatically.",
                                details=list(plan.unparsed), retry_hint="Use the guide link for this part.")
        checks = self.write_files(stage, plan, progress)
        bad = [c.message for c in checks if c.level == "fail"]
        if bad:
            return ActionResult(False, "The files didn't read back correctly from the SD card.", details=bad,
                                retry_hint="The card may be failing. Try again; if it keeps failing, use another card.")
        n = sum(op.kind != "mkdir" for op in plan.ops)
        return ActionResult(True, f"Copied {n} files and checked every one.")

    def _stages_for(self, slug: str) -> list:
        return [s for s in self.stages() if s.slug == slug]

    def _from_checks(self, checks, ok_message: str):
        from walkthrough import ActionResult

        bad = [c.message for c in checks if c.level == "fail"]
        notes = [c.message for c in checks if c.level == "warn"]
        if bad:
            return ActionResult(False, bad[0], details=bad[1:] + notes)
        return ActionResult(True, ok_message, details=notes)

    def walk_action(self, name: str, progress=None):
        import mset9_runner
        from stages import boot9strap, finalize, mset9
        from walkthrough import ActionResult

        if name == "copy_mset9":
            return self._copy_stage(WALK_METHOD, progress)
        if name == "copy_finalize":
            return self._copy_stage("finalizing-setup", progress)
        if name == "backup":
            import backup as backup_mod

            try:
                dest = self.backup(progress)
            except backup_mod.BackupError as e:
                return ActionResult(False, str(e).splitlines()[0], details=str(e).splitlines()[1:],
                                    retry_hint="Re-run the setup script (hold Start while powering on), then try again.")
            files = [f"{p.name}  ({p.stat().st_size:,} bytes)" for p in sorted(dest.iterdir())]
            return ActionResult(True, f"Backups copied and checked: {dest}", details=files + [
                "Keep a second copy somewhere else too (cloud storage or another drive)."])
        if name == "install_luma_build":
            return self.install_luma_build()

        got = self._card_for_walk()
        if isinstance(got, ActionResult):
            return got
        drive, _ = got
        root = Path(drive.mount)
        if name == "check_b9s":
            return self._from_checks(boot9strap.verify_installed(root), "Custom firmware is on the card.")
        if name == "check_mset9_removed":
            checks = mset9.verify_removed(root)
            if any(c.level == "fail" for c in checks) and "wt:mset9-remove" in self.state.completed:
                # The removal step said it worked but the card disagrees: run it again (the runner
                # finishes a half-done removal with the guide's manual rename), then check again.
                try:
                    redo = mset9_runner.run(root, self._mset9_index(root), "remove")
                except mset9_runner.Mset9Error as e:
                    return ActionResult(False, str(e))
                if not redo.ok:
                    return ActionResult(False, redo.message, details=redo.errors)
                checks = mset9.verify_removed(root)
            return self._from_checks(checks, "Your data is back in place.")
        if name == "check_backups":
            return self._from_checks(finalize.verify_backups(root), "Your backups are on the card.")
        actions = {"mset9_create": "create", "mset9_ready": "ready", "mset9_inject": "inject",
                   "mset9_remove": "remove"}
        if name in actions:
            try:
                result = mset9_runner.run(root, self._mset9_index(root), actions[name])
            except mset9_runner.Mset9Error as e:
                return ActionResult(False, str(e))
            hints = {"HOME menu extdata": "Put the card in the console and turn it on once.",
                     "Mii Maker extdata": "Put the card in the console and open Mii Maker once.",
                     "Title database": "On the console: System Settings > Data Management > Nintendo 3DS > "
                                       "Software > Reset."}
            missing = [hints[k] for k, ok in result.report.items() if not ok and k in hints]
            return ActionResult(result.ok, result.message, details=result.errors + missing,
                                retry_hint=("Do these on the console, then put the card back in this computer:"
                                            if missing else ""))
        return ActionResult(False, f"Unknown step action {name!r}.")

    # --- fork install / restore -----------------------------------------------------------

    OFFICIAL_BACKUP = "official-boot.firm"

    def install_luma_build(self):
        """Replace /boot.firm with the chosen fork, keeping the official one on this PC."""
        import stager
        from walkthrough import ActionResult

        got = self._card_for_walk()
        if isinstance(got, ActionResult):
            return got
        drive, confirmed = got
        luma = self.state.luma or {}
        firm = Path(luma.get("firm", ""))
        if not firm.is_file():
            build = self.fetch_luma_build()
            firm, luma = build.firm, self.state.luma
        current = Path(drive.mount) / "boot.firm"
        keep = paths.APP_DIR / self.OFFICIAL_BACKUP
        if current.is_file() and not keep.exists():
            keep.parent.mkdir(parents=True, exist_ok=True)
            keep.write_bytes(current.read_bytes())
        plan = stager.StagePlan("custom-luma", "-", Path(drive.mount),
                                [stager.StageOp("copy", str(firm), "boot.firm", current.exists(),
                                                f"Install {self.luma_choice.label}")])
        log = paths.APP_DIR / "logs" / f"luma-{datetime.now():%Y%m%d-%H%M%S}.log"
        stager.apply_stage(plan, confirmed, self.find_card(), log)
        if current.read_bytes() != firm.read_bytes():
            return ActionResult(False, "boot.firm didn't read back correctly from the SD card.")
        return ActionResult(True, f"Installed {self.luma_choice.label} ({luma.get('version', '')}). "
                                  f"Official Luma3DS is saved on this computer in case you want it back.")

    def restore_official(self):
        """Put the saved official boot.firm back on the card."""
        import stager
        from walkthrough import ActionResult

        got = self._card_for_walk()
        if isinstance(got, ActionResult):
            return got
        drive, confirmed = got
        keep = paths.APP_DIR / self.OFFICIAL_BACKUP
        if not keep.is_file():
            return ActionResult(False, "No saved official boot.firm was found on this computer.")
        plan = stager.StagePlan("official-luma", "-", Path(drive.mount),
                                [stager.StageOp("copy", str(keep), "boot.firm", True, "Restore official Luma3DS")])
        stager.apply_stage(plan, confirmed, self.find_card(),
                           paths.APP_DIR / "logs" / f"luma-restore-{datetime.now():%Y%m%d-%H%M%S}.log")
        return ActionResult(True, "Official Luma3DS is back on the SD card. Put it in the console and power on.")

    # --- helpers -----------------------------------------------------------------------

    def _save(self, **fields) -> None:
        for k, v in fields.items():
            setattr(self.state, k, v)
        self.state.save()


def stage_title(stage) -> str:
    return f"{stage.page_title} - Section {stage.number}: {stage.title}" if stage.number else \
        f"{stage.page_title} - {stage.title}"
