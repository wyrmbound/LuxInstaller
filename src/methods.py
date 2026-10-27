"""Pick the guide page for a console by running the guide's own selecting.js.

No exploit rules live here. The guide's routing scripts run inside QuickJS (no filesystem,
network or host access) against a stub of the version form that has only the element ids
the real form has. Anything unexpected raises RoutingError carrying the Get Started link.
"""
from __future__ import annotations

import json
import platform
import re
from dataclasses import dataclass, replace

from guide_parser import ROUTER_PAGE, ROUTING_FILES, GuideParseError, GuideSnapshot, link, page_url

# Intake choices -> the guide's two model families (the form's Old/New radio buttons).
MODELS = {
    "old3ds": ("Old 3DS", "old"),
    "old3dsxl": ("Old 3DS XL", "old"),
    "old2ds": ("2DS", "old"),
    "new3ds": ("New 3DS", "new"),
    "new3dsxl": ("New 3DS XL", "new"),
    "new2dsxl": ("New 2DS XL", "new"),
}

VERSION_RE = re.compile(
    r"^\s*(?:(?P<prefix>\S+?)\s*)??"
    r"(?P<major>\d+)\.(?P<minor>\d+)\.(?P<micro>\d+)-(?P<nver>\d+)(?P<region>[A-Za-z])\s*$"
)

JS_MEMORY_LIMIT = 32 * 1024 * 1024
JS_TIME_LIMIT_S = 2

# Builds the minimum browser surface selecting.js touches. Elements exist only for ids the
# real form declares, so a script path that relies on a missing element fails the same way
# it would on the website.
_HARNESS = r"""
var window = globalThis;
window.location = { href: "" };
var __els = {};
__FORM_IDS__.forEach(function (id) {
    __els[id] = { id: id, value: "", checked: false, style: { display: "none" } };
});
var document = {
    getElementById: function (id) {
        return Object.prototype.hasOwnProperty.call(__els, id) ? __els[id] : null;
    }
};
var __store = {};
var sessionStorage = {
    setItem: function (k, v) { __store[k] = String(v); },
    getItem: function (k) { return Object.prototype.hasOwnProperty.call(__store, k) ? __store[k] : null; }
};
"""

_CALL = r"""
(function (input) {
    Object.keys(input.values).forEach(function (id) { document.getElementById(id).value = input.values[id]; });
    if (input.model) document.getElementById(input.model).checked = true;
    var returned = window.redirect();
    var shown = Object.keys(__els).filter(function (id) { return __els[id].style.display === "block"; });
    return JSON.stringify({ href: window.location.href, shown: shown, returned: returned === undefined ? null : returned });
})(__INPUT__)
"""


class VersionFormatError(ValueError):
    pass


class RoutingError(Exception):
    """The guide's routing could not be run or gave an unexpected answer."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason
        self.url = page_url(ROUTER_PAGE)


@dataclass(frozen=True)
class ConsoleVersion:
    prefix: str
    major: str
    minor: str
    micro: str
    nver: str
    region: str

    def __str__(self) -> str:
        prefix = "" if self.prefix == "(Other)" else f"{self.prefix} "
        return f"{prefix}{self.major}.{self.minor}.{self.micro}-{self.nver}{self.region}"


@dataclass(frozen=True)
class RouteResult:
    outcome: str  # "method", or the form message shown (e.g. "invalidVersion", "cfwPrefixDetected")
    page: str | None
    message: str
    explanation: str
    commit: str
    simple: bool = False  # chosen by select_simple's near-universal default, not the guide's order
    note: str = ""

    @property
    def url(self) -> str:
        return page_url(self.page or ROUTER_PAGE)


def model_family(model: str) -> str:
    try:
        return MODELS[model][1]
    except KeyError:
        raise VersionFormatError(f"Unknown model {model!r}; choose one of: {', '.join(MODELS)}") from None


def parse_version(text: str, snapshot: GuideSnapshot) -> ConsoleVersion:
    """Read a version like 'Ver. 11.17.0-50U', accepting only values the guide's form offers."""
    form = _form(snapshot)
    m = VERSION_RE.match(text or "")
    if not m:
        raise VersionFormatError(
            f"{text!r} doesn't look like a system version. It is shown at the bottom right of "
            "System Settings, e.g. 'Ver. 11.17.0-50U'."
        )
    selects = form["selects"]
    typed = (m["prefix"] or "Ver.").lower()
    prefix = next((p for p in selects["prefix"] if p.lower() == typed), "(Other)")
    version = ConsoleVersion(prefix, str(int(m["major"])), str(int(m["minor"])), str(int(m["micro"])),
                             str(int(m["nver"])), m["region"].upper())
    for field, sid in (("major", "major"), ("minor", "minor"), ("micro", form["micro_select"]),
                       ("nver", "nver"), ("region", "region")):
        if getattr(version, field) not in selects[sid]:
            raise VersionFormatError(f"{field} {getattr(version, field)!r} is not a value the guide accepts")
    return version


