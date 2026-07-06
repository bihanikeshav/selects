"""Taste-personalization HTTP endpoints (train / status).

Mirrors the registration style of :func:`register_model_routes`: a single
``register_taste_routes(app, cfg)`` call wires ``/api/taste/*`` onto the
already-built FastAPI app. *cfg* is the same ``ActiveConfigProxy`` handed to
``register_routes`` so the endpoints transparently follow the active library.

Not wired into ``server/app.py`` — see wiring_needed in the feature report.
"""
from __future__ import annotations

import threading

from fastapi import FastAPI, HTTPException

from travelcull.config import FolderConfig
from travelcull.ml import taste


def register_taste_routes(app: FastAPI, cfg: FolderConfig) -> None:
    """Register /api/taste/* routes.

    *cfg* may be an ``ActiveConfigProxy``; every request re-reads it so
    switching the active library at runtime is picked up.
    """
    # Serialize training runs: it is fast (numpy logreg on a few thousand
    # vectors), but two concurrent trains writing taste.npz would race.
    train_lock = threading.Lock()

    @app.post("/api/taste/train")
    def taste_train():
        if not train_lock.acquire(blocking=False):
            raise HTTPException(409, detail="a taste training run is already in progress")
        try:
            result = taste.train_taste_model(cfg)
        except taste.TasteTrainingError as exc:
            raise HTTPException(400, detail=str(exc))
        finally:
            train_lock.release()
        return result

    @app.get("/api/taste/status")
    def taste_status():
        return taste.taste_status(cfg)
