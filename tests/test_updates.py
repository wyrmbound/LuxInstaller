from __future__ import annotations

import copy
import types
import zipfile
from pathlib import Path

import pytest

import updates
from guide_parser import GuideSnapshot


# --- Checking for CFW routing (rules read from the guide) -------------------------------

def test_rules_come_from_the_guide(real_snapshot):
    rules = updates.cfw_rules(real_snapshot)
    assert [(r.kind, r.version, r.slug) for r in rules] == [
        ("home", (), "get-started"),
        ("le", (7, 0, 5), "a9lh-to-b9s"),
        ("eq", (7, 1), "updating-b9s"),
        ("ge", (8, 0), "restoring-updating-cfw"),
    ]


@pytest.mark.parametrize("text, slug", [
    ("Luma3DS v13.4", "restoring-updating-cfw"),
    ("8.0", "restoring-updating-cfw"),
    ("v10.2.1", "restoring-updating-cfw"),
    ("7.1", "updating-b9s"),
    ("Luma3DS 7.1.2", "updating-b9s"),
    ("7.0.5", "a9lh-to-b9s"),
    ("6.6", "a9lh-to-b9s"),
    ("it boots to the HOME Menu", "get-started"),
])
def test_route_existing_cfw(real_snapshot, text, slug):
    got, message = updates.route_existing_cfw(real_snapshot, text)
    assert got == slug and message


@pytest.mark.parametrize("text", ["", "no idea", "7.5", "7.10"])
def test_unmatched_versions_go_to_checking_for_cfw(real_snapshot, text):
    slug, message = updates.route_existing_cfw(real_snapshot, text)
    assert slug == "checking-for-cfw" and "https://3ds.hacks.guide/checking-for-cfw" in message


def test_guide_change_breaks_rules_loudly(real_snapshot):
    data = copy.deepcopy(real_snapshot.data)
    for b in data["pages"]["checking-for-cfw"]["blocks"]:
        if b["heading"] == "What to do next":
            b["heading"] = "Renamed"
    slug, message = updates.route_existing_cfw(GuideSnapshot(data), "13.4")
    assert slug == "checking-for-cfw" and "couldn't be read" in message


def test_wrong_menu_warning(real_snapshot):
    assert any("held (Start) by accident" in w for w in updates.wrong_menu_warnings(real_snapshot))


# --- installed files ---------------------------------------------------------------------

def test_detect_installed(tmp_path):
    assert updates.detect_installed(tmp_path) == updates.Installed(False, False, False, (), False)
    (tmp_path / "luma" / "payloads").mkdir(parents=True)
    (tmp_path / "luma" / "payloads" / "GodMode9.firm").write_bytes(b"g")
    (tmp_path / "luma" / "config.ini").write_text("x")
    (tmp_path / "boot.firm").write_bytes(b"b")
    (tmp_path / "gm9").mkdir()
    before = sorted((p, p.stat().st_mtime_ns) for p in tmp_path.rglob("*"))
    found = updates.detect_installed(tmp_path)
    assert found.boot_firm and found.luma_config and found.gm9_dir and found.godmode9_firm
    assert found.payloads == ("GodMode9.firm",)
    assert sorted((p, p.stat().st_mtime_ns) for p in tmp_path.rglob("*")) == before


# --- update plans ------------------------------------------------------------------------

def _zip(path: Path, members: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w") as z:
        for n, b in members.items():
            z.writestr(n, b)
    return path


def dl(name, path):
    return types.SimpleNamespace(name=name, path=path, sha256="", verified="published", from_cache=False)


def test_luma_update_needs_the_luma_zip(real_snapshot):
    info = updates.luma_update_info(real_snapshot)
    assert info.url == "https://3ds.hacks.guide/restoring-updating-cfw"
    assert [(s.name, s.kind, s.asset_suffix) for s in info.specs] == [("Luma3DS", "github", ".zip")]


def test_luma_update_step_is_unparsed_not_guessed(real_snapshot, tmp_path):
    # The guide's wording adds "(`boot.firm`, `boot.3dsx`, and `config`)" which the stager
    # doesn't recognise, so the plan must refuse rather than guess.
    luma = _zip(tmp_path / "Luma3DSv13.4.zip", {"boot.firm": b"b", "boot.3dsx": b"x", "config/x": b"c"})
    result = updates.plan_luma_update(real_snapshot, [dl("Luma3DS", luma)], tmp_path / "sd")
    assert result.plan is not None
    if result.unparsed:
        assert not result.ready and "restoring-updating-cfw" in result.message
        assert any("Copy everything from the Luma3DS" in u for u in result.unparsed)
    else:  # if the stager learns the wording, it must map exactly the zip's files
        assert sorted(op.dest for op in result.plan.ops) == ["boot.3dsx", "boot.firm", "config/x"]


def test_gm9_update_is_offered_from_the_guide(real_snapshot):
    info = updates.gm9_update_info(real_snapshot)
    assert info.url == "https://3ds.hacks.guide/godmode9-usage#updating-godmode9"
    assert [(s.name, s.owner, s.repo, s.asset_suffix) for s in info.specs] == [("GodMode9", "d0k3", "GodMode9", ".zip")]
    assert info.message == ""


def test_gm9_update_plan(real_snapshot, tmp_path):
    gm9 = _zip(tmp_path / "GodMode9-v2.zip", {"GodMode9.firm": b"g", "gm9/scripts/a.gm9": b"s"})
    result = updates.plan_gm9_update(real_snapshot, [dl("GodMode9", gm9)], tmp_path / "sd")
    dests = [op.dest for op in result.plan.ops]
    assert "luma/payloads/GodMode9.firm" in dests
    # "Copy the `gm9` folder from the GodMode9 `.zip`" is not a stager pattern: reported, not guessed
    if result.unparsed:
        assert any("gm9" in u for u in result.unparsed) and not result.ready


def test_missing_page_returns_link(real_snapshot):
    data = copy.deepcopy(real_snapshot.data)
    del data["pages"]["godmode9-usage"]
    info = updates.gm9_update_info(GuideSnapshot(data))
    assert info.specs == [] and "https://3ds.hacks.guide/godmode9-usage#updating-godmode9" in info.message


def test_gm9_without_github_link_is_not_offered(real_snapshot):
    data = copy.deepcopy(real_snapshot.data)
    for b in data["pages"]["godmode9-usage"]["blocks"]:
        if b["heading"] == "What You Need":
            b["bullets"] = [{"text": "GodMode9 from [somewhere](https://example.com/gm9)", "details": []}]
    info = updates.gm9_update_info(GuideSnapshot(data))
    assert "Universal-Updater" in info.message