def _form(snapshot: GuideSnapshot) -> dict:
    try:
        snapshot.page(ROUTER_PAGE)
    except GuideParseError as e:
        raise RoutingError(f"Get Started could not be parsed: {e.reason}") from e
    if not snapshot.version_form:
        raise RoutingError("the guide's version form could not be parsed")
    return snapshot.version_form


def run_selecting_js(snapshot: GuideSnapshot, family: str, version: ConsoleVersion) -> dict:
    """Run the guide's routing scripts in QuickJS and return what the page would have done."""
    try:
        import quickjs
    except ImportError as e:  # pragma: no cover - depends on the platform's wheels
        raise RoutingError("the QuickJS engine is not installed") from e

    form = _form(snapshot)
    missing = [f for f in ROUTING_FILES if f not in snapshot.routing]
    if missing:
        raise RoutingError(f"routing script missing from the guide: {', '.join(missing)}")
    values = {"prefix": version.prefix, "major": version.major, "minor": version.minor,
              form["micro_select"]: version.micro, "nver": version.nver, "region": version.region}
    payload = {"values": values, "model": form["models"][family]}

    ctx = quickjs.Context()
    ctx.set_memory_limit(JS_MEMORY_LIMIT)
    ctx.set_time_limit(JS_TIME_LIMIT_S)
    try:
        ctx.eval(_HARNESS.replace("__FORM_IDS__", json.dumps(form["element_ids"])))
        for name in ROUTING_FILES:
            ctx.eval(snapshot.routing[name]["source"])
        out = ctx.eval(_CALL.replace("__INPUT__", json.dumps(payload)))
    except Exception as e:  # quickjs raises JSException; anything else is equally fatal
        raise RoutingError(f"the guide's routing script failed: {e}") from e
    try:
        return json.loads(out)
    except (TypeError, ValueError) as e:
        raise RoutingError("the guide's routing script returned nothing usable") from e


def interpret(js_out: dict, snapshot: GuideSnapshot) -> RouteResult:
    """Turn the script's effect (a redirect, or a message it revealed) into a RouteResult."""
    href = js_out.get("href") or ""
    shown = js_out.get("shown") or []
    if href:
        slug = link("", href)["slug"]
        if not slug:
            raise RoutingError(f"the guide redirected to {href!r}, which is not a guide page")
        try:
            page = snapshot.page(slug)
            if not page["sections"]:  # an OS chooser such as MSET9's has links, not steps
                if not page["links"]:
                    raise GuideParseError(slug, "no steps and no links to follow")
            else:
                snapshot.stage_page(slug)
        except GuideParseError as e:
            raise RoutingError(f"the guide chose {slug}, but that page can't be used: {e.reason}") from e
        compat = (page.get("compatibility") or {}).get("text", "")
        return RouteResult("method", slug, page["title"], compat, snapshot.commit)

    if len(shown) == 1 and shown[0].startswith("result_"):
        key = shown[0].removeprefix("result_")
        frontmatter = snapshot.page(ROUTER_PAGE)["frontmatter"]
        message = frontmatter.get(key)
        if message is None:
            raise RoutingError(f"the guide showed {shown[0]} but has no text for it")
        return RouteResult(key, None, message, "", snapshot.commit)

    raise RoutingError(f"the guide's routing gave no answer (shown={shown!r})")


