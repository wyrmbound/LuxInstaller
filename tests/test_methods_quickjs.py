from __future__ import annotations

import copy

import pytest

import methods
from guide_parser import GuideSnapshot
from methods import RoutingError
from test_methods import TABLE

# --- the guide's real selecting.js under QuickJS ----------------------------------------

quickjs = pytest.importorskip("quickjs", reason="QuickJS has no wheel for this platform (Windows ARM64)")


@pytest.mark.parametrize("model, version, outcome, page", TABLE)
def test_method_table(real_snapshot, model, version, outcome, page):
    r = methods.select_method(real_snapshot, model, version)
    assert (r.outcome, r.page) == (outcome, page)


def test_unmodified_script_hits_missing_element_as_error(real_snapshot):
    # selecting.js writes to result_methodUnavailable, which the real form does not define.
    # Force that path: with every can_* returning false the page would throw, so must we.
    snap = copy.deepcopy(real_snapshot.data)
    src = snap["routing"]["public/assets/js/selecting.js"]["source"]
    snap["routing"]["public/assets/js/selecting.js"]["source"] = src.replace(
        "].some(func => func(major, minor, nver, region, model));", "].some(func => false);"
    )
    assert snap["routing"]["public/assets/js/selecting.js"]["source"] != src
    with pytest.raises(RoutingError, match="routing script failed"):
        methods.select_method(GuideSnapshot(snap), "new3ds", "Ver. 11.17.0-50U")


def test_runaway_script_is_stopped(real_snapshot):
    snap = copy.deepcopy(real_snapshot.data)
    snap["routing"]["public/assets/js/selecting.js"]["source"] = "while (true) {}"
    with pytest.raises(RoutingError):
        methods.select_method(GuideSnapshot(snap), "new3ds", "Ver. 11.17.0-50U")


def test_sandbox_has_no_host_access(real_snapshot):
    snap = copy.deepcopy(real_snapshot.data)
    probe = (
        "window.redirect = function () {"
        " var names = ['require', 'std', 'os', 'fetch', 'XMLHttpRequest', 'process', 'Deno'];"
        " window.location.href = names.filter(function (n) { return typeof globalThis[n] !== 'undefined'; }).join(',') || 'none'; };"
    )
    snap["routing"]["public/assets/js/selecting.js"]["source"] = probe
    # The probe "redirects" to the names it found; "none" is not a page, so it is refused by name.
    with pytest.raises(RoutingError, match="chose none,"):
        methods.select_method(GuideSnapshot(snap), "new3ds", "Ver. 11.17.0-50U")
