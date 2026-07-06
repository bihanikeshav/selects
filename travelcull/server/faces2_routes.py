"""Face-quality endpoint: per-face eyes-open / head-pose attributes + rollups.

``GET /api/photos/{sha256}/face_quality`` returns the stored per-face
attributes (computed at face-embedding time, or lazily backfilled here for
rows that predate the attribute columns) plus photo-level rollups:

    {
      "faces": [{"eyes_open": 0.8, "yaw": 4.1, "pitch": -2.0, "area": 0.06}],
      "any_eyes_closed": false,
      "all_looking_away": false
    }

Kept in its own file/router per the project's conflict rules; NOT wired into
``app.py`` by this feature — see wiring_needed in the task report. Mirrors the
register pattern of ``travelcull.server.search2_routes``.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, FastAPI, HTTPException, Query
from pydantic import BaseModel

from travelcull.config import FolderConfig
from travelcull.db import init_db, session_scope
from travelcull.db.models import FaceEmbedding, Photo
from travelcull.ml.face_attributes import (
    FaceAttrs,
    backfill_photo_attributes,
    rollup_face_quality,
)

log = logging.getLogger(__name__)


class FaceQualityFace(BaseModel):
    eyes_open: Optional[float] = None
    yaw: Optional[float] = None
    pitch: Optional[float] = None
    area: Optional[float] = None


class FaceQualityOut(BaseModel):
    faces: list[FaceQualityFace]
    any_eyes_closed: bool
    all_looking_away: bool


def build_router(cfg: FolderConfig) -> APIRouter:
    router = APIRouter()

    def Session():
        return init_db(cfg.db_path)()

    @router.get("/api/photos/{sha256}/face_quality", response_model=FaceQualityOut)
    def face_quality(
        sha256: str,
        compute: bool = Query(
            True,
            description=(
                "Lazily compute attributes for face rows that predate the "
                "attribute columns (requires the face detector; failures are "
                "non-fatal and yield null attributes)"
            ),
        ),
    ):
        with session_scope(Session) as s:
            photo = s.query(Photo).filter(Photo.sha256 == sha256).one_or_none()
            if photo is None:
                raise HTTPException(404, detail="photo not found")
            photo_id, preview_path = photo.id, photo.preview_path

            rows = (
                s.query(FaceEmbedding)
                .filter(FaceEmbedding.photo_id == photo_id)
                .order_by(FaceEmbedding.face_index)
                .all()
            )
            needs_backfill = any(r.eyes_open is None for r in rows)

        if rows and needs_backfill and compute and preview_path:
            try:
                backfill_photo_attributes(cfg, Session, photo_id, preview_path)
            except Exception as exc:
                # Detector unavailable / decode failure — serve what we have.
                log.warning("face_quality: lazy backfill failed for %s: %s", sha256, exc)
            with session_scope(Session) as s:
                rows = (
                    s.query(FaceEmbedding)
                    .filter(FaceEmbedding.photo_id == photo_id)
                    .order_by(FaceEmbedding.face_index)
                    .all()
                )

        attrs = [
            FaceAttrs(
                eyes_open=r.eyes_open,
                yaw=r.yaw,
                pitch=r.pitch,
                area_ratio=r.face_area_ratio,
            )
            for r in rows
        ]
        rollup = rollup_face_quality(attrs)
        return FaceQualityOut(
            faces=[
                FaceQualityFace(
                    eyes_open=a.eyes_open, yaw=a.yaw, pitch=a.pitch, area=a.area_ratio
                )
                for a in attrs
            ],
            any_eyes_closed=rollup["any_eyes_closed"],
            all_looking_away=rollup["all_looking_away"],
        )

    return router


def register_faces2_routes(app: FastAPI, cfg: FolderConfig) -> None:
    """Register ``/api/photos/{sha256}/face_quality`` on *app*.

    Mirrors :func:`travelcull.server.routes.register_routes`'s pattern of
    resolving the sessionmaker at request time via *cfg* (which may be an
    ``ActiveConfigProxy``). NOT called from ``app.py`` by this feature — see
    wiring_needed.
    """
    app.include_router(build_router(cfg))
