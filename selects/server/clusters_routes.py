"""Tag-derived clusters (Collections) and the raw tag listing."""
from __future__ import annotations

import logging
from collections import defaultdict
from typing import Optional

from fastapi import FastAPI, Query
from sqlalchemy import select

from selects.config import FolderConfig
from selects.db import init_db, session_scope
from selects.db.models import AestheticScore, ClassicalScore, Embedding, Photo, PhotoTag
from selects.server.schemas import (
    ClusterEntry, ClusterList, PhotoList, PhotoOut, TagEntry, TagList,
)

log = logging.getLogger(__name__)


def register_clusters_routes(app: FastAPI, cfg: FolderConfig) -> None:
    def Session():
        return init_db(cfg.db_path)()

    @app.get("/api/clusters", response_model=ClusterList)
    def list_clusters(
        min_count: int = Query(2, ge=1),
        source: Optional[str] = Query(
            "thematic",
            description="Tag source: 'thematic' (location-driven, default), 'lookback', 'posting', "
                        "or empty for legacy zero-shot tags",
        ),
    ):
        """Return clusters grouped by tag for the given source.

        source=lookback  → broad global themes (default, ~10-20 clusters)
        source=posting   → tight session-block groups (more granular, for carousels)
        source=          → legacy zero-shot SigLIP tags (fallback)
        """
        with session_scope(Session) as s:
            # Filter by source if specified
            q = s.query(PhotoTag.photo_id, PhotoTag.tag, PhotoTag.score)
            if source:
                q = q.filter(PhotoTag.source == source)
            else:
                # Legacy: NULL source
                q = q.filter(PhotoTag.source.is_(None))

            primary_rows = q.order_by(PhotoTag.photo_id, PhotoTag.score.desc()).all()

            # One tag per photo (first = highest score, or just first for equal scores)
            primary_by_photo: dict[int, tuple[str, float]] = {}
            for pid, tag, score in primary_rows:
                if pid not in primary_by_photo:
                    primary_by_photo[pid] = (tag, score)

            # Find ALL photo IDs so we can surface uncategorized photos
            all_photo_ids: set[int] = {r[0] for r in s.query(Photo.id).all()}
            tagged_photo_ids: set[int] = set(primary_by_photo.keys())
            untagged_ids = sorted(all_photo_ids - tagged_photo_ids)

            # Group photo IDs by tag
            groups: dict[str, list[int]] = defaultdict(list)
            for pid, (tag, _) in primary_by_photo.items():
                groups[tag].append(pid)

            # Add synthetic "uncategorized" cluster for photos with no tags
            if untagged_ids:
                groups["uncategorized"] = list(untagged_ids)

            all_cover_ids = [pid for pids in groups.values() for pid in pids]
            score_by_id: dict[int, tuple[str, float, float]] = {}
            if all_cover_ids:
                for pid, sha, iqa, ap25 in (
                    s.query(
                        Photo.id,
                        Photo.sha256,
                        Embedding.aesthetic_iqa,
                        AestheticScore.ap25_score,
                    )
                    .outerjoin(Embedding, Embedding.photo_id == Photo.id)
                    .outerjoin(AestheticScore, AestheticScore.photo_id == Photo.id)
                    .filter(Photo.id.in_(all_cover_ids))
                    .all()
                ):
                    score_by_id[pid] = (sha, ap25 if ap25 is not None else -1.0, iqa if iqa is not None else -1.0)

            clusters_out: list[ClusterEntry] = []
            for tag, pids in groups.items():
                if len(pids) < min_count:
                    continue
                ranked = sorted(
                    pids,
                    key=lambda pid: score_by_id.get(pid, ("", -1.0, -1.0))[1:],
                    reverse=True,
                )
                cover_sha = score_by_id.get(ranked[0], ("",))[0] if ranked else ""
                samples = [
                    f"/api/thumb/{score_by_id[pid][0]}"
                    for pid in ranked[:4]
                    if pid in score_by_id and score_by_id[pid][0]
                ]
                if not cover_sha:
                    continue
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
    def list_cluster_photos(
        tag: str,
        limit: int = Query(500, le=2000),
        source: Optional[str] = Query("thematic"),
    ):
        with session_scope(Session) as s:
            # Synthetic "uncategorized" cluster: photos with NO tag in this source.
            # Empty/missing source matches list_clusters (legacy NULL-source tags).
            if tag.lower() == "uncategorized":
                tagged_subq = s.query(PhotoTag.photo_id)
                if source:
                    tagged_subq = tagged_subq.filter(PhotoTag.source == source)
                else:
                    tagged_subq = tagged_subq.filter(PhotoTag.source.is_(None))
                tagged_ids = {r[0] for r in tagged_subq.all()}
                all_ids = {r[0] for r in s.query(Photo.id).all()}
                ids = list(all_ids - tagged_ids)
            else:
                q = s.query(PhotoTag.photo_id).filter(PhotoTag.tag == tag)
                if source:
                    q = q.filter(PhotoTag.source == source)
                else:
                    q = q.filter(PhotoTag.source.is_(None))
                ids = [r[0] for r in q.all()]
            if not ids:
                return PhotoList(total=0, items=[])

            rows = s.execute(
                select(Photo, ClassicalScore, Embedding)
                .join(ClassicalScore, Photo.id == ClassicalScore.photo_id, isouter=True)
                .join(Embedding, Photo.id == Embedding.photo_id, isouter=True)
                .outerjoin(AestheticScore, AestheticScore.photo_id == Photo.id)
                .where(Photo.id.in_(ids))
                .order_by(
                    AestheticScore.ap25_score.desc().nulls_last(),
                    Embedding.aesthetic_iqa.desc(),
                )
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

    @app.get("/api/tags", response_model=TagList)
    def list_tags():
        """Return all distinct tags with counts, sorted by count descending."""
        from sqlalchemy import text
        with session_scope(Session) as s:
            result = s.execute(
                text("SELECT tag, COUNT(*) as n FROM photo_tags GROUP BY tag ORDER BY n DESC")
            ).fetchall()
            tags = [TagEntry(tag=row[0], count=row[1]) for row in result]
        return TagList(tags=tags)
