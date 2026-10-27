from __future__ import annotations

import io
import tarfile

import httpx
import pytest

import guide_parser
from guide_parser import GuideParseError, GuideSnapshot, build_snapshot, page_url

COMMIT = "a" * 40

METHOD_PAGE = """\
---
title: ignored
head: [
    ['script', {src: '/assets/js/common.js'}]
]
---
# Installing boot9strap (Example)

## Compatibility Notes

Works on everything. If it fails, use [MSET9](installing-boot9strap-(mset9)) instead.

## What You Need

* The latest release of [SafeB9SInstaller](https://github.com/d0k3/SafeB9SInstaller/releases/download/v0.0.7/SafeB9SInstaller.zip) (direct download)
* The latest release of [Luma3DS](https://github.com/LumaTeam/Luma3DS/releases/latest) (the `.zip` file)

## Instructions

### Section I - Prep Work

In this section, you will copy files.

1. Power off your console
1. Copy `boot.firm` to the root of your SD card
    + Overwrite existing files

    ::: info

    ![](/images/screenshots/root-layout.png)

    :::

<!--@include: ./_include/chorus.md -->

::: warning

Do not skip this.

:::

#### Section II - Running It

1. Do the thing
    + If it fails, see [troubleshooting](troubleshooting-example#red-screen)

::: tip

Continue to [Finalizing Setup](finalizing-setup)

:::
"""

CHORUS = """\
1. Run the script:
    + **Windows**: double-click it
"""


def snapshot_for(make_docs, files, commit=COMMIT) -> GuideSnapshot:
    return build_snapshot(make_docs(files), commit)


@pytest.fixture
def example(make_docs):
    snap = snapshot_for(make_docs, {
        "installing-boot9strap-(example).md": METHOD_PAGE,
        "_include/chorus.md": CHORUS,
    })
    return snap.page("installing-boot9strap-(example)")


def test_title_and_scalar_frontmatter(example):
    assert example["title"] == "Installing boot9strap (Example)"
    assert example["frontmatter"] == {"title": "ignored"}


def test_sections_are_numbered_at_any_heading_level(example):
    sections = example["sections"]
    assert [(s["number"], s["title"]) for s in sections] == [
        ("I", "Prep Work"),
        ("II", "Running It"),
    ]
    assert sections[0]["intro"] == ["In this section, you will copy files."]


def test_steps_keep_sub_bullets_and_drop_image_only_notes(example):
    steps = example["sections"][0]["steps"]
    assert steps[1]["text"] == "Copy `boot.firm` to the root of your SD card"
    assert steps[1]["details"] == ["+ Overwrite existing files"]


def test_include_is_inlined_as_steps(example):
    steps = example["sections"][0]["steps"]
    assert [s["text"] for s in steps] == [
        "Power off your console",
        "Copy `boot.firm` to the root of your SD card",
        "Run the script:",
    ]
    assert steps[2]["details"] == ["+ **Windows**: double-click it"]


def test_column_zero_admonitions_become_section_notes(example):
    assert example["sections"][0]["notes"] == [{"kind": "warning", "title": "", "text": "Do not skip this."}]


def test_requirements_keep_links_with_parentheses_and_pinned_urls(example):
    reqs = example["requirements"]
    assert len(reqs) == 2
    assert reqs[0]["links"][0]["href"].endswith("/releases/download/v0.0.7/SafeB9SInstaller.zip")
    compat_links = example["compatibility"]["links"]
    assert compat_links[0]["slug"] == "installing-boot9strap-(mset9)"


def test_next_page_and_troubleshooting_links(example):
    assert example["next_page"] == "finalizing-setup"
    assert example["troubleshooting"] == [
        {"text": "troubleshooting", "href": "troubleshooting-example#red-screen",
         "slug": "troubleshooting-example", "anchor": "red-screen"},
    ]


def test_image_links_use_alt_text(make_docs):
    snap = snapshot_for(make_docs, {"chooser.md": (
        "# Chooser\n\n| PC | Phone |\n|:-:|:-:|\n"
        "| [![Windows](/images/windows.png)](installing-boot9strap-(mset9-cli)) "
        "| [![Android](/images/android.png)](installing-boot9strap-(mset9-play-store)) |\n"
    )})
    links = snap.page("chooser")["links"]
    assert [(l["text"], l["slug"]) for l in links] == [
        ("Windows", "installing-boot9strap-(mset9-cli)"),
        ("Android", "installing-boot9strap-(mset9-play-store)"),
    ]


