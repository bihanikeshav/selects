"""Model-weights HTTP endpoints (status / download).

Mirrors the registration style of :func:`register_libraries`: the orchestrator
calls ``register_model_routes(app, publish)`` with the same thread-safe
``publish`` callable used elsewhere (the closure in ``build_app`` that forwards
dicts onto the websocket progress bus via
``asyncio.run_coroutine_threadsafe``).
"""
from __future__ import annotations

import threading
from typing import Callable

from fastapi import FastAPI, HTTPException

from selects.ml import model_assets


def register_model_routes(
    app: FastAPI,
    publish: Callable[[dict], None],
) -> None:
    """Register /api/models/* routes.

    *publish* is a callable taking a progress dict (same signature as the one
    passed to :func:`register_libraries`); it is responsible for getting the
    dict onto the ``/ws/progress`` bus in a thread-safe way.
    """
    # Single in-flight download guard, shared across requests.
    state = {"downloading": False}
    lock = threading.Lock()

    @app.get("/api/models/status")
    def models_status():
        st = model_assets.status()
        st["downloading"] = state["downloading"]
        return st

    @app.post("/api/models/download")
    def models_download():
        with lock:
            if state["downloading"]:
                raise HTTPException(409, detail="a model download is already in progress")
            state["downloading"] = True

        def worker():
            try:
                total = model_assets.download_all(publish)
                publish(
                    {
                        "stage": "models",
                        "current": total,
                        "total": total,
                        "message": "done",
                    }
                )
            finally:
                with lock:
                    state["downloading"] = False

        threading.Thread(target=worker, daemon=True).start()
        return {"started": True}
