from __future__ import annotations

import hashlib

import httpx
import pytest

import downloader
from downloader import ChecksumMismatch, DownloadError, DownloadSpec, fetch, make_client, plan_downloads, resolve

LUMA = b"luma zip bytes" * 100
OTHERAPP = b"otherapp" * 50
B9S = b"b9s zip"
SB9SI = b"sb9si zip"
FIN_FIRM, FIN_ROMFS = b"helper firm", b"romfs" * 300

B9S_URL = "https://github.com/SciresM/boot9strap/releases/download/1.4/boot9strap-1.4.zip"
SB9SI_URL = "https://github.com/d0k3/SafeB9SInstaller/releases/download/v0.0.7/SafeB9SInstaller-20170605-122940.zip"


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def asset(name, body, url, digest=True):
    return {"name": name, "size": len(body), "browser_download_url": url,
            "digest": f"sha256:{sha(body)}" if digest else None}


def releases():
    return {
        "/repos/LumaTeam/Luma3DS/releases/latest": {"tag_name": "v13.4", "published_at": "2026-04-02T00:00:00Z", "assets": [
            asset("Luma3DSv13.4.zip", LUMA, "https://github.com/LumaTeam/Luma3DS/releases/download/v13.4/Luma3DSv13.4.zip")]},
        "/repos/TuxSH/universal-otherapp/releases/latest": {"tag_name": "v1.4.0", "assets": [
            asset("otherapp.bin", OTHERAPP, "https://github.com/TuxSH/universal-otherapp/releases/download/v1.4.0/otherapp.bin", digest=False)]},
        "/repos/SciresM/boot9strap/releases/tags/1.4": {"tag_name": "1.4", "assets": [
            asset("boot9strap-1.4-devkit.zip", b"dev", "https://github.com/SciresM/boot9strap/releases/download/1.4/boot9strap-1.4-devkit.zip", False),
            asset("boot9strap-1.4.zip", B9S, B9S_URL, digest=False)]},
        "/repos/d0k3/SafeB9SInstaller/releases/tags/v0.0.7": {"tag_name": "v0.0.7", "assets": [
            asset("SafeB9SInstaller-20170605-122940.zip", SB9SI, SB9SI_URL, digest=False)]},
        "/repos/hacks-guide/finalize/releases/latest": {"tag_name": "v1.10.4", "assets": [
            asset("finalize.romfs", FIN_ROMFS, "https://github.com/hacks-guide/finalize/releases/download/v1.10.4/finalize.romfs"),
            asset("x_finalize_helper.firm", FIN_FIRM, "https://github.com/hacks-guide/finalize/releases/download/v1.10.4/x_finalize_helper.firm")]},
    }


class FakeGitHub:
    def __init__(self, bodies=None):
        self.releases = releases()
        self.bodies = {a["browser_download_url"]: b for a, b in self._pairs()}
        self.bodies.update(bodies or {})
        self.requests = []

    def _pairs(self):
        data = {"Luma3DSv13.4.zip": LUMA, "otherapp.bin": OTHERAPP, "boot9strap-1.4.zip": B9S,
                "boot9strap-1.4-devkit.zip": b"dev", "SafeB9SInstaller-20170605-122940.zip": SB9SI,
                "finalize.romfs": FIN_ROMFS, "x_finalize_helper.firm": FIN_FIRM}
        for rel in self.releases.values():
            for a in rel["assets"]:
                yield a, data[a["name"]]

    def __call__(self, request: httpx.Request):
        self.requests.append(request)
        if request.url.host == "api.github.com":
            rel = self.releases.get(request.url.path)
            return httpx.Response(200, json=rel) if rel else httpx.Response(404)
        body = self.bodies.get(str(request.url))
        return httpx.Response(200, content=body) if body is not None else httpx.Response(404)


@pytest.fixture
def gh():
    return FakeGitHub()


@pytest.fixture
def client(gh):
    return make_client(token="", transport=httpx.MockTransport(gh))


SOUNDHAX = "installing-boot9strap-(soundhax)"


# --- reading requirements -------------------------------------------------------------

