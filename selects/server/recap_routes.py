"""Trip-recap HTTP endpoints: generate + download a self-contained keepsake page.

Registered the same way as :mod:`selects.server.models_routes` — a
``register_recap_routes(app, cfg)`` call the orchestrator (``build_app``) is
expected to wire in (see module docstring / wiring notes). ``cfg`` may be the
``ActiveConfigProxy`` so this follows whichever library is active.

Endpoints:
    POST /api/recap/{story_id}           generate the recap HTML, return its path
    GET  /api/recap/{story_id}/download   stream the previously generated file
"""
from __future__ import annotations

from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from selects.config import FolderConfig
from selects.recap import RecapError, generate_recap, recap_output_path

class RecapResponse(BaseModel):
    story_id: int
    path: str
    download_url: str


def register_recap_routes(app: FastAPI, cfg: FolderConfig) -> None:
    # Router is created per-call: a module-level router would accumulate
    # duplicate routes (closing over the first cfg) when build_app is
    # called more than once, e.g. across tests.
    router = APIRouter()

    @router.post("/api/recap/{story_id}", response_model=RecapResponse)
    def create_recap(story_id: int):
        try:
            out_path = generate_recap(cfg, story_id)
        except RecapError as exc:
            raise HTTPException(404, detail=str(exc))
        return RecapResponse(
            story_id=story_id,
            path=str(out_path),
            download_url=f"/api/recap/{story_id}/download",
        )

    @router.get("/api/recap/{story_id}/download")
    def download_recap(story_id: int):
        out_path = recap_output_path(cfg, story_id)
        if not out_path.exists():
            raise HTTPException(404, detail="recap not generated yet — POST /api/recap/{story_id} first")
        return FileResponse(out_path, media_type="text/html", filename=out_path.name)

    app.include_router(router)