def test_missing_include_is_recorded_with_raw_page_link(make_docs):
    snap = snapshot_for(make_docs, {"broken.md": "# Broken\n\n<!--@include: ./_include/nope.md -->\n"})
    assert snap.errors["broken"]["url"] == "https://3ds.hacks.guide/broken"
    with pytest.raises(GuideParseError) as err:
        snap.page("broken")
    assert err.value.url == page_url("broken")
    assert "nope.md" in str(err.value)


def test_unclosed_admonition_is_a_parse_error(make_docs):
    snap = snapshot_for(make_docs, {"open.md": "# Open\n\n::: warning\n\nnever closed\n"})
    assert "open" in snap.errors


def test_inner_fence_left_open_is_closed_by_its_parent(make_docs):
    # Shape used by finalizing-setup: ::: info wraps a ::: details with no close of its own.
    snap = snapshot_for(make_docs, {"nested.md": (
        "# Nested\n\n## Intro\n\n::: info\n\nOuter text\n\n::: details More\n\n- inner\n\n:::\n\n"
        "## What You Need\n\n* [thing](https://example.com/thing)\n"
    )})
    page = snap.page("nested")
    intro = next(b for b in page["blocks"] if b["heading"] == "Intro")
    assert intro["notes"] == [{"kind": "info", "title": "", "text": "Outer text\nMore\n- inner"}]
    assert page["requirements"][0]["links"][0]["href"] == "https://example.com/thing"


def test_four_colon_outer_fence_and_heading_inside_fence(make_docs):
    snap = snapshot_for(make_docs, {"deep.md": (
        "# Deep\n\n## Q\n\n:::: details Question\n\n## not a heading\n\n::: warning\n\nInner\n\n:::\n\n::::\n"
    )})
    headings = [b["heading"] for b in snap.page("deep")["blocks"]]
    assert headings == ["", "Deep", "Q"]


def test_include_outside_docs_is_refused(make_docs):
    snap = snapshot_for(make_docs, {"escape.md": "# Escape\n\n<!--@include: ../../secret.md -->\n"})
    assert "outside" in snap.errors["escape"]["reason"]


def test_unknown_page_raises_with_link(make_docs):
    snap = snapshot_for(make_docs, {"a.md": "# A\n"})
    with pytest.raises(GuideParseError) as err:
        snap.page("does-not-exist")
    assert err.value.url == "https://3ds.hacks.guide/does-not-exist"


def test_only_english_top_level_pages_are_parsed(make_docs):
    snap = snapshot_for(make_docs, {"a.md": "# A\n", "de_DE/a.md": "# A (de)\n", "_include/x.md": "x\n"})
    assert set(snap.pages) == {"a"}


def test_stage_page_validation(make_docs):
    snap = snapshot_for(make_docs, {"empty.md": "# Empty\n\nNothing here.\n"})
    with pytest.raises(GuideParseError):
        snap.stage_page("empty")


def test_retired_redirect_page_is_never_a_stage(make_docs):
    snap = snapshot_for(make_docs, {"installing-boot9strap-(usm).md": (
        "---\nhead:\n  - - meta\n    - http-equiv: refresh\n      content: '0; URL=410.html'\n---\n"
    )})
    assert snap.page("installing-boot9strap-(usm)")["redirect"] == "410"
    with pytest.raises(GuideParseError, match="retired"):
        snap.stage_page("installing-boot9strap-(usm)")


def test_title_falls_back_to_frontmatter(make_docs):
    snap = snapshot_for(make_docs, {"index.md": '---\nlayout: home\ntitle: "3DS Hacks Guide"\n---\n\nHello\n'})
    assert snap.page("index")["title"] == "3DS Hacks Guide"


def test_snapshot_round_trip(tmp_path, example, make_docs):
    snap = snapshot_for(make_docs, {"a.md": "# A\n"})
    path = tmp_path / "out" / "guide_snapshot.json"
    snap.save(path)
    loaded = GuideSnapshot.load(path)
    assert loaded.commit == COMMIT
    assert loaded.page("a")["title"] == "A"


def test_load_rejects_other_formats(tmp_path):
    path = tmp_path / "s.json"
    path.write_text('{"format": 999}', encoding="utf-8")
    with pytest.raises(GuideParseError):
        GuideSnapshot.load(path)


# --- network: GitHub is always mocked ---------------------------------------------------

def mock_client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_fetch_latest_commit():
    def handler(request):
        assert request.url.path == "/repos/hacks-guide/Guide_3DS/commits/master"
        return httpx.Response(200, text="b" * 40 + "\n")

    assert guide_parser.fetch_latest_commit(mock_client(handler)) == "b" * 40


def test_fetch_latest_commit_rejects_garbage():
    with pytest.raises(GuideParseError):
        guide_parser.fetch_latest_commit(mock_client(lambda r: httpx.Response(200, text="<html>")))