def test_soundhax_specs(real_snapshot):
    specs = downloader.specs_for_page(real_snapshot.page(SOUNDHAX))
    by = {s.name: s for s in specs}
    assert by["Soundhax"].kind == "manual"  # per-region file from soundhax.com
    assert (by["SafeB9SInstaller"].tag, by["SafeB9SInstaller"].asset) == ("v0.0.7", "SafeB9SInstaller-20170605-122940.zip")
    assert (by["boot9strap"].tag, by["boot9strap"].asset) == ("1.4", "boot9strap-1.4.zip")
    assert (by["Luma3DS"].tag, by["Luma3DS"].asset_suffix) == (None, ".zip")
    assert (by["universal-otherapp"].tag, by["universal-otherapp"].asset) == (None, "otherapp.bin")


def test_finalize_and_mset9_specs(real_snapshot):
    fin = downloader.specs_for_page(real_snapshot.page("finalizing-setup"))
    assert [(s.tag, s.asset) for s in fin] == [(None, "x_finalize_helper.firm"), (None, "finalize.romfs")]
    mset9 = downloader.specs_for_page(real_snapshot.page("installing-boot9strap-(mset9-cli)"))
    assert [(s.name, s.kind) for s in mset9] == [("MSET9", "github"), ("Python", "prerequisite")]


def test_skater_is_manual(real_snapshot):
    [spec] = downloader.specs_for_page(real_snapshot.page("installing-boot9strap-(super-skaterhax)"))
    assert spec.kind == "manual" and spec.url == "https://skater.nintendohomebrew.com"


def test_release_without_named_asset_is_manual():
    spec = downloader.spec_for_requirement("p", {"text": "The latest [tool](https://github.com/a/b/releases/latest)",
                                                 "links": [{"text": "tool", "href": "https://github.com/a/b/releases/latest"}]})
    assert spec.kind == "manual" and "which file" in spec.reason


# --- resolving and downloading --------------------------------------------------------

def test_plan_shows_versions_and_checksum_status(real_snapshot, client):
    plan = plan_downloads(real_snapshot, [SOUNDHAX, "finalizing-setup"], client)
    got = {r.asset_name: (r.tag, r.digest is not None) for r in plan.resolved}
    assert got == {
        "SafeB9SInstaller-20170605-122940.zip": ("v0.0.7", False),
        "boot9strap-1.4.zip": ("1.4", False),
        "Luma3DSv13.4.zip": ("v13.4", True),
        "otherapp.bin": ("v1.4.0", False),
        "x_finalize_helper.firm": ("v1.10.4", True),
        "finalize.romfs": ("v1.10.4", True),
    }
    assert [m.name for m in plan.manual] == ["Soundhax"]


def test_pinned_url_is_used_verbatim(real_snapshot, client):
    plan = plan_downloads(real_snapshot, [SOUNDHAX], client)
    assert next(r for r in plan.resolved if r.asset_name == "boot9strap-1.4.zip").download_url == B9S_URL


def test_download_all_and_cache(real_snapshot, client, gh, tmp_path):
    plan = plan_downloads(real_snapshot, [SOUNDHAX, "finalizing-setup"], client)
    first = downloader.download_all(plan, client, tmp_path)
    assert {d.verified for d in first if d.resolved.digest} == {"published"}
    assert {d.verified for d in first if not d.resolved.digest} == {"first-use"}
    assert all(d.path.read_bytes() and d.sha256 == sha(d.path.read_bytes()) for d in first)
    n = len(gh.requests)
    again = downloader.download_all(plan, client, tmp_path)
    assert all(d.from_cache for d in again) and len(gh.requests) == n


def test_published_checksum_mismatch_aborts(client, gh, tmp_path):
    gh.bodies["https://github.com/LumaTeam/Luma3DS/releases/download/v13.4/Luma3DSv13.4.zip"] = b"x" * len(LUMA)
    spec = DownloadSpec("Luma3DS", "p", "", "https://github.com/LumaTeam/Luma3DS/releases/latest", "github",
                        "LumaTeam", "Luma3DS", None, asset_suffix=".zip")
    with pytest.raises(ChecksumMismatch, match="does not match"):
        fetch(resolve(spec, client), client, tmp_path)
    assert not any(p.is_file() for p in tmp_path.rglob("*"))  # nothing cached, no .part left


