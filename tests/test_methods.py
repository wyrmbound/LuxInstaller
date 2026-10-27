from __future__ import annotations

import copy

import pytest

import methods
from guide_parser import GuideSnapshot
from methods import RoutingError, VersionFormatError, interpret, parse_version, resolve_os_variant

SOUNDHAX = "installing-boot9strap-(soundhax)"
SSLOTH = "installing-boot9strap-(ssloth-browser)"
SAFECERT = "installing-boot9strap-(safecerthax)"
SKATER = "installing-boot9strap-(super-skaterhax)"
MSET9 = "installing-boot9strap-(mset9)"

# The method-selection table approved in docs/guide-summary.md.
TABLE = [
    ("old3ds", "Ver. 11.3.0-36U", "method", SOUNDHAX),
    ("new3ds", "Ver. 11.13.0-45E", "method", SSLOTH),
    ("old3ds", "Ver. 11.13.0-45U", "method", SSLOTH),
    ("old3ds", "Ver. 11.14.0-46J", "method", SAFECERT),
    ("old3ds", "Ver. 11.4.0-33K", "method", SAFECERT),
    ("new3ds", "Ver. 11.10.0-42U", "method", MSET9),
    ("new3ds", "Ver. 11.17.0-50U", "method", SKATER),
    ("new3dsxl", "Ver. 11.16.0-49K", "method", SKATER),
    ("new2dsxl", "Ver. 11.17.0-50J", "method", MSET9),
    ("old2ds", "Ver. 11.17.0-50E", "method", MSET9),
    ("old3dsxl", "Ver. 11.16.0-49C", "method", MSET9),
    ("new3ds", "Ver. 11.16.0-49T", "invalidVersion", None),
    ("old3ds", "Ver. 11.17.0-50K", "invalidVersion", None),
    ("old3ds", "Sys. 11.17.0-50U", "cfwPrefixDetected", None),
]


# --- intake (no JS engine needed) -------------------------------------------------------

@pytest.mark.parametrize("text, expected", [
    ("Ver. 11.17.0-50U", ("Ver.", "11", "17", "0", "50", "U")),
    ("11.17.0-50U", ("Ver.", "11", "17", "0", "50", "U")),
    ("  ver. 9.2.0-20e ", ("Ver.", "9", "2", "0", "20", "E")),
    ("Luma 11.17.0-50U", ("(Other)", "11", "17", "0", "50", "U")),
    ("Sys.11.4.0-37E", ("Sys.", "11", "4", "0", "37", "E")),
    ("Emu. 11.17.0-50J", ("Emu.", "11", "17", "0", "50", "J")),
])
def test_parse_version(real_snapshot, text, expected):
    v = parse_version(text, real_snapshot)
    assert (v.prefix, v.major, v.minor, v.micro, v.nver, v.region) == expected


@pytest.mark.parametrize("text", [
    "", "11.17", "11.17.0", "11.17.0-50", "eleven", "11.17.0-50X",  # shape / unknown region
    "11.17.0-51U",  # NVER the form does not offer
    "11.17.1-50U",  # micro the form does not offer
    "12.0.0-50U",   # major the form does not offer
])
def test_parse_version_rejects(real_snapshot, text):
    with pytest.raises(VersionFormatError):
        parse_version(text, real_snapshot)


def test_version_str():
    assert str(methods.ConsoleVersion("Ver.", "11", "17", "0", "50", "U")) == "Ver. 11.17.0-50U"


@pytest.mark.parametrize("model, family", [
    ("old3ds", "old"), ("old3dsxl", "old"), ("old2ds", "old"),
    ("new3ds", "new"), ("new3dsxl", "new"), ("new2dsxl", "new"),
])
def test_model_family(model, family):
    assert methods.model_family(model) == family


def test_unknown_model():
    with pytest.raises(VersionFormatError):
        methods.model_family("switch")


# --- interpreting the script's effect (no JS engine needed) -----------------------------

def test_interpret_redirect(real_snapshot):
    r = interpret({"href": SKATER, "shown": []}, real_snapshot)
    assert (r.outcome, r.page) == ("method", SKATER)
    assert r.url == "https://3ds.hacks.guide/installing-boot9strap-(super-skaterhax)"
    assert "MSET9" in r.explanation  # the guide's own fallback note travels with the result
    assert r.commit == real_snapshot.commit


def test_interpret_message_uses_guide_text(real_snapshot):
    r = interpret({"href": "", "shown": ["result_invalidVersion"]}, real_snapshot)
    assert r.outcome == "invalidVersion"
    assert r.message == "This doesn't seem to be a valid system version."
    assert r.url == "https://3ds.hacks.guide/get-started"


@pytest.mark.parametrize("js_out", [
    {"href": "installing-boot9strap-(not-a-page)", "shown": []},
    {"href": "https://example.com/elsewhere", "shown": []},
    {"href": "", "shown": []},
    {"href": "", "shown": ["result_noneSelected", "result_invalidVersion"]},
    {"href": "", "shown": ["result_somethingNew"]},
])
def test_interpret_refuses_to_guess(real_snapshot, js_out):
    with pytest.raises(RoutingError) as err:
        interpret(js_out, real_snapshot)
    assert err.value.url == "https://3ds.hacks.guide/get-started"


def test_interpret_refuses_retired_page(real_snapshot):
    snap = copy.deepcopy(real_snapshot.data)
    snap["pages"][SOUNDHAX]["redirect"] = "410"
    with pytest.raises(RoutingError, match="retired"):
        interpret({"href": SOUNDHAX, "shown": []}, GuideSnapshot(snap))


@pytest.mark.parametrize("os_label", ["Windows", "macOS", "Linux"])
def test_mset9_resolves_to_cli_on_desktop(real_snapshot, os_label):
    assert resolve_os_variant(real_snapshot, MSET9, os_label) == "installing-boot9strap-(mset9-cli)"


def test_non_chooser_page_resolves_to_itself(real_snapshot):
    assert resolve_os_variant(real_snapshot, SKATER, "Windows") == SKATER


def test_chooser_without_this_os(real_snapshot):
    with pytest.raises(RoutingError):
        resolve_os_variant(real_snapshot, MSET9, "Plan 9")
