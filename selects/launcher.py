"""Robust UI launcher for the packaged desktop app.

Double-clicking the bundled exe starts the server, but simply calling
``webbrowser.open`` on a fixed delay is unreliable: on a slow cold-start the
browser can open before the port is listening (blank "can't reach this page"),
and ``webbrowser`` may not locate a browser at all when launched by double-click.

This module instead (1) waits until the server actually answers, then (2) opens
a chromeless *app window* using a Chromium browser that ships with the OS (Edge
on Windows), falling back to the system default browser.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import time
import urllib.request

log = logging.getLogger(__name__)


def _server_ready(url: str, timeout: float = 45.0) -> bool:
    """Poll ``url`` until it responds or ``timeout`` elapses."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1):
                return True
        except Exception:
            time.sleep(0.3)
    return False


def _find_chromium() -> str | None:
    """Locate a Chromium-family browser for app-mode, or None."""
    candidates: list[str] = []
    if sys.platform == "win32":
        pf = os.environ.get("PROGRAMFILES", r"C:\Program Files")
        pfx86 = os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")
        local = os.environ.get("LOCALAPPDATA", "")
        candidates = [
            os.path.join(pfx86, r"Microsoft\Edge\Application\msedge.exe"),
            os.path.join(pf, r"Microsoft\Edge\Application\msedge.exe"),
            os.path.join(pf, r"Google\Chrome\Application\chrome.exe"),
            os.path.join(pfx86, r"Google\Chrome\Application\chrome.exe"),
            os.path.join(local, r"Google\Chrome\Application\chrome.exe"),
        ]
    elif sys.platform == "darwin":
        candidates = [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
            "/Applications/Chromium.app/Contents/MacOS/Chromium",
        ]
    else:  # linux
        for name in ("google-chrome", "chromium", "chromium-browser", "microsoft-edge"):
            found = shutil.which(name)
            if found:
                candidates.append(found)
    for path in candidates:
        if path and os.path.exists(path):
            return path
    return None


def open_ui(url: str) -> None:
    """Wait for the server, then open the UI in an app window (or a browser)."""
    if not _server_ready(url):
        log.warning("server did not become ready at %s; open it manually", url)
        print(f"[selects] server not ready; open {url} in your browser")
        return
    exe = _find_chromium()
    if exe:
        try:
            data_dir = os.path.join(
                os.path.expanduser("~"), ".selects-app", "browser-profile"
            )
            subprocess.Popen(
                [exe, f"--app={url}", "--new-window", f"--user-data-dir={data_dir}"]
            )
            print(f"[selects] opened app window ({os.path.basename(exe)})")
            return
        except Exception as exc:  # noqa: BLE001
            log.warning("app-mode launch failed (%s); falling back to browser", exc)
    import webbrowser

    if webbrowser.open(url):
        print(f"[selects] opened {url} in your default browser")
    else:
        print(f"[selects] could not open a browser; go to {url} manually")
