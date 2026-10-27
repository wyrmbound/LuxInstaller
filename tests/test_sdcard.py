from __future__ import annotations

import copy
import json
import os
from dataclasses import replace
from pathlib import Path

import pytest

import sdcard
from guide_parser import GuideSnapshot
from sdcard import (
    ConfirmationError, ConfirmedDrive, DriveInfo, ProtectedPathError, assert_writable, confirm_drive,
    detect_cfw, evaluate, nominal_gb, scan_3ds_folder,
)

GB = 1_000_000_000

GOOD = DriveInfo(
    mount="E:\\", label="3DS", filesystem="FAT32", cluster_size=32768,
    total_bytes=31_900_000_000, free_bytes=20 * GB, removable=True, system=False,
    partition_style="MBR", device="E:", serial="1234-ABCD",
)


def levels(checks):
    return {c.name: c.level for c in checks}


# --- drive checks ----------------------------------------------------------------------

def test_good_card_passes():
    checks = evaluate(GOOD)
    assert all(c.level == "ok" for c in checks), checks
    assert sdcard.usable(checks)


@pytest.mark.parametrize("change, name, level", [
    ({"removable": False}, "removable", "fail"),
    ({"system": True}, "system", "fail"),
    ({"filesystem": "exFAT"}, "filesystem", "fail"),
    ({"filesystem": "NTFS"}, "filesystem", "fail"),
    ({"filesystem": "FAT16"}, "filesystem", "fail"),  # FAT16 only for cards of 2GB or less
    ({"partition_style": "GPT"}, "partition", "fail"),
    ({"partition_style": None}, "partition", "info"),
    ({"cluster_size": 4096}, "cluster", "warn"),
    ({"cluster_size": 65536}, "cluster", "ok"),
    ({"cluster_size": None}, "cluster", "info"),
    ({"total_bytes": 980_000_000}, "capacity", "fail"),
    ({"total_bytes": 255_000_000_000}, "capacity", "warn"),
    ({"total_bytes": 150_000_000_000}, "capacity", "warn"),
    ({"free_bytes": 1 * GB}, "free_space", "fail"),
])
def test_check_levels(change, name, level):
    assert levels(evaluate(replace(GOOD, **change)))[name] == level


def test_fat16_is_fine_on_a_2gb_card():
    small = replace(GOOD, filesystem="FAT16", total_bytes=1_960_000_000, free_bytes=1_900_000_000)
    assert levels(evaluate(small))["filesystem"] == "ok"


def test_free_space_includes_files_to_stage():
    drive = replace(GOOD, free_bytes=int(1.4 * GB))
    assert levels(evaluate(drive))["free_space"] == "ok"
    assert levels(evaluate(drive, extra_bytes=200_000_000))["free_space"] == "fail"


def test_failing_checks_link_to_the_guide():
    fs = next(c for c in evaluate(replace(GOOD, filesystem="exFAT"), host_os="Windows") if c.name == "filesystem")
    assert fs.guide == "formatting-sd-(windows)"
    fs = next(c for c in evaluate(replace(GOOD, filesystem="exFAT"), host_os="macOS") if c.name == "filesystem")
    assert fs.guide == "formatting-sd-(mac)"


@pytest.mark.parametrize("total, nominal", [
    (1_960_000_000, 2), (3_900_000_000, 4), (31_900_000_000, 32), (63_800_000_000, 64),
    (127_800_000_000, 128), (255_000_000_000, 256), (500_000_000, 1),
])
def test_nominal_gb(total, nominal):
    assert nominal_gb(total) == nominal


def test_rules_match_the_guide(real_snapshot):
    assert sdcard.rules_drift(real_snapshot) == []


def test_rules_drift_is_reported(real_snapshot):
    data = copy.deepcopy(real_snapshot.data)
    data["pages"]["troubleshooting-finalizing-setup"] = {**data["pages"]["key-information"], "slug": "x"}
    drift = sdcard.rules_drift(GuideSnapshot(data))
    assert any("1.3GB" in d for d in drift)


