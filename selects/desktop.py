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


class _NativeChrome:
    """JS-exposed API so the web UI can retint the native title bar when the
    app theme toggles — the frontend calls ``window.pywebview.api.set_theme``.
    """

    def __init__(self) -> None:
        # NOTE: underscore-prefixed on purpose. pywebview's JS-API bridge walks
        # dir(js_api) and recurses into every *public* non-callable attribute to
        # enumerate methods. A public window ref would make it descend into the
        # live .NET window (window.native.AccessibilityObject.Bounds.Empty…) and
        # spew "Error while processing" recursion. The leading _ makes it skip.
        self._window = None

    def set_theme(self, is_dark: bool) -> None:
        if self._window is not None:
            _apply_windows_chrome(self._window, dark=bool(is_dark))


def _apply_windows_chrome(window, dark: bool = False) -> None:
    """Tint the native Windows-11 title bar to match the app background for the
    current theme (light/dark), with readable caption text.

    Uses the DWM caption-color attributes (Windows 11 build 22000+). Keeps the
    reliable native window (a frameless custom bar crashes in the PyInstaller
    bundle via a pywebview/WinForms layout recursion) while blending the caption
    into the app so it doesn't read as a jarring coloured band. No-op on older
    Windows / non-Windows — DwmSetWindowAttribute ignores unknown attributes.
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
    # COLORREF is 0x00BBGGRR — matched to the app's surface for each theme.
    if dark:
        caption = wintypes.DWORD(0x00201C1C)  # ~#1C1C20 (dark surface)
        text = wintypes.DWORD(0x00E7E4E4)     # ~#E4E4E7
    else:
        caption = wintypes.DWORD(0x00FAF7F7)  # ~#F7F7FA (light surface)
        text = wintypes.DWORD(0x00242420)     # ~#202124
    try:
        dwm = ctypes.windll.dwmapi
        for attr, val in ((DWMWA_CAPTION_COLOR, caption), (DWMWA_TEXT_COLOR, text)):
            dwm.DwmSetWindowAttribute(
                wintypes.HWND(hwnd), attr, ctypes.byref(val), ctypes.sizeof(val)
            )
    except Exception as exc:  # noqa: BLE001 — DWM unavailable / old Windows
        log.debug("caption color: DwmSetWindowAttribute failed (%s)", exc)


def run_app(host: str = "127.0.0.1", port: int = 8000) -> None:
    """Start the server and open the native app window."""
    import uvicorn

    from selects.logging_setup import setup_logging

    log_path = setup_logging()
    log.info("selects desktop starting; logs at %s", log_path)

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
        chrome = _NativeChrome()
        window = webview.create_window(
            WINDOW_TITLE,
            url,
            width=1440,
            height=900,
            min_size=(1024, 720),
            maximized=True,     # open filling the screen
            js_api=chrome,
        )

        def _close_splash(*_):
            # Dismiss the PyInstaller boot splash once our window is visible.
            try:
                import pyi_splash  # type: ignore

                pyi_splash.close()
            except Exception:
                pass

        window.events.shown += _close_splash
        chrome._window = window
        # Start matched to the light theme; the web UI calls set_theme() on load
        # and on every toggle to keep the caption in sync with the app theme.
        window.events.shown += lambda *_: _apply_windows_chrome(window, dark=False)
        # private_mode=False + a persistent storage_path so the WebView keeps
        # localStorage across launches (theme choice, UI prefs). The default
        # (private_mode=True) uses an ephemeral profile that is wiped on exit, so
        # the app always reopened in light mode.
        from pathlib import Path

        storage = Path.home() / ".selects" / "webview"
        storage.mkdir(parents=True, exist_ok=True)
        webview.start(private_mode=False, storage_path=str(storage))
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
