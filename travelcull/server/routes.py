from __future__ import annotations

from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import select

from travelcull.config import FolderConfig
from travelcull.db import init_db, session_scope
from travelcull.db.models import ClassicalScore, Photo


class PhotoOut(BaseModel):
    id: int
    sha256: str
    path: str
    format: Optional[str]
    width: Optional[int]
    height: Optional[int]
    taken_at: Optional[str]
    thumb_url: str
    preview_url: str
    blur: Optional[float] = None
    exposure: Optional[float] = None
    faces_count: Optional[int] = None
    auto_reject: Optional[bool] = None
    reject_reason: Optional[str] = None


class PhotoList(BaseModel):
    total: int
    items: list[PhotoOut]


def register_routes(app: FastAPI, cfg: FolderConfig) -> None:
    Session = init_db(cfg.db_path)

    @app.get("/api/photos", response_model=PhotoList)
    def list_photos(
        offset: int = Query(0, ge=0),
        limit: int = Query(200, le=2000),
        rejected: Optional[bool] = None,
    ):
        with session_scope(Session) as s:
            base = select(Photo, ClassicalScore).join(
                ClassicalScore, Photo.id == ClassicalScore.photo_id, isouter=True
            )
            if rejected is True:
                base = base.where(ClassicalScore.auto_reject.is_(True))
            elif rejected is False:
                base = base.where(
                    (ClassicalScore.auto_reject.is_(False))
                    | (ClassicalScore.photo_id.is_(None))
                )

            total = s.query(Photo).count()
            rows = s.execute(base.offset(offset).limit(limit)).all()

            items = []
            for photo, score in rows:
                items.append(
                    PhotoOut(
                        id=photo.id,
                        sha256=photo.sha256,
                        path=photo.path,
                        format=photo.format,
                        width=photo.width,
                        height=photo.height,
                        taken_at=photo.taken_at.isoformat() if photo.taken_at else None,
                        thumb_url=f"/api/thumb/{photo.sha256}",
                        preview_url=f"/api/preview/{photo.sha256}",
                        blur=score.blur if score else None,
                        exposure=score.exposure if score else None,
                        faces_count=score.faces_count if score else None,
                        auto_reject=score.auto_reject if score else None,
                        reject_reason=score.reject_reason if score else None,
                    )
                )
        return PhotoList(total=total, items=items)

    @app.get("/api/thumb/{sha256}")
    def thumb(sha256: str):
        return _serve_image_for(cfg, sha256, kind="thumb")

    @app.get("/api/preview/{sha256}")
    def preview(sha256: str):
        return _serve_image_for(cfg, sha256, kind="preview")


def _serve_image_for(cfg: FolderConfig, sha256: str, kind: str):
    parent = cfg.thumbs_dir if kind == "thumb" else cfg.previews_dir
    path = parent / f"{sha256}.jpg"
    if not path.exists():
        raise HTTPException(404, detail=f"{kind} not found")
    return FileResponse(path, media_type="image/jpeg")