def select_method(snapshot: GuideSnapshot, model: str, version_text: str) -> RouteResult:
    family = model_family(model)
    version = parse_version(version_text, snapshot)
    return interpret(run_selecting_js(snapshot, family, version), snapshot)


# --- near-universal default ---------------------------------------------------------------
# The guide's own compatibility checks, called directly: MSET9 covers 11.4+ on every model and
# region, soundhax covers 11.3 and below. Only when neither applies does the guide's full
# routing order decide.

SELECTING = "public/assets/js/selecting.js"
EXPOSE_ANCHOR = "window.redirect = redirect;"
EXPOSE = "window.redirect = redirect; window.__can = {soundhax: can_soundhax, mset9: can_mset9};"
SIMPLE_NOTES = {
    "mset9": "MSET9 works on every model and region from 11.4 to 11.17, and it doesn't go online.",
    "soundhax": "Soundhax works on every model and region up to 11.3.",
}

_PROBE = r"""
(function (input) {
    var model = input.family === "new" ? DEVICE_N3DS : DEVICE_O3DS;
    var ok = window.__can[input.name](input.major, input.minor, input.nver, input.region, model);
    return JSON.stringify({ ok: ok === true, href: window.location.href });
})(__INPUT__)
"""


def _exposed_source(snapshot: GuideSnapshot) -> str | None:
    src = snapshot.routing.get(SELECTING, {}).get("source", "")
    if src.count(EXPOSE_ANCHOR) != 1 or "function can_soundhax(" not in src or "function can_mset9(" not in src:
        return None
    return src.replace(EXPOSE_ANCHOR, EXPOSE)


def _probe(snapshot: GuideSnapshot, source: str, name: str, family: str, version: ConsoleVersion) -> dict:
    import quickjs

    form = _form(snapshot)
    payload = {"name": name, "family": family, "major": version.major, "minor": version.minor,
               "nver": version.nver, "region": version.region}
    ctx = quickjs.Context()
    ctx.set_memory_limit(JS_MEMORY_LIMIT)
    ctx.set_time_limit(JS_TIME_LIMIT_S)
    try:
        ctx.eval(_HARNESS.replace("__FORM_IDS__", json.dumps(form["element_ids"])))
        ctx.eval(snapshot.routing[ROUTING_FILES[0]]["source"])
        ctx.eval(source)
        return json.loads(ctx.eval(_PROBE.replace("__INPUT__", json.dumps(payload))))
    except Exception as e:
        raise RoutingError(f"the guide's {name} check failed: {e}") from e


def select_simple(snapshot: GuideSnapshot, model: str, version_text: str) -> RouteResult:
    """MSET9 (11.4+) or soundhax (11.3-), using the guide's own checks; else the guide's routing."""
    guide = select_method(snapshot, model, version_text)
    if guide.outcome != "method":
        return guide  # invalid version, CFW already installed, ...
    source = _exposed_source(snapshot)
    if source is None:
        return replace(guide, note="The guide's routing script changed shape, so its own choice is used.")
    family = model_family(model)
    version = parse_version(version_text, snapshot)
    for name in ("mset9", "soundhax"):
        probe = _probe(snapshot, source, name, family, version)
        if probe.get("ok") and probe.get("href"):
            chosen = interpret({"href": probe["href"], "shown": []}, snapshot)
            return replace(chosen, simple=True, note=SIMPLE_NOTES[name])
    return replace(guide, note="Using the guide's routing for this version.")


def host_os_label() -> str:
    return {"Windows": "Windows", "Darwin": "macOS", "Linux": "Linux"}.get(platform.system(), platform.system())


def resolve_os_variant(snapshot: GuideSnapshot, slug: str, os_label: str | None = None) -> str:
    """Follow an OS chooser page (e.g. MSET9) to the variant for this computer; else return slug."""
    page = snapshot.page(slug)
    if page["sections"]:
        return slug
    os_label = os_label or host_os_label()
    for l in page["links"]:
        if l["text"] == os_label and l["slug"]:
            snapshot.stage_page(l["slug"])
            return l["slug"]
    raise RoutingError(f"{slug} has no instructions for {os_label}")
