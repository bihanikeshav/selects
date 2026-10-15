"""Manual, read-only release checks. Never downloads or executes an updater."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import platform
import re
import sys
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from fastapi import FastAPI

from selects import __version__

REPOSITORY = "https://github.com/bihanikeshav/selects"
LATEST_API = "https://api.github.com/repos/bihanikeshav/selects/releases/latest"
_STABLE = re.compile(r"v?(\d+)\.(\d+)\.(\d+)")


def _version(tag: str) -> tuple[int, ...]:
    match = _STABLE.fullmatch(tag)
    if match is None:
        raise ValueError("Not a stable version")
    return tuple(int(part) for part in match.groups())


def _platform_asset() -> tuple[str, str | None]:
    machine = platform.machine().lower()
    if sys.platform == "win32":
        return "Windows", "selects-windows-x64.zip" if machine in ("amd64", "x86_64") else None
    if sys.platform == "darwin":
        return "macOS", "selects-macos-arm64.zip" if machine in ("arm64", "aarch64") else None
    if sys.platform.startswith("linux"):
        return "Linux", "selects-linux-x64.zip" if machine in ("amd64", "x86_64") else None
    return platform.system(), None


def _metadata() -> dict:
    label, _ = _platform_asset()
    return {
        "current_version": __version__,
        "platform": label,
        "installation": "desktop" if getattr(sys, "frozen", False) or "__compiled__" in globals() else "python",
        "status": "unchecked",
        "latest_version": None,
        "release_url": f"{REPOSITORY}/releases/latest",
        "download_url": None,
        "checked_at": None,
        "message": None,
    }


def _fetch_release() -> dict:
    request = Request(LATEST_API, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": f"Selects/{__version__}",
    })
    with urlopen(request, timeout=8) as response:
        data = response.read(1_000_001)
    if len(data) > 1_000_000:
        raise ValueError("Release response is too large")
    return json.loads(data)


def _release_status(release: dict) -> dict:
    tag = release["tag_name"]
    latest = _version(tag)
    if release.get("draft") or release.get("prerelease"):
        raise ValueError("Not a published stable release")
    newer = latest > _version(__version__)
    _, expected_asset = _platform_asset()
    download = None
    if newer and expected_asset:
        # Only offer this repository's expected asset URL, never an arbitrary
        # URL supplied in release metadata. A platform build may be absent.
        expected_url = f"{REPOSITORY}/releases/download/{tag}/{expected_asset}"
        for asset in release.get("assets", []):
            if (asset.get("name") == expected_asset
                    and asset.get("browser_download_url") == expected_url
                    and asset.get("state") == "uploaded"):
                download = expected_url
                break
    return {
        "status": "available" if newer else "current",
        "latest_version": ".".join(str(part) for part in latest),
        "release_url": f"{REPOSITORY}/releases/tag/{tag}",
        "download_url": download,
    }


def register_update_routes(app: FastAPI) -> None:
    lock = threading.Lock()
    cached: dict = {}
    checked = float("-inf")

    @app.get("/api/updates")
    def updates() -> dict:
        # Merely opening the page never contacts GitHub.
        with lock:
            return {**_metadata(), **cached}

    @app.post("/api/updates/check")
    def check_updates() -> dict:
        nonlocal cached, checked
        with lock:
            # Bound repeated clicks / parallel requests, including failures.
            if time.monotonic() - checked < 60:
                return {**_metadata(), **cached}
            result = {"checked_at": datetime.now(timezone.utc).isoformat()}
            try:
                result.update(_release_status(_fetch_release()))
            except HTTPError as exc:
                result.update(status="unavailable", message=(
                    "No stable release is available yet." if exc.code == 404 else
                    "GitHub could not complete the check. Try again in a minute."
                ))
            except (URLError, OSError, ValueError, KeyError, TypeError, AttributeError):
                result.update(status="unavailable", message=
                              "Could not check for updates. Check your connection and try again in a minute.")
            cached = result
            checked = time.monotonic()
            return {**_metadata(), **cached}
