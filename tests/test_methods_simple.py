"""The near-universal default: MSET9 for 11.4+, soundhax for 11.3-, using the guide's own checks."""
from __future__ import annotations

import copy

import pytest

import methods
from guide_parser import GuideSnapshot

pytest.importorskip("quickjs", reason="QuickJS has no wheel for this platform (Windows ARM64)")

MSET9 = "installing-boot9strap-(mset9)"
SOUNDHAX = "installing-boot9strap-(soundhax)"


@pytest.mark.parametrize("model, version, guide_page", [
    ("old2ds", "11.17.0-50U", MSET9),
    ("new3ds", "Ver. 11.17.0-50U", "installing-boot9strap-(super-skaterhax)"),
    ("old3ds", "Ver. 11.13.0-45U", "installing-boot9strap-(ssloth-browser)"),
    ("old3ds", "Ver. 11.14.0-46J", "installing-boot9strap-(safecerthax)"),
    ("new2dsxl", "Ver. 11.4.0-37E", "installing-boot9strap-(ssloth-browser)"),
])
def test_11_4_and_up_always_uses_mset9(real_snapshot, model, version, guide_page):
    assert methods.select_method(real_snapshot, model, version).page == guide_page  # what the guide would do
    r = methods.select_simple(real_snapshot, model, version)
    assert (r.outcome, r.page, r.simple) == ("method", MSET9, True)
    assert "11.4 to 11.17" in r.note
    assert r.url == "https://3ds.hacks.guide/installing-boot9strap-(mset9)"
    assert methods.resolve_os_variant(real_snapshot, r.page, "Windows") == "installing-boot9strap-(mset9-cli)"


def test_11_3_and_below_uses_soundhax(real_snapshot):
    r = methods.select_simple(real_snapshot, "old3ds", "Ver. 11.3.0-36U")
    assert (r.outcome, r.page, r.simple) == ("method", SOUNDHAX, True)
    assert r.explanation  # the soundhax page's compatibility notes


@pytest.mark.parametrize("model, version, outcome", [
    ("old3ds", "Sys. 11.17.0-50U", "cfwPrefixDetected"),
    ("new3ds", "Ver. 11.16.0-49C", "invalidVersion"),
    ("old3ds", "Ver. 11.17.0-50K", "invalidVersion"),
])
def test_guide_messages_are_unchanged(real_snapshot, model, version, outcome):
    r = methods.select_simple(real_snapshot, model, version)
    assert (r.outcome, r.page, r.simple) == (outcome, None, False)
    assert r == methods.select_method(real_snapshot, model, version)


def _tampered(real_snapshot, old: str, new: str) -> GuideSnapshot:
    data = copy.deepcopy(real_snapshot.data)
    src = data["routing"][methods.SELECTING]["source"]
    assert old in src
    data["routing"][methods.SELECTING]["source"] = src.replace(old, new)  # every occurrence
    return GuideSnapshot(data)


def test_missing_anchor_falls_back_to_the_guide(real_snapshot):
    snap = _tampered(real_snapshot, "window.redirect = redirect;", "window['redirect'] = redirect;")
    r = methods.select_simple(snap, "new3ds", "Ver. 11.17.0-50U")
    assert (r.page, r.simple) == ("installing-boot9strap-(super-skaterhax)", False)
    assert "changed shape" in r.note


def test_renamed_check_falls_back_to_the_guide(real_snapshot):
    # Renamed everywhere, so the guide's own script still works but the expected name is gone.
    snap = _tampered(real_snapshot, "can_mset9", "can_mset9_v2")
    r = methods.select_simple(snap, "old3ds", "Ver. 11.13.0-45U")
    assert (r.page, r.simple) == ("installing-boot9strap-(ssloth-browser)", False)


def test_no_simple_method_uses_the_guide_routing(real_snapshot):
    # If the guide's MSET9 and soundhax checks both reject a version, its own order decides.
    snap = _tampered(real_snapshot, "if(major == 11 && minor >= 4) {", "if(false) {")
    r = methods.select_simple(snap, "old3ds", "Ver. 11.14.0-46J")
    assert (r.page, r.simple) == ("installing-boot9strap-(safecerthax)", False)
    assert r.note == "Using the guide's routing for this version."
