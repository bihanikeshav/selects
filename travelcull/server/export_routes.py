"""Export engine HTTP endpoints: get keepers out of the app.

Registered the same way as :mod:`travelcull.server.models_routes` — a
``register_export_routes(app, cfg)`` call that ``build_app`` is expected to
wire in (see module docstring / wiring notes). *cfg* may be the
``ActiveConfigProxy`` so these endpoints follow whichever library is active.

Endpoints:
    POST /api/export              copy/zip originals for a source set
    GET  /api/export/status/{id}  poll an in-flight export job
    GET  /api/export/xmp/preview  dry-run of the XMP rating write-back
    POST /api/export/xmp          actually write XMP ratings / sidecars
"""
from __future__ import annotations

import threading
import uuid
from pathlib import Path
from typing import Literal, Optional

from fastapi import APIRouter, Body, FastAPI, HTTPException, Query
from pydantic import BaseModel

from travelcull.config import FolderConfig
from travelcull.db import init_db, session_scope
from travelcull.db.models import Photo, Story, StoryItem, Swipe
from travelcull.export import ExportItem, export_photos, preview_xmp_writes, write_xmp_ratings

router = APIRouter()

# decision -> XMP verdict key used by travelcull.export.VERDICT_RATING
_DECISION_VERDICT: dict[str, str] = {
    "keep": "liked",
    "silver": "curated",
    "reject": "rejected",
}

_JOBS: dict[str, dict] = {}
_JOBS_LOCK = threading.Lock()


class ExportRequest(BaseModel):
    target: str
    mode: Literal["copy", "zip"] = "copy"
    source: str = "curated"  # "curated" | "liked" | "story:<id>"
    structure: Literal["flat", "by-day"] = "flat"


class XmpWriteRequest(BaseModel):
    source: str = "curated"
    force: bool = False


