"""Native desktop-window entry point for the packaged app.

Double-clicking the bundled executable opens Selects as a real application
window (its own window + taskbar icon, no browser chrome) via pywebview, which
embeds the OS webview (WebView2 on Windows, WebKit on macOS, WebKitGTK on
Linux) — the same "web UI in a native shell" approach used by Electron/Tauri,
but pure-Python so it rides along in the existing PyInstaller bundle.

The FastAPI server runs in a background thread; the webview owns the main
thread (a GUI requirement). If the native window can't start on a given
machine, we fall back to opening the UI in an app window via the system
browser so the app is still usable.
"""
from __future__ import annotations

import logging
import threading
import time

log = logging.getLogger(__name__)

WINDOW_TITLE = "Selects"


class _WindowControls:
    """JS-exposed window controls for the custom (frameless) title bar."""

    def __init__(self) -> None:
        self.window = None
        self._maximized = False

    def minimize(self) -> None:
        if self.window is not None:
            self.window.minimize()

    def toggle_maximize(self) -> None:
        if self.window is None:
            return
        if self._maximized:
            self.window.restore()
        else:
            self.window.maximize()
        self._maximized = not self._maximized

    def close(self) -> None:
        if self.window is not None:
            self.window.destroy()


def _apply_windows_chrome(window) -> None:
    """Tint the native Windows-11 title bar to the brand blue with white text.

    Uses the DWM caption-color attributes (Windows 11 build 22000+). This is a
    safe alternative to a frameless custom title bar (which crashes in the
    PyInstaller bundle via a pywebview/WinForms layout recursion): it keeps the
    reliable native window but gives it branded chrome. No-op on older Windows
    or non-Windows — DwmSetWindowAttribute simply ignores unknown attributes.
    """
    import ctypes
    from ctypes import wintypes

    try:
        hwnd = int(window.native.Handle.ToInt64())
    except Exception as exc:  # noqa: BLE001 — native handle not available
        log.debug("caption color: no native handle (%s)", exc)
        return

    DWMWA_CAPTION_COLOR = 35
    DWMWA_TEXT_COLOR = 36
    # COLORREF is 0x00BBGGRR. Brand blue #1A5DCC -> 0x00CC5D1A.
    caption = wintypes.DWORD(0x00CC5D1A)
    text = wintypes.DWORD(0x00FFFFFF)  # white
    try:
        dwm = ctypes.windll.dwmapi
        dwm.DwmSetWindowAttribute(
            wintypes.HWND(hwnd), DWMWA_CAPTION_COLOR, ctypes.byref(caption), ctypes.sizeof(caption)
        )
        dwm.DwmSetWindowAttribute(
            wintypes.HWND(hwnd), DWMWA_TEXT_COLOR, ctypes.byref(text), ctypes.sizeof(text)
        )
    except Exception as exc:  # noqa: BLE001 — DWM unavailable / old Windows
        log.debug("caption color: DwmSetWindowAttribute failed (%s)", exc)


def run_app(host: str = "127.0.0.1", port: int = 8000) -> None:
    """Start the server and open the native app window."""
    import uvicorn

    from selects.launcher import _server_ready
    from selects.server.app import build_app
    from selects.server.library_manager import LibraryManager

    manager = LibraryManager()
    _libs, active_id = manager.list_libraries()
    if active_id is not None:
        try:
            manager.activate(active_id)
        except Exception:  # noqa: BLE001 — stale/removed active library is non-fatal
            pass
    app = build_app(manager=manager, run_background=True)

    url = f"http://{host}:{port}"

    def _serve() -> None:
        uvicorn.run(app, host=host, port=port, log_level="warning")

    threading.Thread(target=_serve, daemon=True).start()

    if not _server_ready(url):
        print(f"[selects] server failed to start on {url}")
        return

    try:
        import webview

        # A fully frameless custom title bar crashes in the PyInstaller bundle
        # (a pywebview/WinForms layout recursion, not a bundling gap — Color/
        # Point/Size resolve fine, so it isn't a missing System.Drawing). Rather
        # than ship an unreliable frameless window, keep the native window and
        # brand it via the Windows-11 DWM caption color. A fully custom title
        # bar would mean moving to a Tauri shell.
        window = webview.create_window(
            WINDOW_TITLE,
            url,
            width=1440,
            height=900,
            min_size=(1024, 720),
        )
        window.events.shown += lambda *_: _apply_windows_chrome(window)
        webview.start()  # blocks on the main thread until the window is closed
    except Exception as exc:  # noqa: BLE001
        log.warning("native window unavailable (%s); using browser fallback", exc)
        print(f"[selects] native window unavailable ({exc}); opening in browser")
        from selects.launcher import open_ui

        open_ui(url)
        # Keep the process (and the daemon server thread) alive.
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            pass
