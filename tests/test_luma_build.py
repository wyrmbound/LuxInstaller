"""Picking and fetching a Luma3DS fork's build (GitHub always mocked)."""
from __future__ import annotations

import hashlib

import httpx
import pytest

import downloader
import luma_build
from luma_build import LumaBuildError, LumaChoice

FIRM = b"FIRM" + b"luxios" * 64
DL = "https://github.com/wyrmbound/LuxiOS/releases/download/{tag}/{name}"


def gh(releases: list[dict], latest: dict | None = None, bodies: dict | None = None):
    bodies = bodies or {}

    def handler(request: httpx.Request):
        path = request.url.path
        if path.endswith("/releases/latest"):
            return httpx.Response(200, json=latest) if latest else httpx.Response(404, json={"message": "Not Found"})
        if path.endswith("/releases"):
            return httpx.Response(200, json=releases)
        for rel in [*releases, *([latest] if latest else [])]:
            if path.endswith(f"/releases/tags/{rel['tag_name']}"):
                return httpx.Response(200, json=rel)
        body = bodies.get(str(request.url))
        return httpx.Response(200, content=body) if body is not None else httpx.Response(404)

    return downloader.make_client(token="", transport=httpx.MockTransport(handler))


def release(tag, prerelease=False, draft=False, firm=FIRM, digest=True):
    return {"tag_name": tag, "prerelease": prerelease, "draft": draft, "published_at": "2026-10-27",
            "assets": [{"name": "boot.firm", "size": len(firm), "browser_download_url": DL.format(tag=tag, name="boot.firm"),
                        "digest": f"sha256:{hashlib.sha256(firm).hexdigest()}" if digest else None},
                       {"name": "splash.bin", "size": 3, "browser_download_url": DL.format(tag=tag, name="splash.bin"),
                        "digest": None}]}


CHOICE = LumaChoice("github", repo="wyrmbound/LuxiOS")


def test_prerelease_only_fork_uses_the_newest_prerelease(tmp_path):
    beta = release("luxios-2026.10.27-beta", prerelease=True)
    client = gh([beta, release("old-draft", draft=True)],
                bodies={DL.format(tag=beta["tag_name"], name="boot.firm"): FIRM})
    build = luma_build.resolve(CHOICE, client, tmp_path)
    assert build.version == "luxios-2026.10.27-beta (pre-release)"
    assert build.firm.read_bytes() == FIRM and build.verified == "published"


def test_stable_release_is_preferred(tmp_path):
    stable = release("v1.0")
    client = gh([release("v1.1-beta", prerelease=True), stable], latest=stable,
                bodies={DL.format(tag="v1.0", name="boot.firm"): FIRM})
    assert luma_build.resolve(CHOICE, client, tmp_path).version == "v1.0"


def test_published_checksum_is_enforced(tmp_path):
    beta = release("b1", prerelease=True)
    client = gh([beta], bodies={DL.format(tag="b1", name="boot.firm"): b"FIRM" + b"x" * (len(FIRM) - 4)})
    with pytest.raises(downloader.ChecksumMismatch):
        luma_build.resolve(CHOICE, client, tmp_path)


def test_release_firm_must_be_a_firm(tmp_path):
    junk = b"MZ" + b"0" * 100
    beta = release("b2", prerelease=True, firm=junk)
    client = gh([beta], bodies={DL.format(tag="b2", name="boot.firm"): junk})
    with pytest.raises(LumaBuildError, match="FIRM"):
        luma_build.resolve(CHOICE, client, tmp_path)


def test_no_releases(tmp_path):
    with pytest.raises(LumaBuildError, match="no published releases"):
        luma_build.resolve(CHOICE, gh([release("d", draft=True)]), tmp_path)


@pytest.mark.parametrize("text, repo", [
    ("wyrmbound/LuxiOS", "wyrmbound/LuxiOS"),
    ("https://github.com/wyrmbound/LuxiOS", "wyrmbound/LuxiOS"),
    ("https://github.com/wyrmbound/LuxiOS.git", "wyrmbound/LuxiOS"),
])
def test_parse_repo(text, repo):
    assert luma_build.parse_repo(text) == repo


def test_parse_repo_rejects():
    with pytest.raises(LumaBuildError):
        luma_build.parse_repo("LuxiOS")