# --- confirmation and write guards -----------------------------------------------------

def test_confirm_with_typed_label_case_insensitive():
    confirmed = confirm_drive(GOOD, " 3ds ")
    assert isinstance(confirmed, ConfirmedDrive)
    assert confirmed.root == Path("E:\\")


@pytest.mark.parametrize("typed", ["", "E:", "3DS2", "SD"])
def test_wrong_label_is_refused(typed):
    with pytest.raises(ConfirmationError):
        confirm_drive(GOOD, typed)


def test_unlabelled_drive_needs_its_mount_typed():
    drive = replace(GOOD, label="")
    with pytest.raises(ConfirmationError):
        confirm_drive(drive, "")
    assert confirm_drive(drive, "E:\\").drive == drive


@pytest.mark.parametrize("change", [{"filesystem": "NTFS"}, {"removable": False}, {"system": True}])
def test_confirm_refuses_unusable_drives(change):
    drive = replace(GOOD, **change)
    with pytest.raises(ConfirmationError, match="cannot be used"):
        confirm_drive(drive, "3DS")


def test_confirmed_drive_cannot_be_forged():
    with pytest.raises(TypeError):
        ConfirmedDrive(GOOD, object())


def test_revalidate_catches_a_swapped_card():
    confirmed = confirm_drive(GOOD, "3DS")
    confirmed.revalidate(replace(GOOD, free_bytes=1))  # same card, less free space: fine
    with pytest.raises(ConfirmationError, match="changed"):
        confirmed.revalidate(replace(GOOD, serial="9999-0000"))
    with pytest.raises(ConfirmationError, match="changed"):
        confirmed.revalidate(None)


def drive_at(path: Path, **kw) -> ConfirmedDrive:
    drive = replace(GOOD, mount=str(path), device=str(path), **kw)
    return confirm_drive(drive, "3DS")


@pytest.mark.parametrize("rel", [
    "Nintendo 3DS/abc", "nintendo 3ds/x/y", "NINTENDO 3DS", "Nintendo 3DS",
    "luma/../Nintendo 3DS/a",
])
def test_nintendo_3ds_folder_is_protected(tmp_path, rel):
    with pytest.raises(ProtectedPathError):
        assert_writable(drive_at(tmp_path), tmp_path / rel)


@pytest.mark.parametrize("rel", ["../outside.txt", "/etc/passwd" if os.name != "nt" else "C:/Windows/x"])
def test_paths_outside_the_card_are_refused(tmp_path, rel):
    with pytest.raises(ProtectedPathError):
        assert_writable(drive_at(tmp_path / "sd"), (tmp_path / "sd" / rel) if not rel.startswith(("/", "C:")) else Path(rel))


def test_ordinary_paths_are_writable(tmp_path):
    target = assert_writable(drive_at(tmp_path), tmp_path / "luma" / "payloads" / "x.firm")
    assert target == (tmp_path / "luma" / "payloads" / "x.firm").resolve()


# --- reading the card ------------------------------------------------------------------

ID0 = "0123456789abcdef0123456789abcdef"
ID1 = "fedcba9876543210fedcba9876543210"
ID1B = "00000000111111112222222233333333"


def make_tree(root: Path, dirs: list[str], files: list[str] = ()):
    for d in dirs:
        (root / d).mkdir(parents=True, exist_ok=True)
    for f in files:
        (root / f).parent.mkdir(parents=True, exist_ok=True)
        (root / f).write_bytes(b"x")


def tree_state(root: Path):
    return sorted((p.relative_to(root).as_posix(), p.stat().st_mtime_ns) for p in root.rglob("*"))


def test_scan_without_nintendo_3ds_folder(tmp_path):
    layout = scan_3ds_folder(tmp_path)
    assert not layout.present
    assert [c.level for c in layout.issues] == ["fail"]
    assert layout.issues[0].guide == "troubleshooting-mset9"


