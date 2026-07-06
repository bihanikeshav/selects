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

        webview.create_window(
            WINDOW_TITLE, url, width=1440, height=900, min_size=(1024, 720)
        )
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
