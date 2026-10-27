"""The guide's console-side stages, one per numbered section, with SD checks between them.

Stage text always comes from the parsed guide. The modules here only add what the assistant
can check on the SD card once a section is finished (attached by the guide's own section
titles), and notes on steps the assistant does itself. A "fail" check blocks the next stage;
a "warn" check can be overridden by the user.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from guide_parser import GuideSnapshot, page_url
from sdcard import Check
from stages import boot9strap, finalize, mset9

FINALIZE = "finalizing-setup"
BACKUP_KEY = "backup"

Verifier = Callable[[Path], list[Check]]
Annotator = Callable[[str], "str | None"]

# (page predicate, section title, verifier) - titles are matched case-insensitively.
VERIFIERS: list[tuple[Callable[[str], bool], str, Verifier]] = [
    (lambda slug: slug.startswith("installing-boot9strap-"), "installing boot9strap", boot9strap.verify_installed),
    (lambda slug: slug.startswith("installing-boot9strap-(mset9"), "removing mset9", mset9.verify_removed),
    (lambda slug: slug == FINALIZE, "setup script", finalize.verify_backups),
]
ANNOTATORS: list[tuple[Callable[[str], bool], str, Annotator]] = [
    (lambda slug: slug == FINALIZE, "setup script", finalize.annotate_step),
]


@dataclass(frozen=True)
class Stage:
    key: str
    slug: str
    number: str | None
    title: str
    page_title: str
    section: dict | None
    stages_files: bool = False
    verifiers: tuple[Verifier, ...] = ()
    annotators: tuple[Annotator, ...] = ()
    troubleshooting: tuple[str, ...] = field(default_factory=tuple)

    @property
    def url(self) -> str:
        base = page_url(self.slug)
        if self.number is None:
            return base
        return f"{base}#{anchor(f'Section {self.number} - {self.title}')}"

    @property
    def needs_card(self) -> bool:
        return bool(self.verifiers) or self.key == BACKUP_KEY

    def annotate(self, step_text: str) -> str | None:
        for a in self.annotators:
            if note := a(step_text):
                return note
        return None


def anchor(heading: str) -> str:
    """VitePress's heading slug (as in @mdit-vue/shared)."""
    s = unicodedata.normalize("NFKD", heading)
    s = re.sub(r"[\u0000-\u001f]", "", s)
    s = re.sub(r"[\s~`!@#$%^&*()\-_+=\[\]{}|\\;:\"'“”‘’<>,.?/]+", "-", s)
    s = re.sub(r"-{2,}", "-", s).strip("-")
    s = re.sub(r"^(\d)", r"_\1", s)
    return s.lower()


def _troubleshooting(page: dict) -> tuple[str, ...]:
    urls = []
    for link in page["troubleshooting"]:
        url = page_url(link["slug"]) + (f"#{link['anchor']}" if link["anchor"] else "")
        if page_url(link["slug"]) not in [u.split("#")[0] for u in urls]:
            urls.append(page_url(link["slug"]))
    if page_url("troubleshooting") not in urls:
        urls.append(page_url("troubleshooting"))
    return tuple(urls)


def build_stages(snapshot: GuideSnapshot, method_slug: str) -> list[Stage]:
    import stager

    stages: list[Stage] = []
    for slug in (method_slug, FINALIZE):
        page = snapshot.stage_page(slug)
        prep = stager.prep_section_number(page)
        trouble = _troubleshooting(page)
        for sec in page["sections"]:
            if not sec["steps"]:
                continue
            title = sec["title"].casefold()
            stages.append(Stage(
                key=f"{slug}#{sec['number']}", slug=slug, number=sec["number"], title=sec["title"],
                page_title=page["title"], section=sec, stages_files=sec["number"] == prep,
                verifiers=tuple(v for match, t, v in VERIFIERS if match(slug) and t == title),
                annotators=tuple(a for match, t, a in ANNOTATORS if match(slug) and t == title),
                troubleshooting=trouble,
            ))
    fin = snapshot.page(FINALIZE)
    stages.append(Stage(
        key=BACKUP_KEY, slug=FINALIZE, number=None, title="Copy your backups to this computer",
        page_title=fin["title"], section=None, troubleshooting=_troubleshooting(fin),
    ))
    return stages