def test_scan_single_id0_id1(tmp_path):
    make_tree(tmp_path, [f"Nintendo 3DS/{ID0}/{ID1}/dbs", "Nintendo 3DS/Private"])
    layout = scan_3ds_folder(tmp_path)
    assert layout.present and layout.issues == []
    assert [(i.name, i.id1s) for i in layout.id0s] == [(ID0, [ID1])]


def test_scan_reports_multiple_id0_and_id1(tmp_path):
    make_tree(tmp_path, [f"Nintendo 3DS/{ID0}/{ID1}", f"Nintendo 3DS/{ID0}/{ID1B}", f"Nintendo 3DS/{ID1B}/{ID1}"])
    messages = [c.message for c in scan_3ds_folder(tmp_path).issues]
    assert any("2 ID0" in m for m in messages)
    assert any("2 ID1" in m for m in messages)


def test_scan_notes_mset9_leftovers_without_touching_them(tmp_path):
    make_tree(tmp_path, [f"Nintendo 3DS/{ID0}/{ID1}_user-id1", f"Nintendo 3DS/{ID0}/some-hax-id1"],
              [f"Nintendo 3DS/{ID0}/{ID1}_user-id1/dbs/title.db"])
    before = tree_state(tmp_path)
    layout = scan_3ds_folder(tmp_path)
    assert tree_state(tmp_path) == before
    id0 = layout.id0s[0]
    assert id0.id1s == [] and sorted(id0.other) == sorted([f"{ID1}_user-id1", "some-hax-id1"])
    assert id0.user_id1_backups == [f"{ID1}_user-id1"]


def test_scan_ignores_case_of_folder_name(tmp_path):
    make_tree(tmp_path, [f"nintendo 3ds/{ID0}/{ID1}"])
    assert scan_3ds_folder(tmp_path).present


@pytest.mark.parametrize("files, dirs, modded", [
    (["boot.firm"], ["luma"], True),
    (["boot.firm"], [], False),
    ([], ["luma"], False),
    ([], [], False),
])
def test_detect_cfw(tmp_path, files, dirs, modded):
    make_tree(tmp_path, dirs, files)
    assert detect_cfw(tmp_path).already_modded is modded


# --- integrity test (h2testw-style) ----------------------------------------------------

KIB = 1024


def small_test(tmp_path, size=640 * KIB, **kw):
    confirmed = drive_at(tmp_path)
    plan = sdcard.write_integrity_test(confirmed, size, file_size=256 * KIB, chunk_size=64 * KIB, **kw)
    return confirmed, plan


def test_integrity_round_trip(tmp_path):
    confirmed, plan = small_test(tmp_path)
    assert plan.total_bytes == 640 * KIB
    assert [f.size for f in plan.files] == [256 * KIB, 256 * KIB, 128 * KIB]
    result = sdcard.verify_integrity_test(confirmed, plan)
    assert result.ok and result.bad_bytes == 0 and result.ok_bytes == 640 * KIB


def test_integrity_plan_survives_json(tmp_path):
    confirmed, plan = small_test(tmp_path)
    again = sdcard.IntegrityPlan.from_dict(json.loads(json.dumps(plan.to_dict())))
    assert sdcard.verify_integrity_test(confirmed, again).ok


def test_integrity_detects_corruption(tmp_path):
    confirmed, plan = small_test(tmp_path)
    victim = confirmed.root / plan.files[1].path
    data = bytearray(victim.read_bytes())
    data[70 * KIB] ^= 0xFF
    victim.write_bytes(bytes(data))
    result = sdcard.verify_integrity_test(confirmed, plan)
    assert not result.ok
    assert result.bad_bytes == 64 * KIB  # one chunk
    assert result.first_bad_offset == 256 * KIB + 64 * KIB


