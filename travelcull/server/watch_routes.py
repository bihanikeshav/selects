"""Watch-folder HTTP endpoints (status / enable / disable / interval).

Mirrors the registration style of ``register_libraries`` / ``register_model_routes``:
the orchestrator calls ``register_watch_routes(app, manager, publish)`` with the
same thread-safe ``publish`` callable used elsewhere (forwards progress/event
dicts onto the ``/ws/progress`` bus).

GET  /api/watch  -> {enabled, interval, last_run, new_files_found, running}
POST /api/watch  -> body {enabled?: bool, interval?: int} -> same shape

Operates on whichever library is currently active in *manager*.
"""
from __future__ import annotations

from typing import Callable

from fastapi import Body, FastAPI, HTTPException

from travelcull.server.library_manager import LibraryManager
from travelcull.watcher import get_or_create_watcher


def register_watch_routes(
    app: FastAPI,
    manager: LibraryManager,
    publish: Callable[[dict], None],
) -> None:
    def _active_cfg():
        cfg = manager.active_cfg
        if cfg is None:
            raise HTTPException(400, detail="no active library configured")
        return cfg

    @app.get("/api/watch")
    def watch_status():
        watcher = get_or_create_watcher(_active_cfg(), publish=publish)
        return watcher.status()

    @app.post("/api/watch")
    def watch_update(payload: dict = Body(default={})):
        cfg = _active_cfg()
        watcher = get_or_create_watcher(cfg, publish=publish)

        interval = payload.get("interval")
        if interval is not None:
            try:
                interval = int(interval)
            except (TypeError, ValueError):
                raise HTTPException(400, detail="interval must be an integer (seconds)")
            if interval < 5:
                raise HTTPException(400, detail="interval must be >= 5 seconds")
            watcher.interval = interval

        enabled = payload.get("enabled")
        if enabled is True:
            watcher.start()
        elif enabled is False:
            watcher.stop()

        return watcher.status()
