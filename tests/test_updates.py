from urllib.error import HTTPError, URLError

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from selects.server import update_routes as updates


def release(tag="v0.1.19", **extra):
    name = "selects-windows-x64.zip"
    return {"tag_name": tag, "draft": False, "prerelease": False, "assets": [{
        "name": name, "state": "uploaded",
        "browser_download_url": f"{updates.REPOSITORY}/releases/download/{tag}/{name}",
    }], **extra}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(updates, "__version__", "0.1.18")
    monkeypatch.setattr(updates, "_platform_asset", lambda: ("Windows", "selects-windows-x64.zip"))
    app = FastAPI()
    updates.register_update_routes(app)
    return TestClient(app)


def test_opening_updates_does_not_contact_github(client, monkeypatch):
    def unexpected():
        pytest.fail("Opening the page must not contact GitHub")
    monkeypatch.setattr(updates, "_fetch_release", unexpected)
    info = client.get("/api/updates").json()
    assert info["current_version"] == "0.1.18"
    assert info["status"] == "unchecked"
    assert info["download_url"] is None


def test_new_release_download_and_cached_checks(client, monkeypatch):
    calls = []
    monkeypatch.setattr(updates, "_fetch_release", lambda: calls.append(1) or release())
    info = client.post("/api/updates/check").json()
    assert info["status"] == "available"
    assert info["latest_version"] == "0.1.19"
    assert info["download_url"].endswith("v0.1.19/selects-windows-x64.zip")
    assert client.post("/api/updates/check").json() == info
    assert client.get("/api/updates").json() == info
    assert len(calls) == 1


@pytest.mark.parametrize("tag, expected", [
    ("v0.1.18", "current"), ("v0.1.9", "current"), ("v0.1.100", "available"),
    ("v0.2.0", "available"), ("v0.1.19-rc1", "unavailable"),
])
def test_numeric_stable_version_comparison(client, monkeypatch, tag, expected):
    monkeypatch.setattr(updates, "_fetch_release", lambda: release(tag))
    assert client.post("/api/updates/check").json()["status"] == expected


@pytest.mark.parametrize("payload", [release(prerelease=True), release(draft=True), [], {}, {"tag_name": None}])
def test_unusable_release_never_offers_download(client, monkeypatch, payload):
    monkeypatch.setattr(updates, "_fetch_release", lambda: payload)
    info = client.post("/api/updates/check").json()
    assert info["status"] == "unavailable"
    assert info["download_url"] is None


def test_arbitrary_asset_url_is_not_offered(client, monkeypatch):
    payload = release()
    payload["assets"][0]["browser_download_url"] = "https://example.com/updater.exe"
    monkeypatch.setattr(updates, "_fetch_release", lambda: payload)
    info = client.post("/api/updates/check").json()
    assert info["status"] == "available"
    assert info["download_url"] is None


def test_missing_platform_asset_falls_back_to_release_page(client, monkeypatch):
    monkeypatch.setattr(updates, "_platform_asset", lambda: ("macOS", None))
    monkeypatch.setattr(updates, "_fetch_release", lambda: release())
    info = client.post("/api/updates/check").json()
    assert info["status"] == "available"
    assert info["download_url"] is None
    assert info["release_url"] == f"{updates.REPOSITORY}/releases/tag/v0.1.19"


def test_frozen_app_identifies_as_desktop(client, monkeypatch):
    monkeypatch.setattr(updates.sys, "frozen", True, raising=False)
    assert client.get("/api/updates").json()["installation"] == "desktop"


@pytest.mark.parametrize("failure", [URLError("offline"), TimeoutError(), HTTPError(updates.LATEST_API, 429, "rate limited", {}, None)])
def test_failed_check_can_retry_after_cache_expires(client, monkeypatch, failure):
    clock = [100.0]
    monkeypatch.setattr(updates.time, "monotonic", lambda: clock[0])
    def fail():
        raise failure
    monkeypatch.setattr(updates, "_fetch_release", fail)
    info = client.post("/api/updates/check").json()
    assert info["status"] == "unavailable"
    assert info["checked_at"]
    assert info["download_url"] is None
    monkeypatch.setattr(updates, "_fetch_release", lambda: release())
    clock[0] += 61
    assert client.post("/api/updates/check").json()["status"] == "available"


@pytest.mark.parametrize("system, machine, asset", [
    ("win32", "AMD64", "selects-windows-x64.zip"),
    ("win32", "ARM64", None),
    ("darwin", "arm64", "selects-macos-arm64.zip"),
    ("darwin", "x86_64", None),
    ("linux", "x86_64", "selects-linux-x64.zip"),
    ("linux", "aarch64", None),
])
def test_platform_asset_matches_architecture(monkeypatch, system, machine, asset):
    monkeypatch.setattr(updates.sys, "platform", system)
    monkeypatch.setattr(updates.platform, "machine", lambda: machine)
    assert updates._platform_asset()[1] == asset


def test_update_routes_work_without_a_library(monkeypatch):
    from selects.server.app import build_app
    from selects.server.library_manager import LibraryManager

    client = TestClient(build_app(manager=LibraryManager(), run_background=False))
    assert client.get("/api/updates").status_code == 200
