from __future__ import annotations

from collections import defaultdict
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import select

from travelcull.config import FolderConfig
from travelcull.db import init_db, session_scope
from travelcull.db.models import ClassicalScore, Embedding, Photo, PhotoTag


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
    aesthetic_iqa: Optional[float] = None


class PhotoList(BaseModel):
    total: int
    items: list[PhotoOut]


class ClusterEntry(BaseModel):
    tag: str
    count: int
    cover_sha256: str
    cover_url: str
    sample_thumbs: list[str]


class ClusterList(BaseModel):
    total: int
    clusters: list[ClusterEntry]


def register_routes(app: FastAPI, cfg: FolderConfig) -> None:
    Session = init_db(cfg.db_path)

    @app.get("/api/photos", response_model=PhotoList)
    def list_photos(
        offset: int = Query(0, ge=0),
        limit: int = Query(200, le=2000),
        rejected: Optional[bool] = None,
        tag: Optional[str] = None,
    ):
        with session_scope(Session) as s:
            base = (
                select(Photo, ClassicalScore, Embedding)
                .join(ClassicalScore, Photo.id == ClassicalScore.photo_id, isouter=True)
                .join(Embedding, Photo.id == Embedding.photo_id, isouter=True)
            )
            if rejected is True:
                base = base.where(ClassicalScore.auto_reject.is_(True))
            elif rejected is False:
                base = base.where(
                    (ClassicalScore.auto_reject.is_(False))
                    | (ClassicalScore.photo_id.is_(None))
                )
            if tag is not None:
                base = base.join(PhotoTag, Photo.id == PhotoTag.photo_id).where(
                    PhotoTag.tag == tag
                )

            total = s.query(Photo).count()
            rows = s.execute(base.offset(offset).limit(limit)).all()

            items = []
            for photo, score, emb in rows:
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
                        aesthetic_iqa=emb.aesthetic_iqa if emb else None,
                    )
                )
        return PhotoList(total=total, items=items)

    @app.get("/api/clusters", response_model=ClusterList)
    def list_clusters(min_count: int = Query(2, ge=1)):
        with session_scope(Session) as s:
            # Each photo's PRIMARY tag = the highest-scoring PhotoTag row
            primary_rows = (
                s.query(PhotoTag.photo_id, PhotoTag.tag, PhotoTag.score)
                .order_by(PhotoTag.photo_id, PhotoTag.score.desc())
                .all()
            )

            # Take the first (highest-scoring) tag per photo
            primary_by_photo: dict[int, tuple[str, float]] = {}
            for pid, tag, score in primary_rows:
                if pid not in primary_by_photo:
                    primary_by_photo[pid] = (tag, score)

            # Group photo IDs by primary tag
            groups: dict[str, list[int]] = defaultdict(list)
            for pid, (tag, _) in primary_by_photo.items():
                groups[tag].append(pid)

            clusters_out: list[ClusterEntry] = []
            for tag, pids in groups.items():
                if len(pids) < min_count:
                    continue

                # Sort by aesthetic_iqa descending so the cover is the best photo
                rows = (
                    s.query(Photo.sha256, Embedding.aesthetic_iqa)
                    .join(Embedding, Embedding.photo_id == Photo.id, isouter=True)
                    .filter(Photo.id.in_(pids))
                    .all()
                )
                rows_sorted = sorted(rows, key=lambda r: (r[1] or 0.0), reverse=True)
                cover_sha = rows_sorted[0][0]
                samples = [f"/api/thumb/{r[0]}" for r in rows_sorted[:4]]
                clusters_out.append(ClusterEntry(
                    tag=tag,
                    count=len(pids),
                    cover_sha256=cover_sha,
                    cover_url=f"/api/thumb/{cover_sha}",
                    sample_thumbs=samples,
                ))

            clusters_out.sort(key=lambda c: c.count, reverse=True)

        return ClusterList(total=sum(c.count for c in clusters_out), clusters=clusters_out)

    @app.get("/api/clusters/{tag}/photos", response_model=PhotoList)
    def list_cluster_photos(tag: str, limit: int = Query(200, le=2000)):
        with session_scope(Session) as s:
            ids = [r[0] for r in s.query(PhotoTag.photo_id).filter(PhotoTag.tag == tag).all()]
            if not ids:
                return PhotoList(total=0, items=[])

            rows = s.execute(
                select(Photo, ClassicalScore, Embedding)
                .join(ClassicalScore, Photo.id == ClassicalScore.photo_id, isouter=True)
                .join(Embedding, Photo.id == Embedding.photo_id, isouter=True)
                .where(Photo.id.in_(ids))
                .order_by(Embedding.aesthetic_iqa.desc())
                .limit(limit)
            ).all()

            items = []
            for photo, score, emb in rows:
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
                        aesthetic_iqa=emb.aesthetic_iqa if emb else None,
                    )
                )
        return PhotoList(total=len(items), items=items)

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
