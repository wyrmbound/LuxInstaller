"""Consoles that already run custom firmware: the guide's "Checking for CFW" routing and updates.

Everything comes from the guide. Routing rules are read from the tip notes on
checking-for-cfw ("If you see a Luma3DS version of X or lower/greater, continue to ...").
The Luma3DS update is restoring-updating-cfw's "Instructions"; the GodMode9 update is
godmode9-usage's "Updating GodMode9". Those pages have no numbered "Section" headings, so
their instruction block is handed to the stager as a section; any step the stager can't
map is returned as unparsed, never guessed.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from guide_parser import GuideParseError, GuideSnapshot, extract_links, page_url

CHECK_PAGE = "checking-for-cfw"
LUMA_PAGE = "restoring-updating-cfw"
GM9_PAGE = "godmode9-usage"
GM9_HEADING = "Updating GodMode9"

RULE_RE = re.compile(r"Luma3DS version of (?P<v>\d+(?:\.\d+)*)(?P<how> or lower| or greater)?", re.I)
HOME_RE = re.compile(r"boots to the normal HOME Menu", re.I)
VERSION_RE = re.compile(r"(\d+(?:\.\d+)*)")


# --- Checking for CFW --------------------------------------------------------------------

@dataclass(frozen=True)
class CfwRule:
    kind: str  # "le", "ge", "eq", or "home"
    version: tuple[int, ...]
    slug: str
    text: str


def _next_block(page: dict) -> dict:
    for b in page["blocks"]:
        if b["heading"].casefold() == "what to do next":
            return b
    raise GuideParseError(page["slug"], "no 'What to do next' section")


def cfw_rules(snapshot: GuideSnapshot) -> list[CfwRule]:
    """The routing rules from checking-for-cfw's tip notes, in page order."""
    rules = []
    for note in _next_block(snapshot.page(CHECK_PAGE))["notes"]:
        if note["kind"] != "tip":
            continue
        links = [l for l in extract_links(note["text"]) if l["slug"]]
        if not links:
            continue
        slug = links[0]["slug"]
        if HOME_RE.search(note["text"]):
            rules.append(CfwRule("home", (), slug, note["text"]))
        elif m := RULE_RE.search(note["text"]):
            how = (m["how"] or "").strip().casefold()
            kind = {"or lower": "le", "or greater": "ge"}.get(how, "eq")
            rules.append(CfwRule(kind, tuple(int(x) for x in m["v"].split(".")), slug, note["text"]))
    return rules


def wrong_menu_warnings(snapshot: GuideSnapshot) -> list[str]:
    """The guide's warnings for menus that aren't the Luma3DS configuration (e.g. held Start)."""
    return [n["text"] for n in _next_block(snapshot.page(CHECK_PAGE))["notes"] if n["kind"] in ("warning", "danger")]


def _matches(rule: CfwRule, v: tuple[int, ...]) -> bool:
    width = max(len(v), len(rule.version))
    a, b = v + (0,) * (width - len(v)), rule.version + (0,) * (width - len(rule.version))
    if rule.kind == "le":
        return a <= b
    if rule.kind == "ge":
        return a >= b
    return v[: len(rule.version)] == rule.version  # "7.1" means any 7.1.x


def route_existing_cfw(snapshot: GuideSnapshot, luma_version_text: str) -> tuple[str, str]:
    """(slug, message) for the Luma3DS version the user sees when holding (Select) on boot.

    Text like "home" or "HOME Menu" follows the guide's "boots to the normal HOME Menu" rule.
    Anything that can't be read, or matches no rule, sends the user to checking-for-cfw itself.
    """
    text = (luma_version_text or "").strip()
    try:
        rules = cfw_rules(snapshot)
    except GuideParseError as e:
        return CHECK_PAGE, f"The guide's Checking for CFW page couldn't be read ({e.reason}). See {e.url}"
    if re.search(r"\bhome\b", text, re.I):
        rule = next((r for r in rules if r.kind == "home"), None)
        if rule:
            return rule.slug, rule.text
    m = VERSION_RE.search(re.sub(r"luma\s*3ds", " ", text, flags=re.I))  # not the "3" in "Luma3DS"
    if not m:
        return CHECK_PAGE, (f"Couldn't read a Luma3DS version from {text!r}. Follow {page_url(CHECK_PAGE)} "
                            "and compare what you see with the options listed there.")
    version = tuple(int(x) for x in m.group(1).split("."))
    for rule in rules:
        if rule.kind != "home" and _matches(rule, version):
            return rule.slug, rule.text
    return CHECK_PAGE, (f"The guide has no instruction for Luma3DS {m.group(1)}. Follow {page_url(CHECK_PAGE)}, "
                        "and ask for help as it describes if nothing there matches.")


# --- what's on the card ------------------------------------------------------------------

@dataclass(frozen=True)
class Installed:
    boot_firm: bool
    luma_dir: bool
    luma_config: bool
    payloads: tuple[str, ...]
    gm9_dir: bool

    @property
    def godmode9_firm(self) -> bool:
        return any(p.casefold() == "godmode9.firm" for p in self.payloads)


