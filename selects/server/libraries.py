"""Library-registry HTTP endpoints (add / list / activate / delete / index)."""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Callable

from fastapi import Body, FastAPI, HTTPException

from selects.config import get_folder_config

from .library_manager import (
    ActiveLibraryError,
    DuplicateLibraryError,
    LibraryManager,
)
from .pipeline_runner import run_pipeline_stages


def register_libraries(
    app: FastAPI,
    manager: LibraryManager,
    publish: Callable[[dict], None],
) -> None:
    @app.get("/api/libraries")
    def list_libraries():
        libraries, active_id = manager.list_libraries()
        return {"libraries": libraries, "active_id": active_id}

    @app.post("/api/libraries")
    def add_library(payload: dict = Body(...)):
        name = (payload.get("name") or "").strip()
        path = (payload.get("path") or "").strip()
        if not name or not path:
            raise HTTPException(400, detail="name and path are required")
        p = Path(path).expanduser()
        if not p.exists() or not p.is_dir():
            raise HTTPException(400, detail="path does not exist or is not a directory")
        try:
            lib = manager.add_library(name, path)
        except DuplicateLibraryError:
            # Already registered: open it instead of dead-ending. Activate the
            # existing library and return it so the UI can proceed straight in.
            existing = manager.find_by_path(path)
            if existing is not None:
                manager.activate(existing["id"])
                return {"library": existing, "already_registered": True}
            raise HTTPException(409, detail="path is already registered")
        return {"library": lib}

    @app.post("/api/libraries/{lib_id}/activate")
    def activate_library(lib_id: str):
        try:
            lib = manager.activate(lib_id)
        except KeyError:
            raise HTTPException(404, detail="unknown library")
        return {"ok": True, "library": lib}

    @app.delete("/api/libraries/{lib_id}")
    def delete_library(lib_id: str):
        try:
            manager.delete(lib_id)
        except KeyError:
            raise HTTPException(404, detail="unknown library")
        except ActiveLibraryError:
            raise HTTPException(
                400,
                detail="cannot delete the active library while others exist; activate another first",
            )
        return {"ok": True}

    @app.post("/api/libraries/{lib_id}/index")
    def index_library(lib_id: str):
        lib = manager.get(lib_id)
        if lib is None:
            raise HTTPException(404, detail="unknown library")
        if not manager.begin_indexing():
            raise HTTPException(409, detail="an indexing run is already in progress")

        cfg = get_folder_config(lib["path"])

        def worker():
            try:
                run_pipeline_stages(cfg, publish)
            finally:
                manager.end_indexing()

        threading.Thread(target=worker, daemon=True).start()
        return {"started": True}

    @app.get("/api/libraries/status")
    def libraries_status():
        return manager.status()