def register_export_routes(app: FastAPI, cfg: FolderConfig) -> None:
    def Session():
        return init_db(cfg.db_path)()

    def _resolve_source(s, source: str) -> list[ExportItem]:
        """Resolve a source spec to a list of ExportItem, in export order."""
        if source in ("curated", "liked"):
            decisions = ["keep", "silver"] if source == "curated" else ["keep"]
            rows = (
                s.query(Photo, Swipe.swiped_at)
                .join(Swipe, Swipe.photo_id == Photo.id)
                .filter(Swipe.decision.in_(decisions))
                .order_by(Photo.taken_at)
                .all()
            )
            return [
                ExportItem(
                    photo_id=photo.id,
                    path=Path(photo.path),
                    day=photo.taken_at.strftime("%Y-%m-%d") if photo.taken_at else None,
                )
                for photo, _ in rows
            ]

        if source.startswith("story:"):
            try:
                story_id = int(source.split(":", 1)[1])
            except ValueError:
                raise HTTPException(400, detail=f"invalid story id in source {source!r}")
            story = s.get(Story, story_id)
            if not story:
                raise HTTPException(404, detail="story not found")
            rows = (
                s.query(StoryItem, Photo)
                .join(Photo, StoryItem.photo_id == Photo.id)
                .filter(StoryItem.story_id == story_id)
                .order_by(StoryItem.rank)
                .all()
            )
            return [
                ExportItem(
                    photo_id=photo.id,
                    path=Path(photo.path),
                    day=photo.taken_at.strftime("%Y-%m-%d") if photo.taken_at else None,
                    rank=it.rank,
                )
                for it, photo in rows
            ]

        raise HTTPException(400, detail=f"unknown source {source!r}")

    def _resolve_ratable(s, source: str) -> list[tuple[int, Path, str]]:
        """Resolve a source spec to (photo_id, path, verdict) triples for XMP write-back.

        Unlike ``_resolve_source`` (export order), this always walks every
        swiped photo whose decision maps to a verdict, filtered by *source*.
        """
        if source in ("curated", "liked"):
            decisions = ["keep", "silver"] if source == "curated" else ["keep"]
        elif source.startswith("story:"):
            try:
                story_id = int(source.split(":", 1)[1])
            except ValueError:
                raise HTTPException(400, detail=f"invalid story id in source {source!r}")
            story = s.get(Story, story_id)
            if not story:
                raise HTTPException(404, detail="story not found")
            photo_ids = {
                pid for (pid,) in s.query(StoryItem.photo_id).filter(StoryItem.story_id == story_id).all()
            }
            rows = (
                s.query(Photo, Swipe.decision)
                .join(Swipe, Swipe.photo_id == Photo.id)
                .filter(Photo.id.in_(photo_ids))
                .all()
            )
            return [
                (photo.id, Path(photo.path), _DECISION_VERDICT[decision])
                for photo, decision in rows
                if decision in _DECISION_VERDICT
            ]
        else:
            raise HTTPException(400, detail=f"unknown source {source!r}")

        rows = (
            s.query(Photo, Swipe.decision)
            .join(Swipe, Swipe.photo_id == Photo.id)
            .filter(Swipe.decision.in_(decisions))
            .all()
        )
        return [
            (photo.id, Path(photo.path), _DECISION_VERDICT[decision])
            for photo, decision in rows
            if decision in _DECISION_VERDICT
        ]

    @router.post("/api/export")
    def start_export(req: ExportRequest):
        with session_scope(Session) as s:
            items = _resolve_source(s, req.source)

        if not items:
            raise HTTPException(404, detail="no photos matched that source")

        job_id = uuid.uuid4().hex[:12]
        with _JOBS_LOCK:
            _JOBS[job_id] = {"status": "running", "current": 0, "total": len(items), "result": None, "error": None}

        def progress(current: int, total: int) -> None:
            with _JOBS_LOCK:
                if job_id in _JOBS:
                    _JOBS[job_id]["current"] = current
                    _JOBS[job_id]["total"] = total

        def worker():
            try:
                result = export_photos(
                    items, req.target, mode=req.mode, structure=req.structure, progress=progress,
                )
                with _JOBS_LOCK:
                    _JOBS[job_id]["status"] = "done"
                    _JOBS[job_id]["result"] = {
                        "count": result.count,
                        "bytes": result.bytes,
                        "path": result.path,
                        "skipped": result.skipped,
                    }
            except Exception as exc:
                with _JOBS_LOCK:
                    _JOBS[job_id]["status"] = "error"
                    _JOBS[job_id]["error"] = str(exc)

        threading.Thread(target=worker, daemon=True).start()
        return {"job_id": job_id, "total": len(items)}

    @router.get("/api/export/status/{job_id}")
    def export_status(job_id: str):
        with _JOBS_LOCK:
            job = _JOBS.get(job_id)
            if job is None:
                raise HTTPException(404, detail="unknown export job")
            return dict(job)

    @router.get("/api/export/xmp/preview")
    def preview_xmp(
        source: str = Query("curated"),
        force: bool = Query(False),
    ):
        with session_scope(Session) as s:
            triples = _resolve_ratable(s, source)
        plans = preview_xmp_writes(triples, force=force)
        return {
            "total": len(plans),
            "to_write": sum(1 for p in plans if p.action == "write"),
            "skipped": sum(1 for p in plans if p.action != "write"),
            "plans": [
                {
                    "photo_id": p.photo_id,
                    "path": p.path,
                    "verdict": p.verdict,
                    "new_rating": p.new_rating,
                    "target": p.target,
                    "is_sidecar": p.is_sidecar,
                    "existing_rating": p.existing_rating,
                    "action": p.action,
                    "reason": p.reason,
                }
                for p in plans
            ],
        }

    @router.post("/api/export/xmp")
    def write_xmp(req: XmpWriteRequest):
        with session_scope(Session) as s:
            triples = _resolve_ratable(s, req.source)
        plans = write_xmp_ratings(triples, force=req.force)
        failed = sum(1 for p in plans if p.reason and p.reason.startswith("write failed"))
        written = sum(1 for p in plans if p.action == "write")
        return {
            "total": len(plans),
            "written": written,
            "skipped": len(plans) - written - failed,
            "failed": failed,
            "plans": [
                {
                    "photo_id": p.photo_id,
                    "path": p.path,
                    "verdict": p.verdict,
                    "new_rating": p.new_rating,
                    "target": p.target,
                    "is_sidecar": p.is_sidecar,
                    "action": p.action,
                    "reason": p.reason,
                }
                for p in plans
            ],
        }

    app.include_router(router)