def detect_installed(sd_root: Path) -> Installed:
    """Read-only look at the CFW files on the card."""
    root = Path(sd_root)
    payload_dir = root / "luma" / "payloads"
    payloads = tuple(sorted(p.name for p in payload_dir.glob("*.firm"))) if payload_dir.is_dir() else ()
    return Installed(
        boot_firm=(root / "boot.firm").is_file(),
        luma_dir=(root / "luma").is_dir(),
        luma_config=(root / "luma" / "config.ini").is_file(),
        payloads=payloads,
        gm9_dir=(root / "gm9").is_dir(),
    )


# --- updates -----------------------------------------------------------------------------

@dataclass
class UpdatePlan:
    name: str
    url: str
    specs: list = field(default_factory=list)  # downloader.DownloadSpec for the files the guide needs
    plan: object = None  # stager.StagePlan when the steps could be mapped
    unparsed: list[str] = field(default_factory=list)
    message: str = ""

    @property
    def ready(self) -> bool:
        return self.plan is not None and not self.unparsed


def _subsection(page: dict, heading: str | None) -> list[dict]:
    """Blocks under `heading` until the next heading of the same or higher level (all if None)."""
    if heading is None:
        return page["blocks"]
    blocks = page["blocks"]
    for i, b in enumerate(blocks):
        if b["heading"].casefold() == heading.casefold():
            return [x for x in _until_level(blocks[i + 1:], b["level"])]
    raise GuideParseError(page["slug"], f"no '{heading}' heading")


def _until_level(blocks: list[dict], level: int):
    for b in blocks:
        if b["level"] <= level:
            return
        yield b


def _instructions(blocks: list[dict], slug: str) -> dict:
    for b in blocks:
        if b["heading"].casefold() == "instructions" and b["steps"]:
            return b
    raise GuideParseError(slug, "no Instructions steps")


def _requirements(blocks: list[dict]) -> list[dict]:
    from guide_parser import item_text

    for b in blocks:
        if b["heading"].casefold() == "what you need":
            return [{"text": item_text(it), "links": extract_links(item_text(it))} for it in b["bullets"]]
    return []


class _SectionView:
    """Presents one instruction block as the only section of a page, for stager.plan_stage."""

    def __init__(self, page: dict, block: dict, label: str):
        self._page = {**page, "sections": [{"number": label, "title": block["heading"], **{
            k: block[k] for k in ("intro", "steps", "bullets", "notes", "embeds")}}]}

    def stage_page(self, slug: str) -> dict:
        return self._page


def _plan(snapshot: GuideSnapshot, slug: str, heading: str | None, name: str, anchor: str,
          downloads: list | None, sd_root: Path | None) -> UpdatePlan:
    import downloader
    import stager

    url = page_url(slug) + (f"#{anchor}" if anchor else "")
    try:
        page = snapshot.page(slug)
        blocks = _subsection(page, heading)
        block = _instructions(blocks, slug)
    except GuideParseError as e:
        return UpdatePlan(name, url, message=f"Couldn't read the guide's {name} steps ({e.reason}); "
                                             f"follow the guide page instead: {url}")
    reqs = _requirements(blocks)
    result = UpdatePlan(name, url, [downloader.spec_for_requirement(slug, r) for r in reqs])
    if downloads is None or sd_root is None:
        return result
    plan = stager.plan_stage(_SectionView(page, block, heading or block["heading"]), slug, downloads, Path(sd_root))
    result.plan, result.unparsed = plan, list(plan.unparsed)
    if result.unparsed:
        result.message = ("The assistant can't do every step of this update itself, so it won't write anything. "
                          f"Follow the guide page instead: {url}")
    return result


def luma_update_info(snapshot: GuideSnapshot) -> UpdatePlan:
    """What restoring-updating-cfw needs (no planning)."""
    return _plan(snapshot, LUMA_PAGE, None, "Luma3DS update", "", None, None)


def plan_luma_update(snapshot: GuideSnapshot, downloads: list, sd_root: Path) -> UpdatePlan:
    return _plan(snapshot, LUMA_PAGE, None, "Luma3DS update", "", downloads, sd_root)


def gm9_update_info(snapshot: GuideSnapshot) -> UpdatePlan:
    """The guide's GodMode9 update. Offered only when it links a GitHub release to download."""
    info = _plan(snapshot, GM9_PAGE, GM9_HEADING, "GodMode9 update", "updating-godmode9", None, None)
    if not info.message and not any(s.kind == "github" for s in info.specs):
        info.message = (f"The guide gives no GodMode9 download to update from. See {info.url}, or update "
                        "GodMode9 from Universal-Updater on the console.")
    return info


def plan_gm9_update(snapshot: GuideSnapshot, downloads: list, sd_root: Path) -> UpdatePlan:
    return _plan(snapshot, GM9_PAGE, GM9_HEADING, "GodMode9 update", "updating-godmode9", downloads, sd_root)
