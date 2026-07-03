from __future__ import annotations

from collections import defaultdict
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import select

from travelcull.config import FolderConfig
from travelcull.db import init_db, session_scope
from travelcull.db.models import (
    ClassicalScore, Embedding, Moment, MomentMember, Photo, PhotoTag, Story, StoryItem, Visit,
)


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
    moment_id: Optional[int] = None
    moment_size: Optional[int] = None


class MomentMemberOut(BaseModel):
    photo_id: int
    sha256: str
    rank: int
    thumb_url: str
    preview_url: str
    taken_at: Optional[str]


class MomentOut(BaseModel):
    id: int
    primary_photo_id: int
    primary_sha256: str
    started_at: str
    ended_at: str
    size: int
    members: list[MomentMemberOut]


class MomentList(BaseModel):
    total: int
    moments: list[MomentOut]


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


class TagEntry(BaseModel):
    tag: str
    count: int


class TagList(BaseModel):
    tags: list[TagEntry]


class StoryItemOut(BaseModel):
    rank: int
    photo_id: int
    sha256: str
    thumb_url: str
    preview_url: str
    scene_label: Optional[str]
    taken_at: Optional[str]
    tag: Optional[str] = None


class VisitOut(BaseModel):
    rank: int
    name: str
    summary: Optional[str]
    lat: float
    lon: float
    elevation_m: Optional[int]
    arrived_at: str
    departed_at: str
    photo_count: int
    cover_thumb_url: Optional[str] = None


class StoryOut(BaseModel):
    id: int
    day: str
    title: str
    photo_count: int
    items: list[StoryItemOut]
    visits: list[VisitOut]
    cover_url: str
    itinerary_breadcrumb: str


class StoryList(BaseModel):
    total: int
    stories: list[StoryOut]


def _build_visit_out(v: Visit, cover_sha: Optional[str]) -> VisitOut:
    return VisitOut(
        rank=v.rank,
        name=v.name,
        summary=v.summary,
        lat=v.lat,
        lon=v.lon,
        elevation_m=v.elevation_m,
        arrived_at=v.arrived_at.isoformat(),
        departed_at=v.departed_at.isoformat(),
        photo_count=v.photo_count,
        cover_thumb_url=f"/api/thumb/{cover_sha}" if cover_sha else None,
    )


def _build_breadcrumb(visits: list[VisitOut]) -> str:
    """Build one-line breadcrumb string from visits, e.g. 'Leh > Khardung La > Nubra'."""
    if not visits:
        return ""
    parts = []
    for v in visits:
        label = v.name
        if v.elevation_m:
            label = f"{label} ({v.elevation_m:,}m)"
        parts.append(label)
    return " › ".join(parts)  # › separator


def _story_to_out(
    st: Story,
    items_rows: list,
    visits_rows: list[Visit],
    cover_sha_by_photo_id: dict[int, str],
    primary_tag_by_photo: dict[int, str],
) -> StoryOut:
    items = [
        StoryItemOut(
            rank=it.rank,
            photo_id=it.photo_id,
            sha256=p.sha256,
            thumb_url=f"/api/thumb/{p.sha256}",
            preview_url=f"/api/preview/{p.sha256}",
            scene_label=it.scene_label,
            taken_at=p.taken_at.isoformat() if p.taken_at else None,
            tag=primary_tag_by_photo.get(it.photo_id),
        )
        for it, p in items_rows
    ]
    cover_url = items[0].thumb_url if items else "/api/thumb/missing"

    visits_out = [
        _build_visit_out(v, cover_sha_by_photo_id.get(v.cover_photo_id or -1))
        for v in visits_rows
    ]
    breadcrumb = _build_breadcrumb(visits_out)

    return StoryOut(
        id=st.id,
        day=st.day,
        title=st.title,
        photo_count=st.photo_count,
        items=items,
        visits=visits_out,
        cover_url=cover_url,
        itinerary_breadcrumb=breadcrumb,
    )