def test_size_mismatch_aborts(client, gh, tmp_path):
    gh.bodies[B9S_URL] = B9S + b"extra"
    spec = downloader.spec_for_requirement("p", {"text": f"[boot9strap]({B9S_URL})", "links": [{"text": "boot9strap", "href": B9S_URL}]})
    with pytest.raises(ChecksumMismatch, match="bytes"):
        fetch(resolve(spec, client), client, tmp_path)


def test_first_use_hash_change_aborts(client, gh, tmp_path):
    spec = downloader.spec_for_requirement("p", {"text": f"[boot9strap]({B9S_URL})", "links": [{"text": "boot9strap", "href": B9S_URL}]})
    r = resolve(spec, client)
    d = fetch(r, client, tmp_path)
    assert d.verified == "first-use"
    d.path.write_bytes(b"corrupt")  # local damage: re-download, still same upstream bytes
    assert fetch(r, client, tmp_path).verified == "recorded"
    d.path.unlink()
    gh.bodies[B9S_URL] = b"B9S zip"  # same size, different bytes upstream
    with pytest.raises(ChecksumMismatch, match="first downloaded"):
        fetch(r, client, tmp_path)


def test_ambiguous_or_missing_asset(client):
    spec = DownloadSpec("b9s", "p", "", "https://github.com/SciresM/boot9strap/releases/tag/1.4", "github",
                        "SciresM", "boot9strap", "1.4", asset_suffix=".zip")
    with pytest.raises(DownloadError, match="expected one"):
        resolve(spec, client)


def test_pinned_url_mismatch_with_github(client, gh):
    gh.releases["/repos/SciresM/boot9strap/releases/tags/1.4"]["assets"][1]["browser_download_url"] = "https://github.com/evil/x.zip"
    spec = downloader.spec_for_requirement("p", {"text": f"[boot9strap]({B9S_URL})", "links": [{"text": "boot9strap", "href": B9S_URL}]})
    with pytest.raises(DownloadError, match="differs"):
        resolve(spec, client)


def test_hosts_outside_allow_list_are_refused(tmp_path):
    def handler(request):
        if request.url.host == "github.com":
            return httpx.Response(302, headers={"Location": "https://evil.example.com/payload"})
        return httpx.Response(200, content=b"bad")

    c = make_client(token="", transport=httpx.MockTransport(handler))
    with pytest.raises(DownloadError, match="evil.example.com"):
        c.get("https://github.com/a/b/releases/download/1/x.zip")
    with pytest.raises(DownloadError):
        c.get("http://github.com/plain-http")


def test_token_only_goes_to_the_api(tmp_path):
    seen = {}

    def handler(request):
        seen[request.url.host] = request.headers.get("Authorization")
        return httpx.Response(200, json={})

    c = make_client(token="t0ken", transport=httpx.MockTransport(handler))
    c.get("https://api.github.com/x")
    c.get("https://github.com/y")
    assert seen == {"api.github.com": "Bearer t0ken", "github.com": None}


def test_manual_items_cannot_be_resolved(real_snapshot, client):
    [spec] = downloader.specs_for_page(real_snapshot.page("installing-boot9strap-(super-skaterhax)"))
    with pytest.raises(DownloadError, match="by hand"):
        resolve(spec, client)


def test_import_manual(real_snapshot, tmp_path):
    [spec] = downloader.specs_for_page(real_snapshot.page("installing-boot9strap-(super-skaterhax)"))
    src = tmp_path / "skater-usa.zip"
    src.write_bytes(b"zip")
    d = downloader.import_manual(spec, src, tmp_path / "cache")
    assert d.verified == "user-provided" and d.path.read_bytes() == b"zip" and d.sha256 == sha(b"zip")
    with pytest.raises(DownloadError):
        downloader.import_manual(spec, tmp_path / "missing.zip", tmp_path / "cache")