def test_integrity_detects_wrapped_fake_capacity(tmp_path):
    # A fake card that maps later writes onto earlier storage returns old data.
    confirmed, plan = small_test(tmp_path)
    first = (confirmed.root / plan.files[0].path).read_bytes()
    (confirmed.root / plan.files[1].path).write_bytes(first)
    assert sdcard.verify_integrity_test(confirmed, plan).bad_bytes == 256 * KIB


def test_integrity_detects_missing_and_short_files(tmp_path):
    confirmed, plan = small_test(tmp_path)
    (confirmed.root / plan.files[0].path).unlink()
    p2 = confirmed.root / plan.files[2].path
    p2.write_bytes(p2.read_bytes()[: 64 * KIB])
    result = sdcard.verify_integrity_test(confirmed, plan)
    assert result.missing_files == [plan.files[0].path]
    assert result.bad_bytes == 256 * KIB + 64 * KIB


def test_integrity_can_be_cancelled_and_cleaned(tmp_path):
    calls = []
    confirmed, plan = small_test(tmp_path, cancel=lambda: len(calls) >= 3, progress=lambda done, total: calls.append(done))
    assert plan.cancelled and plan.total_bytes == 3 * 64 * KIB
    stray = confirmed.root / sdcard.INTEGRITY_DIR / "user-note.txt"
    stray.write_text("not ours")
    sdcard.cleanup_integrity_test(confirmed, plan)
    assert not any((confirmed.root / sdcard.INTEGRITY_DIR).glob("*.h2w"))
    assert stray.exists()  # only files the plan lists are removed


def test_cancel_on_a_file_boundary_leaves_nothing_behind(tmp_path):
    calls = []
    confirmed, plan = small_test(tmp_path, cancel=lambda: len(calls) >= 4, progress=lambda d, t: calls.append(d))
    assert plan.total_bytes == 256 * KIB
    sdcard.cleanup_integrity_test(confirmed, plan)
    assert not (confirmed.root / sdcard.INTEGRITY_DIR).exists()


def test_integrity_is_capped_by_real_free_space(tmp_path, monkeypatch):
    import shutil
    from collections import namedtuple

    usage = namedtuple("usage", "total used free")
    monkeypatch.setattr(shutil, "disk_usage", lambda p: usage(10**9, 0, sdcard.INTEGRITY_RESERVE + 128 * KIB))
    confirmed, plan = small_test(tmp_path, size=None)
    assert plan.total_bytes == 128 * KIB


def test_integrity_refuses_protected_location(tmp_path, monkeypatch):
    monkeypatch.setattr(sdcard, "INTEGRITY_DIR", "Nintendo 3DS/h2test")
    with pytest.raises(ProtectedPathError):
        small_test(tmp_path)


# --- drive enumeration backends (parsers only; no real devices) ------------------------

LSBLK = {
    "blockdevices": [
        {"name": "nvme0n1", "path": "/dev/nvme0n1", "rm": False, "hotplug": False, "type": "disk",
         "pttype": "gpt", "size": 512 * GB, "children": [
            {"name": "nvme0n1p2", "path": "/dev/nvme0n1p2", "rm": False, "hotplug": False, "type": "part",
             "fstype": "ext4", "fsver": "1.0", "label": None, "mountpoint": "/", "size": 500 * GB,
             "pttype": "gpt", "uuid": "aaaa"}]},
        {"name": "sdb", "path": "/dev/sdb", "rm": True, "hotplug": True, "type": "disk",
         "pttype": "dos", "size": 32 * GB, "children": [
            {"name": "sdb1", "path": "/dev/sdb1", "rm": True, "hotplug": True, "type": "part",
             "fstype": "vfat", "fsver": "FAT32", "label": "3DS", "mountpoint": "/media/u/3DS",
             "size": 31_900_000_000, "pttype": "dos", "uuid": "1234-ABCD"}]},
        {"name": "sdc", "path": "/dev/sdc", "rm": True, "hotplug": True, "type": "disk",
         "pttype": "dos", "size": 8 * GB, "children": [
            {"name": "sdc1", "path": "/dev/sdc1", "rm": True, "hotplug": True, "type": "part",
             "fstype": "vfat", "fsver": "FAT32", "label": "NOMOUNT", "mountpoint": None,
             "size": 8 * GB, "pttype": "dos", "uuid": "5555-6666"}]},
    ]
}