def register_routes(app: FastAPI, cfg: FolderConfig) -> None:
    Session = init_db(cfg.db_path)

    @app.get("/api/photos", response_model=PhotoList)
    def list_photos(
        offset: int = Query(0, ge=0),
        limit: int = Query(200, le=2000),
        rejected: Optional[bool] = None,
        tag: Optional[str] = None,
        collapse: str = Query("moments", description="'moments' collapses to primaries only; 'none' returns all"),
    ):
        with session_scope(Session) as s:
            # Build moment membership map for collapse support
            moment_of: dict[int, tuple[int, int, bool]] = {}
            if collapse == "moments":
                for mm_pid, mm_mid, mom_size, mom_primary in (
                    s.query(
                        MomentMember.photo_id,
                        MomentMember.moment_id,
                        Moment.size,
                        Moment.primary_photo_id,
                    )
                    .join(Moment, Moment.id == MomentMember.moment_id)
                    .all()
                ):
                    is_primary = (mm_pid == mom_primary)
                    moment_of[mm_pid] = (mm_mid, mom_size, is_primary)

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
                # Collapse: skip non-primary moment members
                if collapse == "moments" and photo.id in moment_of:
                    _, _, is_primary = moment_of[photo.id]
                    if not is_primary:
                        continue

                moment_id: Optional[int] = None
                moment_size: Optional[int] = None
                if photo.id in moment_of:
                    moment_id, moment_size, _ = moment_of[photo.id]

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
                        moment_id=moment_id,
                        moment_size=moment_size,
                    )
                )
        return PhotoList(total=total, items=items)

    @app.get("/api/moments", response_model=MomentList)
    def list_moments():
        """Return all moments with their members."""
        with session_scope(Session) as s:
            moments = s.query(Moment).order_by(Moment.started_at).all()
            result = []
            for mom in moments:
                primary_photo = s.get(Photo, mom.primary_photo_id)
                member_rows = (
                    s.query(MomentMember, Photo)
                    .join(Photo, Photo.id == MomentMember.photo_id)
                    .filter(MomentMember.moment_id == mom.id)
                    .order_by(MomentMember.rank)
                    .all()
                )
                members = [
                    MomentMemberOut(
                        photo_id=p.id,
                        sha256=p.sha256,
                        rank=mm.rank,
                        thumb_url=f"/api/thumb/{p.sha256}",
                        preview_url=f"/api/preview/{p.sha256}",
                        taken_at=p.taken_at.isoformat() if p.taken_at else None,
                    )
                    for mm, p in member_rows
                ]
                result.append(
                    MomentOut(
                        id=mom.id,
                        primary_photo_id=mom.primary_photo_id,
                        primary_sha256=primary_photo.sha256 if primary_photo else "",
                        started_at=mom.started_at.isoformat(),
                        ended_at=mom.ended_at.isoformat(),
                        size=mom.size,
                        members=members,
                    )
                )
        return MomentList(total=len(result), moments=result)

    @app.get("/api/photos/{sha256}/moment", response_model=Optional[MomentOut])
    def get_photo_moment(sha256: str):
        """Return the moment a photo belongs to, or null if it's not in a moment."""
        with session_scope(Session) as s:
            photo = s.query(Photo).filter(Photo.sha256 == sha256).first()
            if photo is None:
                raise HTTPException(404, detail="Photo not found")

            mm = s.query(MomentMember).filter(MomentMember.photo_id == photo.id).first()
            if mm is None:
                return None

            mom = s.get(Moment, mm.moment_id)
            if mom is None:
                return None

            primary_photo = s.get(Photo, mom.primary_photo_id)
            member_rows = (
                s.query(MomentMember, Photo)
                .join(Photo, Photo.id == MomentMember.photo_id)
                .filter(MomentMember.moment_id == mom.id)
                .order_by(MomentMember.rank)
                .all()
            )
            members = [
                MomentMemberOut(
                    photo_id=p.id,
                    sha256=p.sha256,
                    rank=m.rank,
                    thumb_url=f"/api/thumb/{p.sha256}",
                    preview_url=f"/api/preview/{p.sha256}",
                    taken_at=p.taken_at.isoformat() if p.taken_at else None,
                )
                for m, p in member_rows
            ]
            return MomentOut(
                id=mom.id,
                primary_photo_id=mom.primary_photo_id,
                primary_sha256=primary_photo.sha256 if primary_photo else "",
                started_at=mom.started_at.isoformat(),
                ended_at=mom.ended_at.isoformat(),
                size=mom.size,
                members=members,
            )

    @app.get("/api/clusters", response_model=ClusterList)
    def list_clusters(
        min_count: int = Query(2, ge=1),
        source: Optional[str] = Query(
            "lookback",
            description="Tag source to display: 'lookback' (broad themes), 'posting' (tight groups), "
                        "or None/'' for legacy zero-shot tags",
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

            clusters_out: list[ClusterEntry] = []
            for tag, pids in groups.items():
                if len(pids) < min_count:
                    continue

                # Sort by aesthetic_iqa descending so cover = best photo
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

    @app.get("/api/photos/{sha256}/tags")
    def get_photo_tags(sha256: str):
        """Return all tags for a photo across all sources."""
        with session_scope(Session) as s:
            photo = s.query(Photo).filter(Photo.sha256 == sha256).first()
            if photo is None:
                raise HTTPException(404, detail="Photo not found")

            rows = (
                s.query(PhotoTag.tag, PhotoTag.score, PhotoTag.source)
                .filter(PhotoTag.photo_id == photo.id)
                .order_by(PhotoTag.source, PhotoTag.score.desc())
                .all()
            )
            result: dict[str, list[dict]] = defaultdict(list)
            for tag, score, src in rows:
                result[src or "legacy"].append({"tag": tag, "score": score})
            return {"sha256": sha256, "tags_by_source": dict(result)}

    @app.get("/api/clusters/{tag}/photos", response_model=PhotoList)
    def list_cluster_photos(
        tag: str,
        limit: int = Query(200, le=2000),
        source: Optional[str] = Query("lookback"),
    ):
        with session_scope(Session) as s:
            q = s.query(PhotoTag.photo_id).filter(PhotoTag.tag == tag)
            if source:
                q = q.filter(PhotoTag.source == source)
            ids = [r[0] for r in q.all()]
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

    @app.get("/api/tags", response_model=TagList)
    def list_tags():
        """Return all distinct tags with counts, sorted by count descending."""
        from sqlalchemy import func, text
        with session_scope(Session) as s:
            result = s.execute(
                text("SELECT tag, COUNT(*) as n FROM photo_tags GROUP BY tag ORDER BY n DESC")
            ).fetchall()
            tags = [TagEntry(tag=row[0], count=row[1]) for row in result]
        return TagList(tags=tags)

    def _get_primary_tags_for_photos(s, photo_ids: list[int]) -> dict[int, str]:
        """Return {photo_id: primary_tag} for the given photo_ids."""
        if not photo_ids:
            return {}
        rows = (
            s.query(PhotoTag.photo_id, PhotoTag.tag, PhotoTag.score)
            .filter(PhotoTag.photo_id.in_(photo_ids))
            .order_by(PhotoTag.photo_id, PhotoTag.score.desc())
            .all()
        )
        result: dict[int, str] = {}
        for pid, tag, score in rows:
            if pid not in result:
                result[pid] = tag
        return result

    def _get_cover_sha_map(s, cover_photo_ids: list[int]) -> dict[int, str]:
        """Return {photo_id: sha256} for visit cover photos."""
        if not cover_photo_ids:
            return {}
        rows = s.query(Photo.id, Photo.sha256).filter(Photo.id.in_(cover_photo_ids)).all()
        return {row[0]: row[1] for row in rows}

    @app.get("/api/stories", response_model=StoryList)
    def list_stories(
        include_tags: Optional[str] = Query(None, description="Comma-separated tags to include"),
        exclude_tags: Optional[str] = Query(None, description="Comma-separated tags to exclude"),
    ):
        include_set: Optional[set[str]] = (
            {t.strip() for t in include_tags.split(",") if t.strip()}
            if include_tags else None
        )
        exclude_set: Optional[set[str]] = (
            {t.strip() for t in exclude_tags.split(",") if t.strip()}
            if exclude_tags else None
        )

        with session_scope(Session) as s:
            stories = s.query(Story).order_by(Story.day).all()
            result = []
            for st in stories:
                items_rows = (
                    s.query(StoryItem, Photo)
                    .join(Photo, StoryItem.photo_id == Photo.id)
                    .filter(StoryItem.story_id == st.id)
                    .order_by(StoryItem.rank)
                    .all()
                )

                # Apply tag filters if requested
                if include_set is not None or exclude_set is not None:
                    all_photo_ids = [it.photo_id for it, p in items_rows]
                    tags_map = _get_primary_tags_for_photos(s, all_photo_ids)
                    filtered = []
                    for it, p in items_rows:
                        photo_tag = tags_map.get(it.photo_id, "")
                        if include_set is not None and photo_tag not in include_set:
                            continue
                        if exclude_set is not None and photo_tag in exclude_set:
                            continue
                        filtered.append((it, p))
                    items_rows = filtered

                # Load visits
                visits_rows = (
                    s.query(Visit)
                    .filter(Visit.story_id == st.id)
                    .order_by(Visit.rank)
                    .all()
                )

                # Get cover SHA for visits
                cover_ids = [v.cover_photo_id for v in visits_rows if v.cover_photo_id]
                cover_sha_map = _get_cover_sha_map(s, cover_ids)

                # Get primary tags for items
                all_pids = [it.photo_id for it, p in items_rows]
                primary_tags = _get_primary_tags_for_photos(s, all_pids)

                result.append(_story_to_out(st, items_rows, visits_rows, cover_sha_map, primary_tags))

        return StoryList(total=len(result), stories=result)

    @app.get("/api/stories/{story_id}", response_model=StoryOut)
    def get_story(
        story_id: int,
        include_tags: Optional[str] = Query(None),
        exclude_tags: Optional[str] = Query(None),
    ):
        include_set: Optional[set[str]] = (
            {t.strip() for t in include_tags.split(",") if t.strip()}
            if include_tags else None
        )
        exclude_set: Optional[set[str]] = (
            {t.strip() for t in exclude_tags.split(",") if t.strip()}
            if exclude_tags else None
        )

        with session_scope(Session) as s:
            st = s.get(Story, story_id)
            if st is None:
                raise HTTPException(404, detail="Story not found")

            items_rows = (
                s.query(StoryItem, Photo)
                .join(Photo, StoryItem.photo_id == Photo.id)
                .filter(StoryItem.story_id == st.id)
                .order_by(StoryItem.rank)
                .all()
            )

            if include_set is not None or exclude_set is not None:
                all_photo_ids = [it.photo_id for it, p in items_rows]
                tags_map = _get_primary_tags_for_photos(s, all_photo_ids)
                filtered = []
                for it, p in items_rows:
                    photo_tag = tags_map.get(it.photo_id, "")
                    if include_set is not None and photo_tag not in include_set:
                        continue
                    if exclude_set is not None and photo_tag in exclude_set:
                        continue
                    filtered.append((it, p))
                items_rows = filtered

            visits_rows = (
                s.query(Visit)
                .filter(Visit.story_id == st.id)
                .order_by(Visit.rank)
                .all()
            )

            cover_ids = [v.cover_photo_id for v in visits_rows if v.cover_photo_id]
            cover_sha_map = _get_cover_sha_map(s, cover_ids)

            all_pids = [it.photo_id for it, p in items_rows]
            primary_tags = _get_primary_tags_for_photos(s, all_pids)

            return _story_to_out(st, items_rows, visits_rows, cover_sha_map, primary_tags)

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