def test_check_for_update(make_docs):
    snap = snapshot_for(make_docs, {"a.md": "# A\n"})
    same = mock_client(lambda r: httpx.Response(200, text=COMMIT))
    newer = mock_client(lambda r: httpx.Response(200, text="c" * 40))
    assert guide_parser.check_for_update(snap, same) is None
    assert guide_parser.check_for_update(snap, newer) == "c" * 40


def _tarball(members: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def test_download_guide_keeps_only_needed_files(tmp_path):
    root = f"Guide_3DS-{COMMIT}/"
    body = _tarball({
        root + "docs/get-started.md": b"# Get Started\n",
        root + "docs/de_DE/get-started.md": b"# translated\n",
        root + "docs/_include/x.md": b"x\n",
        root + "docs/_internal/consoleVersionSelect.html": b"<select></select>",
        root + "docs/public/assets/js/selecting.js": b"// js",
        root + "docs/public/images/big.png": b"png",
        root + "rss.py": b"print('no')",
    })

    def handler(request):
        assert request.url.host == "codeload.github.com"
        assert request.url.path.endswith(COMMIT)
        return httpx.Response(200, content=body)

    docs = guide_parser.download_guide(COMMIT, tmp_path / "guide", mock_client(handler))
    got = sorted(p.relative_to(docs).as_posix() for p in docs.rglob("*") if p.is_file())
    assert got == [
        "_include/x.md",
        "_internal/consoleVersionSelect.html",
        "get-started.md",
        "public/assets/js/selecting.js",
    ]


def test_download_guide_blocks_path_traversal(tmp_path):
    body = _tarball({f"Guide_3DS-{COMMIT}/docs/../../../evil.md": b"x"})
    docs = guide_parser.download_guide(COMMIT, tmp_path / "guide", mock_client(lambda r: httpx.Response(200, content=body)))
    assert not (tmp_path / "evil.md").exists()
    assert not any(docs.rglob("*.md"))


# --- contract tests against the pinned real guide ----------------------------------------

METHOD_PAGES = [
    "installing-boot9strap-(soundhax)",
    "installing-boot9strap-(ssloth-browser)",
    "installing-boot9strap-(safecerthax)",
    "installing-boot9strap-(super-skaterhax)",
    "installing-boot9strap-(mset9-cli)",
]


def test_real_guide_has_no_parse_errors(real_snapshot):
    assert real_snapshot.errors == {}


@pytest.mark.parametrize("slug", METHOD_PAGES)
def test_real_method_pages_lead_to_finalizing(real_snapshot, slug):
    page = real_snapshot.stage_page(slug)
    assert page["requirements"], slug
    assert page["next_page"] == "finalizing-setup"
    assert page["sections"][0]["number"] == "I"


def test_real_pinned_downloads_are_kept_verbatim(real_snapshot):
    hrefs = [l["href"] for r in real_snapshot.page("installing-boot9strap-(soundhax)")["requirements"] for l in r["links"]]
    assert "https://github.com/d0k3/SafeB9SInstaller/releases/download/v0.0.7/SafeB9SInstaller-20170605-122940.zip" in hrefs
    assert "https://github.com/SciresM/boot9strap/releases/download/1.4/boot9strap-1.4.zip" in hrefs


def test_real_finalizing_needs_two_files(real_snapshot):
    names = [l["text"] for r in real_snapshot.stage_page("finalizing-setup")["requirements"] for l in r["links"]]
    assert names == ["x_finalize_helper.firm", "finalize.romfs"]


def test_real_mset9_chooser_links_by_os(real_snapshot):
    by_text = {l["text"]: l["slug"] for l in real_snapshot.page("installing-boot9strap-(mset9)")["links"]}
    assert by_text["Windows"] == by_text["macOS"] == by_text["Linux"] == "installing-boot9strap-(mset9-cli)"


def test_real_version_form(real_snapshot):
    form = real_snapshot.version_form
    assert form["selects"]["region"] == ["E", "U", "J", "K", "T", "C"]
    assert form["selects"]["prefix"] == ["Ver.", "Sys.", "Emu.", "(Other)"]
    assert form["micro_select"] not in ("major", "minor", "nver")
    assert form["models"] == {"old": "old3DS", "new": "new3DS"}
    assert "result_methodUnavailable" not in form["element_ids"]


def test_real_routing_sources_are_hashed(real_snapshot):
    routing = real_snapshot.routing
    assert set(routing) == {"public/assets/js/common.js", "public/assets/js/selecting.js"}
    assert all(len(f["sha256"]) == 64 for f in routing.values())