def test_parse_lsblk(monkeypatch):
    from collections import namedtuple

    vfs = namedtuple("vfs", "f_bsize f_frsize f_blocks f_bavail")
    monkeypatch.setattr(sdcard, "_statvfs", lambda m: vfs(32768, 32768, 1000, 600), raising=False)
    drives = sdcard.parse_lsblk(json.dumps(LSBLK))
    assert [(d.mount, d.label, d.filesystem, d.removable, d.system, d.partition_style) for d in drives] == [
        ("/", "", "EXT4", False, True, "GPT"),
        ("/media/u/3DS", "3DS", "FAT32", True, False, "MBR"),
    ]
    sd = drives[1]
    assert (sd.cluster_size, sd.total_bytes, sd.free_bytes, sd.serial) == (32768, 32768 * 1000, 32768 * 600, "1234-ABCD")


def test_parse_diskutil():
    import plistlib

    volume = plistlib.dumps({
        "VolumeName": "3DS", "FilesystemName": "MS-DOS FAT32", "FilesystemType": "msdos",
        "Internal": False, "RemovableMediaOrExternalDevice": True, "TotalSize": 31_900_000_000,
        "FreeSpace": 20 * GB, "VolumeAllocationBlockSize": 32768, "DeviceIdentifier": "disk4s1",
        "ParentWholeDisk": "disk4", "MountPoint": "/Volumes/3DS", "VolumeUUID": "UUID-1",
    })
    whole = plistlib.dumps({"Content": "FDisk_partition_scheme"})
    d = sdcard.parse_diskutil(volume, whole)
    assert (d.mount, d.label, d.filesystem, d.cluster_size, d.removable, d.system, d.partition_style) == (
        "/Volumes/3DS", "3DS", "FAT32", 32768, True, False, "MBR")


class FakeWin32:
    REMOVABLE, FIXED = 2, 3

    def __init__(self, drives):
        self.drives = drives

    def letters(self):
        return list(self.drives)

    def drive_type(self, root):
        return self.drives[root[0]]["type"]

    def volume(self, root):
        d = self.drives[root[0]]
        return d["label"], d["fs"], d["serial"]

    def free_total(self, root):
        return self.drives[root[0]]["free"], self.drives[root[0]]["total"]

    def cluster(self, root):
        return self.drives[root[0]]["cluster"]

    def partition_style(self, letter):
        return self.drives[letter]["pt"]


def test_windows_backend(monkeypatch):
    monkeypatch.setenv("SystemDrive", "C:")
    win = FakeWin32({
        "C": {"type": 3, "label": "OS", "fs": "NTFS", "serial": 1, "free": 10 * GB, "total": 500 * GB, "cluster": 4096, "pt": "GPT"},
        "E": {"type": 2, "label": "3DS", "fs": "FAT32", "serial": 0x1234ABCD, "free": 20 * GB, "total": 32 * GB, "cluster": 32768, "pt": "MBR"},
        "F": {"type": 5, "label": "DVD", "fs": "UDF", "serial": 2, "free": 0, "total": 1, "cluster": 2048, "pt": None},
    })
    drives = sdcard.list_windows_drives(win)
    assert [(d.mount, d.removable, d.system) for d in drives] == [("C:\\", False, True), ("E:\\", True, False)]
    assert drives[1].serial == "1234-ABCD"
    assert sdcard.removable_only(drives) == [drives[1]]
