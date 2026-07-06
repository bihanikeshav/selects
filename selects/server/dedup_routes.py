"""Cross-library duplicate report HTTP endpoints.

Mirrors the registration style of :func:`register_model_routes`: a single
``register_dedup_routes(app, manager)`` call wires ``/api/dedup/*`` onto the
already-built FastAPI app. Not wired into ``server/app.py`` yet — see
wiring_needed in the feature's final report.

The scan walks every library in the registry (not just the active one), so it
can take a while on large registries; it runs in a background thread and
``GET /api/dedup/report`` doubles as the status-polling endpoint — the first
call starts a scan (if none is in flight and no previous result is cached),
and every call (including that first one) returns the current
``{running, result, error}`` state. Callers poll it until ``running`` is
``false``.
"""
from __future__ import annotations

import threading
from typing import Optional

from fastapi import FastAPI, HTTPException

from selects.dedup import scan_all_libraries

from .library_manager import LibraryManager


class DedupScanner:
    """Single in-flight duplicate scan, shared across requests."""

    def __init__(self, manager: LibraryManager) -> None:
        self._manager = manager
        self._lock = threading.Lock()
        self._running = False
        self._result: Optional[dict] = None
        self._error: Optional[str] = None

    def status(self) -> dict:
        with self._lock:
            return {
                "running": self._running,
                "result": self._result,
                "error": self._error,
            }

    def start(self) -> bool:
        """Kick off a scan in a background thread. Returns False (no-op) if
        one is already running."""
        with self._lock:
            if self._running:
                return False
            self._running = True
            self._error = None

        def worker() -> None:
            try:
                libraries, active_id = self._manager.list_libraries()
                report = scan_all_libraries(libraries, active_library_id=active_id)
                with self._lock:
                    self._result = report
            except Exception as e:  # noqa: BLE001 - surfaced via /report's "error"
                with self._lock:
                    self._error = str(e)
            finally:
                with self._lock:
                    self._running = False

        threading.Thread(target=worker, daemon=True).start()
        return True


def register_dedup_routes(app: FastAPI, manager: LibraryManager) -> None:
    scanner = DedupScanner(manager)

    @app.get("/api/dedup/report")
    def dedup_report():
        st = scanner.status()
        if not st["running"] and st["result"] is None and st["error"] is None:
            scanner.start()
            st = scanner.status()
        return st

    @app.post("/api/dedup/rescan")
    def dedup_rescan():
        if not scanner.start():
            raise HTTPException(409, detail="a duplicate scan is already in progress")
        return {"started": True}

    @app.get("/api/dedup/status")
    def dedup_status():
        return scanner.status()
