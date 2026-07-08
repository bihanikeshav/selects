from __future__ import annotations

import logging
from collections import defaultdict
from pathlib import Path
from typing import Optional

# Pre-load torch in the MAIN thread so subsequent imports from FastAPI worker
# threads don't hit a Windows DLL-load race. Torch's C++ extensions need the
# host process to have the right PATH set up — that's only reliable when the
# initial import happens in the main thread.
try:
    import torch as _torch_preload  # noqa: F401
except Exception:
    pass

log = logging.getLogger(__name__)

from fastapi import Body, FastAPI, HTTPException, Query, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import select

from selects.config import FolderConfig
from selects.db import init_db, session_scope
from selects.db.models import (
    AestheticScore, ClassicalScore, Embedding, Moment, MomentMember, Photo, PhotoCategory,
    PhotoPerson, PhotoRating, PhotoTag, Story, StoryItem, Visit,
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
    moment_id: Optional[int] = None
    moment_size: Optional[int] = None


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
    moment_info_by_photo: Optional[dict[int, tuple[int, int]]] = None,
) -> StoryOut:
    minfo = moment_info_by_photo or {}
    items = []
    for i, (it, p) in enumerate(items_rows):
        pid = it.photo_id if it is not None else p.id
        mom_id, mom_size = minfo.get(pid, (None, None))
        items.append(StoryItemOut(
            rank=(it.rank if it is not None else i),
            photo_id=pid,
            sha256=p.sha256,
            thumb_url=f"/api/thumb/{p.sha256}",
            preview_url=f"/api/preview/{p.sha256}",
            scene_label=(it.scene_label if it is not None else None),
            taken_at=p.taken_at.isoformat() if p.taken_at else None,
            tag=primary_tag_by_photo.get(pid),
            moment_id=mom_id,
            moment_size=mom_size,
        ))
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
    # Resolve the sessionmaker at REQUEST time (not registration time) so that
    # switching the active library at runtime is picked up. ``cfg`` may be an
    # ActiveConfigProxy that forwards to whichever library is active; ``init_db``
    # is idempotent + per-path cached, so this call is cheap. ``session_scope``
    # only ever calls ``Session()``, so a plain callable is a drop-in.
    def Session():
        return init_db(cfg.db_path)()

    @app.get("/api/photos", response_model=PhotoList)
    def list_photos(
        offset: int = Query(0, ge=0),
        limit: int = Query(200, le=2000),
        rejected: Optional[bool] = None,
        tag: Optional[str] = None,
        collapse: str = Query("moments", description="'moments' collapses to primaries only; 'none' returns all"),
        sort: str = Query(
            "taken_at",
            description="'taken_at' (default), 'aesthetic' (combined NIMA+AP descending), 'iqa', 'random'",
        ),
        min_aesthetic_pct: float = Query(
            0.0, ge=0.0, le=100.0,
            description="Drop photos whose combined-aesthetic percentile is below this value",
        ),
    ):
        from sqlalchemy import func as _func

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
                select(Photo, ClassicalScore, Embedding, AestheticScore)
                .join(ClassicalScore, Photo.id == ClassicalScore.photo_id, isouter=True)
                .join(Embedding, Photo.id == Embedding.photo_id, isouter=True)
                .join(AestheticScore, Photo.id == AestheticScore.photo_id, isouter=True)
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

            # Aesthetic-percentile floor needs the library distribution
            aesthetic_floor_val: Optional[float] = None
            if min_aesthetic_pct > 0:
                from selects.ml.curation import compute_library_threshold
                aesthetic_floor_val = compute_library_threshold(
                    s, pct_floor=min_aesthetic_pct,
                )
                if aesthetic_floor_val is not None:
                    combined_expr = (
                        cfg.ap_weight * AestheticScore.ap25_score
                        + cfg.nima_weight * AestheticScore.nima_score
                    )
                    base = base.where(combined_expr >= aesthetic_floor_val)

            # Collapse: drop non-primary moment members in SQL, before offset/limit,
            # so pagination doesn't lose photos across page boundaries.
            if collapse == "moments":
                base = base.outerjoin(
                    MomentMember, Photo.id == MomentMember.photo_id
                ).outerjoin(
                    Moment, MomentMember.moment_id == Moment.id
                ).where(
                    (MomentMember.photo_id.is_(None))
                    | (Moment.primary_photo_id == Photo.id)
                )

            total = s.execute(
                select(_func.count()).select_from(base.subquery())
            ).scalar_one()

            # Sort
            if sort == "aesthetic":
                combined_expr = (
                    cfg.ap_weight * AestheticScore.ap25_score
                    + cfg.nima_weight * AestheticScore.nima_score
                )
                base = base.where(AestheticScore.ap25_score.isnot(None))
                base = base.where(AestheticScore.nima_score.isnot(None))
                base = base.order_by(combined_expr.desc())
            elif sort == "iqa":
                base = base.where(Embedding.aesthetic_iqa.isnot(None))
                base = base.order_by(Embedding.aesthetic_iqa.desc())
            elif sort == "random":
                base = base.order_by(_func.random())
            else:  # taken_at
                base = base.order_by(Photo.taken_at.asc().nullslast())

            rows = s.execute(base.offset(offset).limit(limit)).all()

            items = []
            for photo, score, emb, aest in rows:
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
        limit: int = Query(500, le=2000),
        source: Optional[str] = Query("thematic"),
    ):
        with session_scope(Session) as s:
            # Synthetic "uncategorized" cluster: photos with NO tag in this source
            if tag.lower() == "uncategorized":
                tagged_subq = s.query(PhotoTag.photo_id)
                if source:
                    tagged_subq = tagged_subq.filter(PhotoTag.source == source)
                tagged_ids = {r[0] for r in tagged_subq.all()}
                all_ids = {r[0] for r in s.query(Photo.id).all()}
                ids = list(all_ids - tagged_ids)
            else:
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
        include_tags: Optional[str] = Query(None, description="Legacy tag filter"),
        exclude_tags: Optional[str] = Query(None, description="Legacy tag filter"),
        curated: bool = Query(True, description="Apply aesthetic curation pipeline"),
        liked_only: bool = Query(
            False,
            description="Restrict each story to photos the user has Liked (Swipe.decision in keep/silver)",
        ),
        q: Optional[str] = Query(None, description="Natural-language semantic query"),
        scope_pct: float = Query(
            None,
            description="Per-scope percentile gate (default: cfg.aesthetic_per_scope_pct=75)",
        ),
        library_pct: float = Query(
            None,
            description="Library-wide percentile floor (default: cfg.aesthetic_library_pct=50)",
        ),
    ):
        """List stories, by default with the aesthetic-curation pipeline applied.

        When ``curated`` (default True), each story's photo list is filtered
        through the library-wide top-25% aesthetic gate + burst dedup, then
        re-ordered chronologically. Empty stories are dropped from the response.

        When ``q`` is supplied, photos within each story are ranked by SigLIP
        text→image cosine similarity to the query; stories whose top photo
        falls below a small threshold are dropped.
        """
        from selects.ml.curation import curate

        include_set: Optional[set[str]] = (
            {t.strip() for t in include_tags.split(",") if t.strip()}
            if include_tags else None
        )
        exclude_set: Optional[set[str]] = (
            {t.strip() for t in exclude_tags.split(",") if t.strip()}
            if exclude_tags else None
        )

        # Encode query text once (if any) — used to score each story below.
        q_vec = None
        if q:
            try:
                from selects.ml.embed import encode_text_prompts
                q_vec = encode_text_prompts([q])[0].astype("float32")  # already L2-normalized
            except Exception:
                q_vec = None

        with session_scope(Session) as s:
            stories = s.query(Story).order_by(Story.day).all()
            result = []
            match_scores: list[float] = []  # parallel to result; used to sort if NL search active
            for st in stories:
                # Per spec 2026-05-24: only "by day" and "by people" stories are
                # surfaced. Place stories collapse into the day they belong to;
                # pattern stories collapse into category facets.
                if st.day.startswith("place:") or st.day.startswith("pattern:"):
                    continue

                # Fetch story photos. For regular day stories ("YYYY-MM-DD"),
                # use ALL photos taken that day rather than the small set
                # baked into StoryItem (which was capped + scene-segmented).
                # This gives the curation pipeline the full library to choose
                # the top 25% from.
                items_rows = []
                if st.day and len(st.day) == 10 and st.day[4] == "-" and st.day[7] == "-":
                    from sqlalchemy import text as _text
                    rows = s.execute(
                        _text(
                            "SELECT id FROM photos "
                            "WHERE strftime('%Y-%m-%d', taken_at) = :d"
                        ),
                        {"d": st.day},
                    ).fetchall()
                    pids = [r[0] for r in rows]
                    if pids:
                        photos = (
                            s.query(Photo)
                            .filter(Photo.id.in_(pids))
                            .order_by(Photo.taken_at)
                            .all()
                        )
                        items_rows = [(None, p) for p in photos]

                if not items_rows:
                    # Fall back to StoryItem (people stories etc still use this).
                    items_rows = (
                        s.query(StoryItem, Photo)
                        .join(Photo, StoryItem.photo_id == Photo.id)
                        .filter(StoryItem.story_id == st.id)
                        .order_by(StoryItem.rank)
                        .all()
                    )

                # Helper to extract photo id from either tuple shape:
                #   (StoryItem, Photo) or (None, Photo)
                def _pid(row):
                    it, p = row
                    return it.photo_id if it is not None else p.id

                # Legacy tag filter (kept for backward compat with old clients)
                if include_set is not None or exclude_set is not None:
                    all_photo_ids = [_pid(r) for r in items_rows]
                    tags_map = _get_primary_tags_for_photos(s, all_photo_ids)
                    filtered = []
                    for r in items_rows:
                        photo_tag = tags_map.get(_pid(r), "")
                        if include_set is not None and photo_tag not in include_set:
                            continue
                        if exclude_set is not None and photo_tag in exclude_set:
                            continue
                        filtered.append(r)
                    items_rows = filtered

                # "Curated only" mode: restrict each story to liked photos.
                if liked_only and items_rows:
                    from selects.db.models import Swipe as _Swipe
                    pids = [_pid(r) for r in items_rows]
                    liked_pids = {
                        r[0]
                        for r in s.query(_Swipe.photo_id)
                        .filter(_Swipe.photo_id.in_(pids))
                        .filter(_Swipe.decision.in_(["keep", "silver"]))
                        .all()
                    }
                    items_rows = [r for r in items_rows if _pid(r) in liked_pids]

                # Aesthetic curation: gate + burst-dedup, chronological order.
                # Skipped when liked_only is on — the user already curated by hand.
                moment_info_for_story: dict[int, tuple[int, int]] = {}
                if curated and not liked_only and items_rows:
                    photo_ids = [_pid(r) for r in items_rows]
                    is_people_story = st.day.startswith("people:")
                    eff_scope = (
                        0.0
                        if is_people_story
                        else (scope_pct if scope_pct is not None else cfg.aesthetic_per_scope_pct)
                    )
                    eff_library = library_pct if library_pct is not None else cfg.aesthetic_library_pct
                    curated_list = curate(
                        s, photo_ids,
                        sort="chronological",
                        ap_w=cfg.ap_weight, nima_w=cfg.nima_weight,
                        pct_floor=eff_scope,
                        library_pct_floor=eff_library,
                    )
                    kept_ids = {c.photo_id for c in curated_list}
                    for c in curated_list:
                        if c.moment_id is not None and c.moment_size and c.moment_size > 1:
                            moment_info_for_story[c.photo_id] = (c.moment_id, c.moment_size)
                    if kept_ids:
                        items_rows = [r for r in items_rows if _pid(r) in kept_ids]
                        order_idx = {c.photo_id: i for i, c in enumerate(curated_list)}
                        items_rows.sort(key=lambda r: order_idx.get(_pid(r), 1e9))
                    else:
                        items_rows = []

                # Natural-language scoring: per story, find the best photo
                # similarity; drop stories whose best photo doesn't match.
                story_match_score = None
                if q_vec is not None and items_rows:
                    import numpy as _np
                    pids = [_pid(r) for r in items_rows]
                    emb_rows = (
                        s.query(Embedding.photo_id, Embedding.siglip)
                        .filter(Embedding.photo_id.in_(pids))
                        .all()
                    )
                    if emb_rows:
                        embs = _np.stack([
                            _np.frombuffer(r[1], dtype=_np.float16).astype(_np.float32)
                            for r in emb_rows
                        ])
                        embs = embs / (_np.linalg.norm(embs, axis=1, keepdims=True) + 1e-9)
                        sims = embs @ q_vec
                        sim_by_pid = {r[0]: float(s_) for r, s_ in zip(emb_rows, sims)}
                        # Drop stories whose max sim is below a small threshold.
                        # SigLIP image-text sims are typically [-0.05, 0.12]; 0.03
                        # is "meaningfully above random" for this content domain.
                        max_sim = max(sim_by_pid.values())
                        story_match_score = max_sim
                        if max_sim < 0.03:
                            continue
                        items_rows.sort(
                            key=lambda r: -sim_by_pid.get(_pid(r), -1.0)
                        )

                # Drop empty stories from the curated list (or liked-only list).
                if (curated or liked_only) and not items_rows:
                    continue

                visits_rows = (
                    s.query(Visit)
                    .filter(Visit.story_id == st.id)
                    .order_by(Visit.rank)
                    .all()
                )

                cover_ids = [v.cover_photo_id for v in visits_rows if v.cover_photo_id]
                cover_sha_map = _get_cover_sha_map(s, cover_ids)

                all_pids = [_pid(r) for r in items_rows]
                primary_tags = _get_primary_tags_for_photos(s, all_pids)

                story_out = _story_to_out(
                    st, items_rows, visits_rows, cover_sha_map, primary_tags,
                    moment_info_by_photo=moment_info_for_story,
                )
                result.append(story_out)
                match_scores.append(story_match_score if story_match_score is not None else 0.0)

            # If NL search active, sort stories by their match score desc.
            if q_vec is not None and match_scores:
                paired = sorted(zip(result, match_scores), key=lambda p: -p[1])
                result = [r for r, _ in paired]

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

    @app.get("/api/enhance/{sha256}")
    def enhance(
        sha256: str,
        preset: str = Query("film"),
        straighten: bool = Query(False, description="Apply quick auto-straighten"),
        grade: bool = Query(True, description="Apply aesthetic colour grading"),
        model: str = Query(
            "clahe",
            description=(
                "Which enhancement model. 'clahe' = the classical CLAHE+WB pipeline "
                "(default, fast). 'zero-dce-plus' = TPAMI'22 low-light specialist. "
                "'csrnet' = ECCV'20 conditional MLP retoucher (FiveK-trained, experimental). "
                "'nafnet' = CVPR'22 deblur (GoPro-trained, ~17M params)."
            ),
        ),
    ):
        """Render an edited preview. Either or both of grade/straighten can be
        applied independently.

        Cached per-(sha, model, grade, straighten).
        """
        from io import BytesIO
        from PIL import Image
        from selects.classical.aesthetic_grade import aesthetic_grade
        from selects.classical.straighten import straighten as do_straighten

        if preset not in ("film", "clarity", "portrait"):
            preset = "film"
        if model not in ("clahe", "zero-dce-plus", "csrnet", "nafnet"):
            model = "clahe"

        # Build a cache key reflecting all toggles independently.
        parts = []
        if grade:
            parts.append(model if model != "clahe" else preset)
        if straighten:
            parts.append("straight")
        if not parts:
            return _serve_image_for(cfg, sha256, kind="preview")
        suffix = "-".join(parts)

        cached = cfg.state_dir / "enhanced" / "v4" / f"{sha256}-{suffix}.jpg"
        cached.parent.mkdir(parents=True, exist_ok=True)
        if cached.exists():
            return FileResponse(cached, media_type="image/jpeg")

        preview_path = cfg.previews_dir / f"{sha256}.jpg"
        if not preview_path.exists():
            raise HTTPException(404, detail="preview missing")

        has_face = False
        if grade:
            with session_scope(Session) as s:
                row = (
                    s.query(ClassicalScore.faces_count)
                    .join(Photo, Photo.id == ClassicalScore.photo_id)
                    .filter(Photo.sha256 == sha256)
                    .first()
                )
                if row and row[0] and row[0] > 0:
                    has_face = True

        with Image.open(preview_path) as im:
            out = im
            if straighten:
                out, _angle = do_straighten(out)
            if grade:
                if model == "zero-dce-plus":
                    try:
                        from selects.ml.lowlight import enhance_with_zero_dce_plus
                        out = enhance_with_zero_dce_plus(out, cfg)
                    except Exception as exc:
                        log.warning("zero-dce-plus failed: %s — falling back to CLAHE", exc)
                        out = aesthetic_grade(out, preset=preset, has_face=has_face)
                elif model == "csrnet":
                    try:
                        from selects.ml.retouch_csrnet import retouch_with_csrnet
                        out = retouch_with_csrnet(out, cfg)
                    except Exception as exc:
                        log.warning("csrnet failed: %s — falling back to CLAHE", exc)
                        out = aesthetic_grade(out, preset=preset, has_face=has_face)
                elif model == "nafnet":
                    try:
                        from selects.ml.deblur_nafnet import deblur_with_nafnet
                        out = deblur_with_nafnet(out, cfg)
                    except Exception as exc:
                        log.warning("nafnet failed: %s — falling back to CLAHE", exc)
                        out = aesthetic_grade(out, preset=preset, has_face=has_face)
                else:
                    out = aesthetic_grade(out, preset=preset, has_face=has_face)
            buf = BytesIO()
            out.convert("RGB").save(buf, "JPEG", quality=90)
            cached.write_bytes(buf.getvalue())
            return Response(content=buf.getvalue(), media_type="image/jpeg")

    @app.get("/api/doctor/issues")
    def doctor_issues():
        """Classify photos with detectable problems and return them in buckets.

        Buckets:
          - underexposed: ClassicalScore.exposure < 0.35 and mean luma low
          - overexposed:  high clipping ratio at the top end
          - blurry:       ClassicalScore.blur below a threshold
          - blurry_keeper: blur low but combined aesthetic high (rescuable)

        Each photo can appear in multiple buckets.
        """
        AP_W = cfg.ap_weight
        NIMA_W = cfg.nima_weight
        BLUR_HARD = 150.0      # below this is genuinely blurry
        BLUR_SOFT = 400.0      # below this is "a bit soft" — interesting if aesthetic
        UNDER_MEAN = 0.32      # mean luma below this → underexposed
        OVER_MEAN = 0.78       # mean luma above this → overexposed
        HI_CLIP = 0.07         # >7% of pixels saturated at top end → overexposed
        AESTHETIC_HIGH = 5.8   # combined aesthetic threshold for "keeper" status

        with session_scope(Session) as s:
            rows = (
                s.query(
                    Photo.id,
                    Photo.sha256,
                    Photo.taken_at,
                    ClassicalScore.blur,
                    ClassicalScore.exposure,
                    ClassicalScore.luma_mean,
                    ClassicalScore.clipped_high,
                    ClassicalScore.clipped_low,
                    AestheticScore.ap25_score,
                    AestheticScore.nima_score,
                )
                .join(ClassicalScore, ClassicalScore.photo_id == Photo.id)
                .outerjoin(AestheticScore, AestheticScore.photo_id == Photo.id)
                .all()
            )

        underexposed: list[dict] = []
        overexposed: list[dict] = []
        blurry: list[dict] = []
        blurry_keepers: list[dict] = []

        from selects.ml.lowlight import luma_stats as _luma_stats
        from PIL import Image as _PILImage

        # Luma stats computed fresh this request (photo_id -> stats), flushed to
        # the DB once at the end so future visits skip the preview decode.
        freshly_computed: dict[int, dict] = {}

        for pid, sha, taken, blur, exp, luma_mean, clipped_high, clipped_low, ap25, nima in rows:
            blur_v = blur if blur is not None else 9999.0
            exp_v = exp if exp is not None else 0.5
            combined = (
                AP_W * ap25 + NIMA_W * nima
                if ap25 is not None and nima is not None
                else None
            )
            base = {
                "photo_id": pid,
                "sha256": sha,
                "taken_at": taken.isoformat() if taken else None,
                "thumb_url": f"/api/thumb/{sha}",
                "preview_url": f"/api/preview/{sha}",
                "blur": blur_v,
                "exposure": exp_v,
                "combined": combined,
            }

            # Mean+clipping tell us the *direction* of an exposure problem (the
            # classical `exposure` is a direction-less composite). Use the cached
            # values when present; otherwise decode the preview once and remember
            # the result to persist after the loop.
            stats = None
            if luma_mean is not None:
                stats = {
                    "mean": luma_mean,
                    "clipped_high": clipped_high if clipped_high is not None else 0.0,
                    "clipped_low": clipped_low if clipped_low is not None else 0.0,
                }
            else:
                preview_path = cfg.previews_dir / f"{sha}.jpg"
                if preview_path.exists():
                    try:
                        with _PILImage.open(preview_path) as _im:
                            stats = _luma_stats(_im)
                        freshly_computed[pid] = stats
                    except Exception as exc:
                        log.warning("doctor: luma_stats failed for %s: %s", sha, exc)

            if stats is not None:
                base["luma_mean"] = stats["mean"]
                base["clipped_high"] = stats["clipped_high"]
                base["clipped_low"] = stats["clipped_low"]
                if stats["mean"] < UNDER_MEAN or stats["clipped_low"] > 0.10:
                    underexposed.append(base)
                elif stats["mean"] > OVER_MEAN or stats["clipped_high"] > HI_CLIP:
                    overexposed.append(base)

            if blur_v < BLUR_HARD:
                blurry.append(base)
            elif blur_v < BLUR_SOFT and combined is not None and combined >= AESTHETIC_HIGH:
                blurry_keepers.append(base)

        # Persist any newly-computed stats so the next Doctor visit is instant.
        if freshly_computed:
            try:
                with session_scope(Session) as s:
                    for pid, stats in freshly_computed.items():
                        s.query(ClassicalScore).filter(
                            ClassicalScore.photo_id == pid
                        ).update(
                            {
                                ClassicalScore.luma_mean: stats["mean"],
                                ClassicalScore.clipped_high: stats["clipped_high"],
                                ClassicalScore.clipped_low: stats["clipped_low"],
                            },
                            synchronize_session=False,
                        )
            except Exception as exc:
                log.warning("doctor: failed to cache luma stats: %s", exc)

        # Sort each bucket: most-severe first
        underexposed.sort(key=lambda p: p.get("luma_mean", 1.0))
        overexposed.sort(key=lambda p: -p.get("clipped_high", 0.0))
        blurry.sort(key=lambda p: p["blur"])
        blurry_keepers.sort(key=lambda p: -(p["combined"] or 0))

        return {
            "underexposed": underexposed,
            "overexposed": overexposed,
            "blurry": blurry,
            "blurry_keepers": blurry_keepers,
            "counts": {
                "underexposed": len(underexposed),
                "overexposed": len(overexposed),
                "blurry": len(blurry),
                "blurry_keepers": len(blurry_keepers),
            },
        }

    @app.get("/api/doctor/histogram/{sha256}")
    def doctor_histogram(sha256: str):
        """Return per-channel + luminance histogram for a photo (64 bins each).

        Used by the ScoresCard / Doctor preview to show RGB+luma distribution.
        """
        from PIL import Image as _PILImage
        import numpy as _np
        preview_path = cfg.previews_dir / f"{sha256}.jpg"
        if not preview_path.exists():
            raise HTTPException(404, detail="preview missing")
        with _PILImage.open(preview_path) as im:
            arr = _np.asarray(im.convert("RGB"), dtype=_np.uint8)
        bins = 64
        edges = _np.linspace(0, 256, bins + 1)
        r = _np.histogram(arr[..., 0], bins=edges)[0].astype(int)
        g = _np.histogram(arr[..., 1], bins=edges)[0].astype(int)
        b = _np.histogram(arr[..., 2], bins=edges)[0].astype(int)
        luma = (0.299 * arr[..., 0] + 0.587 * arr[..., 1] + 0.114 * arr[..., 2]).astype(_np.uint8)
        l = _np.histogram(luma, bins=edges)[0].astype(int)
        return {
            "bins": bins,
            "r": r.tolist(),
            "g": g.tolist(),
            "b": b.tolist(),
            "luma": l.tolist(),
        }

    # ── Persons (face identity clusters) ─────────────────────────────────────
    class PersonOut(BaseModel):
        id: int
        label: Optional[str]
        photo_count: int
        cover_url: str

    class PersonList(BaseModel):
        total: int
        persons: list[PersonOut]

    @app.get("/api/persons", response_model=PersonList)
    def list_persons(
        min_confidence: float = Query(0.55, ge=0.0, le=1.0),
        min_face_px: int = Query(50, ge=0),
        min_photo_count: int = Query(2, ge=1),
    ):
        """List Person identities. Picks each cluster's BEST face (highest
        confidence × bbox-area) as the cover so a person's surfacing isn't
        gated by whichever face happened to be chosen at clustering time.

        Drops clusters whose best face fails the confidence/size thresholds —
        these are typically ArcFace false positives on paintings, animals,
        statues, etc.
        """
        from sqlalchemy import func

        from selects.db.models import FaceEmbedding, Person, PhotoPerson

        with session_scope(Session) as s:
            persons_all = s.query(Person).order_by(Person.photo_count.desc()).all()
            persons = []
            for p in persons_all:
                if p.photo_count < min_photo_count:
                    continue
                # Pick the best face in this cluster as cover: rank by
                # confidence * sqrt(area). Skip the cluster if no face in it
                # clears the thresholds.
                face_rows = (
                    s.query(FaceEmbedding)
                    .join(PhotoPerson, PhotoPerson.face_embedding_id == FaceEmbedding.id)
                    .filter(PhotoPerson.person_id == p.id)
                    .all()
                )
                if not face_rows:
                    continue
                best_face = max(
                    face_rows,
                    key=lambda f: (f.confidence or 0) * ((f.bbox_w * f.bbox_h) ** 0.5),
                )
                if best_face.confidence < min_confidence:
                    continue
                if max(best_face.bbox_w, best_face.bbox_h) < min_face_px:
                    continue
                persons.append(PersonOut(
                    id=p.id, label=p.label,
                    photo_count=p.photo_count,
                    cover_url=f"/api/face_crop/{best_face.id}",
                ))
        return PersonList(total=len(persons), persons=persons)

    @app.get("/api/face_crop/{face_id}")
    def face_crop(face_id: int):
        """Return the face's bounding box cropped from the 1024px preview."""
        from io import BytesIO

        from PIL import Image

        from selects.db.models import FaceEmbedding

        with session_scope(Session) as s:
            row = s.query(FaceEmbedding, Photo).join(
                Photo, FaceEmbedding.photo_id == Photo.id
            ).filter(FaceEmbedding.id == face_id).first()
            if not row:
                raise HTTPException(404, detail="face not found")
            fe, photo = row
            preview_abs = cfg.state_dir / photo.preview_path

        try:
            with Image.open(preview_abs) as im:
                margin = 20
                x1 = max(0, fe.bbox_x - margin)
                y1 = max(0, fe.bbox_y - margin)
                x2 = min(im.width, fe.bbox_x + fe.bbox_w + margin)
                y2 = min(im.height, fe.bbox_y + fe.bbox_h + margin)
                crop = im.crop((x1, y1, x2, y2)).convert("RGB")
                buf = BytesIO()
                crop.save(buf, "JPEG", quality=88)
                return Response(content=buf.getvalue(), media_type="image/jpeg")
        except FileNotFoundError:
            raise HTTPException(404, detail="preview missing")

    @app.patch("/api/persons/{person_id}")
    def label_person(person_id: int, payload: dict = Body(...)):
        """Body: {label: str | null}."""
        from selects.db.models import Person, Story

        label = payload.get("label")
        if label is not None and not isinstance(label, str):
            raise HTTPException(400, detail="label must be a string or null")
        if isinstance(label, str):
            label = label.strip() or None

        with session_scope(Session) as s:
            person = s.get(Person, person_id)
            if not person:
                raise HTTPException(404, detail="person not found")
            old_label = person.label
            person.label = label
            s.flush()

            # Cascade rename to story titles + synthetic_day keys
            new_name = label or f"P{person_id}"
            old_name = old_label or f"P{person_id}"
            for story in s.query(Story).filter(Story.day.like("people:%")).all():
                if old_name in story.day or old_name in story.title:
                    story.day = story.day.replace(old_name, new_name)
                    story.title = story.title.replace(old_name, new_name)
                    s.add(story)

        return {"ok": True, "label": label}

    @app.post("/api/persons/merge")
    def merge_persons(payload: dict = Body(...)):
        """Merge one or more source Person identities into a target.

        Body: {target_id: int, source_ids: int[]}
        """
        from sqlalchemy import func

        from selects.db.models import Person, PhotoPerson

        target_id = payload.get("target_id")
        source_ids_raw = payload.get("source_ids")
        if not isinstance(target_id, int):
            raise HTTPException(400, detail="target_id must be an integer")
        if not isinstance(source_ids_raw, list) or not source_ids_raw:
            raise HTTPException(400, detail="source_ids must be a non-empty list")

        source_ids = []
        for value in source_ids_raw:
            if not isinstance(value, int):
                raise HTTPException(400, detail="source_ids must contain only integers")
            if value != target_id and value not in source_ids:
                source_ids.append(value)
        if not source_ids:
            raise HTTPException(400, detail="choose at least one source person")

        with session_scope(Session) as s:
            target = s.get(Person, target_id)
            if not target:
                raise HTTPException(404, detail="target person not found")

            sources = s.query(Person).filter(Person.id.in_(source_ids)).all()
            if len(sources) != len(source_ids):
                raise HTTPException(404, detail="one or more source persons were not found")

            if target.label is None:
                first_source_label = next((p.label for p in sources if p.label), None)
                if first_source_label:
                    target.label = first_source_label

            moved = 0
            source_rows = (
                s.query(PhotoPerson)
                .filter(PhotoPerson.person_id.in_(source_ids))
                .all()
            )
            for row in source_rows:
                existing = (
                    s.query(PhotoPerson)
                    .filter(
                        PhotoPerson.photo_id == row.photo_id,
                        PhotoPerson.person_id == target_id,
                    )
                    .first()
                )
                if existing is None:
                    s.add(PhotoPerson(
                        photo_id=row.photo_id,
                        person_id=target_id,
                        face_embedding_id=row.face_embedding_id,
                        confidence=row.confidence,
                    ))
                    moved += 1
                elif row.confidence > existing.confidence:
                    existing.face_embedding_id = row.face_embedding_id
                    existing.confidence = row.confidence
                s.delete(row)

            for person in sources:
                s.delete(person)

            s.flush()
            target.photo_count = (
                s.query(func.count(func.distinct(PhotoPerson.photo_id)))
                .filter(PhotoPerson.person_id == target_id)
                .scalar()
                or 0
            )

        return {"ok": True, "target_id": target_id, "source_ids": source_ids, "moved": moved}

    @app.get("/api/persons/{person_id}/photos", response_model=PhotoList)
    def person_photos(person_id: int, limit: int = Query(500, le=2000)):
        from selects.db.models import PhotoPerson

        with session_scope(Session) as s:
            ids = [
                r[0]
                for r in s.query(PhotoPerson.photo_id)
                .filter(PhotoPerson.person_id == person_id)
                .all()
            ]
            if not ids:
                return PhotoList(total=0, items=[])

            rows = s.execute(
                select(Photo, ClassicalScore, Embedding)
                .join(ClassicalScore, Photo.id == ClassicalScore.photo_id, isouter=True)
                .join(Embedding, Photo.id == Embedding.photo_id, isouter=True)
                .where(Photo.id.in_(ids))
                .order_by(Embedding.aesthetic_iqa.desc().nulls_last())
                .limit(limit)
            ).all()

            items = []
            for photo, classical, _emb in rows:
                items.append(PhotoOut(
                    id=photo.id, sha256=photo.sha256, path=photo.path,
                    format=photo.format, width=photo.width, height=photo.height,
                    taken_at=photo.taken_at.isoformat() if photo.taken_at else None,
                    thumb_url=f"/api/thumb/{photo.sha256}",
                    preview_url=f"/api/preview/{photo.sha256}",
                    blur=classical.blur if classical else None,
                    exposure=classical.exposure if classical else None,
                    faces_count=classical.faces_count if classical else None,
                    auto_reject=classical.auto_reject if classical else None,
                    reject_reason=classical.reject_reason if classical else None,
                ))
        return PhotoList(total=len(items), items=items)

    # ── Cull swipes (J/K/L persistence) ──────────────────────────────────────
    @app.post("/api/swipes/{sha256}")
    def record_swipe(sha256: str, decision: str = Body(..., embed=True)):
        from selects.db.models import Swipe

        with session_scope(Session) as s:
            photo = s.query(Photo).filter(Photo.sha256 == sha256).first()
            if not photo:
                raise HTTPException(404, detail="photo not found")
            existing = s.get(Swipe, photo.id)
            if existing:
                existing.decision = decision
                s.add(existing)
            else:
                s.add(Swipe(photo_id=photo.id, decision=decision))
        return {"ok": True, "decision": decision}

    @app.get("/api/curated")
    def list_curated(
        sort: str = Query("aesthetic", description="aesthetic | taken_at"),
    ):
        """Return all photos the user has liked (Swipe.decision in keep/silver),
        sorted by combined NIMA+AP aesthetic descending by default.

        This is the curated set — the user's chosen keepers post-cull,
        post-curate, ready for edit and post.
        """
        from selects.db.models import Swipe

        with session_scope(Session) as s:
            base = (
                s.query(
                    Photo,
                    AestheticScore.ap25_score,
                    AestheticScore.nima_score,
                )
                .join(Swipe, Swipe.photo_id == Photo.id)
                .outerjoin(AestheticScore, AestheticScore.photo_id == Photo.id)
                .filter(Swipe.decision.in_(["keep", "silver"]))
            )
            rows = base.all()
            ap_w, nima_w = cfg.ap_weight, cfg.nima_weight
            entries = []
            for photo, ap25, nima in rows:
                combined = None
                if ap25 is not None and nima is not None:
                    combined = ap_w * ap25 + nima_w * nima
                entries.append({
                    "photo_id": photo.id,
                    "sha256": photo.sha256,
                    "taken_at": photo.taken_at.isoformat() if photo.taken_at else None,
                    "thumb_url": f"/api/thumb/{photo.sha256}",
                    "preview_url": f"/api/preview/{photo.sha256}",
                    "combined": combined,
                    "ap25": ap25,
                    "nima": nima,
                })
            if sort == "aesthetic":
                entries.sort(key=lambda e: -(e["combined"] or -1e9))
            else:  # taken_at
                entries.sort(key=lambda e: e["taken_at"] or "")
        return {"total": len(entries), "photos": entries}

    @app.get("/api/likes/status")
    def likes_status(shas: str = Query("", description="comma-separated sha256s")):
        """Return {sha256: liked_bool} for the given list."""
        from selects.db.models import Swipe
        sha_list = [s for s in shas.split(",") if s.strip()] if shas else None
        out: dict[str, bool] = {}
        with session_scope(Session) as s:
            q = s.query(Photo.sha256, Swipe.decision).join(
                Swipe, Swipe.photo_id == Photo.id
            )
            if sha_list:
                q = q.filter(Photo.sha256.in_(sha_list))
            for sha, decision in q.all():
                out[sha] = decision in ("keep", "silver")
        return out

    @app.get("/api/swipes/summary")
    def swipes_summary():
        from sqlalchemy import func

        from selects.db.models import Swipe

        with session_scope(Session) as s:
            rows = s.query(Swipe.decision, func.count(Swipe.photo_id)).group_by(Swipe.decision).all()
            counts = {d: c for d, c in rows}
            total_photos = s.query(Photo).count()
        return {
            "total_photos": total_photos,
            "kept": counts.get("keep", 0) + counts.get("silver", 0),
            "rejected": counts.get("reject", 0),
            "skipped": counts.get("skip", 0),
            "undecided": total_photos - sum(counts.values()),
        }

    @app.get("/api/map/markers")
    def map_markers(grid_deg: float = Query(0.01, gt=0, lt=1)):
        """Return GPS-binned photo clusters for the map view.

        Buckets photos onto a ~1km grid (default 0.01° ≈ 1.1 km at the
        equator, slightly less at high latitudes) and returns a cover photo
        for each bucket so the frontend renders one pin per geographic
        cluster instead of 1000 overlapping pins.
        """
        from collections import defaultdict as _dd

        with session_scope(Session) as s:
            rows = s.query(
                Photo.id,
                Photo.sha256,
                Photo.gps_lat,
                Photo.gps_lon,
                Photo.taken_at,
            ).filter(
                Photo.gps_lat.is_not(None),
                Photo.gps_lon.is_not(None),
            ).all()

            visit_rows = s.query(Visit.name, Visit.lat, Visit.lon, Visit.arrived_at, Visit.departed_at).all()

        # Bin into grid cells
        cells: dict[tuple[int, int], dict] = _dd(lambda: {"photos": [], "lat_sum": 0.0, "lon_sum": 0.0})
        for pid, sha, lat, lon, _taken in rows:
            key = (round(lat / grid_deg), round(lon / grid_deg))
            cells[key]["photos"].append((pid, sha, lat, lon, _taken))
            cells[key]["lat_sum"] += lat
            cells[key]["lon_sum"] += lon

        # Map cell → nearest named visit (if any)
        def nearest_visit(lat: float, lon: float) -> Optional[str]:
            best = None
            best_d = 0.05  # ~5km cap
            for name, vlat, vlon, *_ in visit_rows:
                if vlat is None or vlon is None:
                    continue
                d = ((lat - vlat) ** 2 + (lon - vlon) ** 2) ** 0.5
                if d < best_d:
                    best_d = d
                    best = name
            return best

        markers = []
        for cell, data in cells.items():
            n = len(data["photos"])
            lat = data["lat_sum"] / n
            lon = data["lon_sum"] / n
            # Pick the latest photo as cover (most recent moment)
            cover_pid, cover_sha, _, _, _ = max(data["photos"], key=lambda p: p[4] or "")
            markers.append({
                "lat": lat,
                "lon": lon,
                "count": n,
                "cover_sha256": cover_sha,
                "cover_url": f"/api/thumb/{cover_sha}",
                "place": nearest_visit(lat, lon),
            })

        markers.sort(key=lambda m: -m["count"])
        return {"total": sum(m["count"] for m in markers), "markers": markers}

    @app.post("/api/stories/{story_id}/export")
    def export_story(story_id: int):
        """Copy a story's photos (in story order) to .selects/exports/stories/<title>/.

        Each file gets a 2-digit prefix so the user can drop the whole folder
        into Instagram and the carousel order is preserved.
        """
        import shutil

        with session_scope(Session) as s:
            story = s.get(Story, story_id)
            if not story:
                raise HTTPException(404, detail="story not found")
            items = (
                s.query(StoryItem, Photo)
                .join(Photo, StoryItem.photo_id == Photo.id)
                .filter(StoryItem.story_id == story_id)
                .order_by(StoryItem.rank)
                .all()
            )
            if not items:
                raise HTTPException(404, detail="story has no items")
            title = story.title

        clean = "".join(c if c.isalnum() or c in "-_ " else "_" for c in title)[:80] or "story"
        out_dir = cfg.state_dir / "exports" / "stories" / clean
        out_dir.mkdir(parents=True, exist_ok=True)

        copied = []
        skipped = []
        for it, photo in items:
            src = Path(photo.path)
            if not src.exists():
                skipped.append({"photo_id": photo.id, "reason": "missing"})
                continue
            dst = out_dir / f"{it.rank:02d}_{src.name}"
            try:
                shutil.copy2(src, dst)
                copied.append({"photo_id": photo.id, "out": str(dst)})
            except Exception as exc:
                skipped.append({"photo_id": photo.id, "reason": str(exc)})

        return {
            "out_dir": str(out_dir),
            "copied": len(copied),
            "skipped": len(skipped),
            "skipped_detail": skipped,
        }

    @app.get("/api/search")
    def search(q: str = Query(..., min_length=1), k: int = Query(60, le=300)):
        """Free-text photo search via SigLIP image-text similarity."""
        from selects.ml.search import search_photos

        results = search_photos(cfg, q, k=k)
        return {
            "query": q,
            "total": len(results),
            "results": [
                {
                    "photo_id": pid,
                    "sha256": sha,
                    "score": score,
                    "thumb_url": f"/api/thumb/{sha}",
                    "preview_url": f"/api/preview/{sha}",
                }
                for pid, sha, score in results
            ],
        }

    # ── darktable integration ────────────────────────────────────────────────
    @app.get("/api/edits/status")
    def edits_status(shas: str = Query("", description="comma-separated sha256 list")):
        """Report which of the given photos have an XMP sidecar.

        XMP next to an original = darktable (or any editor) has saved develop
        instructions for it. The presence of a fresh XMP = "edited".
        """
        sha_list = [s for s in shas.split(",") if s.strip()] if shas else None
        out: dict[str, dict] = {}
        with session_scope(Session) as s:
            q = s.query(Photo.sha256, Photo.path)
            if sha_list:
                q = q.filter(Photo.sha256.in_(sha_list))
            for sha, path_str in q.all():
                p = Path(path_str)
                xmp = p.with_suffix(p.suffix + ".xmp")
                alt = p.with_suffix(".xmp")
                edited = False
                mtime = None
                for cand in (xmp, alt):
                    if cand.exists():
                        edited = True
                        mtime = cand.stat().st_mtime
                        break
                out[sha] = {"edited": edited, "mtime": mtime}
        return out


    def _detached_popen_kwargs() -> dict:
        """subprocess.Popen kwargs to fully detach a launched GUI editor.

        Windows: DETACHED_PROCESS (no console window, survives parent exit).
        POSIX (macOS/Linux): start_new_session puts the child in its own
        session so it isn't tied to the server process/terminal.
        """
        import subprocess
        import sys

        if sys.platform == "win32":
            return {"creationflags": getattr(subprocess, "DETACHED_PROCESS", 0)}
        return {"start_new_session": True}

    def _find_editor_binary(
        names: list[str],
        windows_candidates: list[Path],
        bundle_subdir: Optional[str] = None,
    ) -> Optional[str]:
        """Locate an external editor binary across Windows/macOS/Linux.

        Resolution order:

          1. A copy bundled inside the app (PyInstaller onedir). When the
             release build ships ``<app>/darktable/bin`` alongside the binary,
             we use it first so editing "just works" with no system install.
          2. ``shutil.which`` for each of ``names`` (binary on PATH).
          3. Well-known per-platform install locations:
             * Windows: caller-supplied ``windows_candidates``.
             * macOS: ``/Applications/<app>.app/Contents/MacOS/<name>``.
             * Linux: ``/usr/bin``, ``/usr/local/bin``, and — best-effort —
               the Flatpak export path for darktable.
        """
        import platform
        import shutil
        import sys

        system = platform.system()

        # (1) Bundled copy — data files land under sys._MEIPASS in a frozen
        # onedir build (the app's `_internal` dir). Skipped when not frozen.
        bundle_root = getattr(sys, "_MEIPASS", None)
        if bundle_root and bundle_subdir:
            suffix = ".exe" if system == "Windows" else ""
            for name in names:
                cand = Path(bundle_root) / bundle_subdir / "bin" / f"{name}{suffix}"
                if cand.exists():
                    return str(cand)

        # (2) On PATH.
        for name in names:
            found = shutil.which(name)
            if found:
                return found

        candidates: list[Path] = []
        if system == "Windows":
            candidates.extend(windows_candidates)
        elif system == "Darwin":
            for name in names:
                # darktable and darktable-cli both live inside darktable.app;
                # take the part before the first "-" as the app bundle name.
                app_name = name.split("-")[0]
                candidates.append(
                    Path("/Applications")
                    / f"{app_name}.app" / "Contents" / "MacOS" / name
                )
        else:  # Linux and other POSIX systems
            for base in (Path("/usr/bin"), Path("/usr/local/bin")):
                for name in names:
                    candidates.append(base / name)
            if any(n.startswith("darktable") for n in names):
                # Flatpak exports use the app's reverse-DNS id rather than
                # the raw binary name; best-effort guess.
                candidates.append(
                    Path("/var/lib/flatpak/exports/bin/org.darktable.Darktable")
                )

        for cand in candidates:
            if cand.exists():
                return str(cand)
        return None

    def _find_darktable() -> Optional[str]:
        """Return the path to the darktable executable, if discoverable."""
        return _find_editor_binary(
            ["darktable"],
            windows_candidates=[
                Path(r"C:\Program Files\darktable\bin\darktable.exe"),
                Path(r"C:\Program Files (x86)\darktable\bin\darktable.exe"),
                Path(r"C:\Program Files\darktable\darktable.exe"),
                Path(r"C:\darktable\bin\darktable.exe"),
            ],
            bundle_subdir="darktable",
        )

    def _find_darktable_cli() -> Optional[str]:
        """Same auto-discovery for darktable-cli (ships next to darktable)."""
        return _find_editor_binary(
            ["darktable-cli"],
            windows_candidates=[
                Path(r"C:\Program Files\darktable\bin\darktable-cli.exe"),
                Path(r"C:\Program Files (x86)\darktable\bin\darktable-cli.exe"),
            ],
            bundle_subdir="darktable",
        )

    @app.post("/api/edit/darktable")
    def launch_darktable(payload: dict = Body(...)):
        """Launch darktable with the selected originals in a per-session library.

        Body: {sha256s: list[str]}

        Per-session library avoids polluting the user's main catalog and lets
        us round-trip XMP edits cleanly. Darktable writes XMPs next to the
        original file when the user saves — no further coordination needed.
        """
        import subprocess
        import uuid

        sha256s = payload.get("sha256s") or []
        if not isinstance(sha256s, list) or not sha256s:
            raise HTTPException(400, detail="sha256s must be a non-empty list of strings")

        editor_cmd = _find_darktable()
        if not editor_cmd:
            raise HTTPException(
                400,
                detail=(
                    "darktable not found. Install it from "
                    "https://www.darktable.org/install/ — the launcher checks "
                    "PATH plus common per-OS install locations "
                    "(e.g. Program Files on Windows, /Applications on macOS, "
                    "/usr/bin or Flatpak on Linux)."
                ),
            )

        with session_scope(Session) as s:
            paths = [
                r[0]
                for r in s.query(Photo.path).filter(Photo.sha256.in_(sha256s)).all()
            ]
        if not paths:
            raise HTTPException(404, detail="no matching photos")

        session_id = uuid.uuid4().hex[:8]
        lib_dir = cfg.state_dir / "darktable-sessions"
        lib_dir.mkdir(parents=True, exist_ok=True)
        library_path = lib_dir / f"session-{session_id}.db"

        cmd = [editor_cmd, "--library", str(library_path), *paths]
        try:
            subprocess.Popen(cmd, close_fds=True, **_detached_popen_kwargs())
        except Exception as exc:
            raise HTTPException(500, detail=f"failed to launch darktable: {exc}")

        return {"opened": len(paths), "session": session_id, "library": str(library_path)}


    @app.post("/api/edits/export")
    def export_edits(payload: dict = Body(...)):
        """Render XMP edits to JPEGs via darktable-cli into
        .selects/exports/<cluster>/<timestamp>/.

        Body: {sha256s: list[str], cluster_name?: str, width?: int, height?: int}.
        """
        import subprocess
        from datetime import datetime

        sha256s = payload.get("sha256s") or []
        cluster_name = payload.get("cluster_name") or "untitled"
        width = int(payload.get("width") or 2048)
        height = int(payload.get("height") or 0)
        if not isinstance(sha256s, list) or not sha256s:
            raise HTTPException(400, detail="sha256s must be a non-empty list")

        dt_cli = _find_darktable_cli()
        if not dt_cli:
            raise HTTPException(
                400,
                detail="darktable-cli not found. It ships with darktable; install from "
                       "https://www.darktable.org/install/ or add its bin dir to PATH.",
            )

        with session_scope(Session) as s:
            rows = s.query(Photo.sha256, Photo.path).filter(
                Photo.sha256.in_(sha256s)
            ).all()
        if not rows:
            raise HTTPException(404, detail="no matching photos")

        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        clean = "".join(c if c.isalnum() or c in "-_" else "_" for c in cluster_name) or "untitled"
        out_dir = cfg.state_dir / "exports" / clean / ts
        out_dir.mkdir(parents=True, exist_ok=True)

        results = []
        for sha, path_str in rows:
            src = Path(path_str)
            out = out_dir / f"{src.stem}.jpg"
            cmd = [
                dt_cli,
                str(src),
                str(out),
                "--width", str(width),
                "--height", str(height),
                "--core", "--conf", "plugins/imageio/format/jpeg/quality=92",
            ]
            try:
                proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
                ok = proc.returncode == 0 and out.exists()
                results.append({
                    "sha256": sha, "ok": ok,
                    "out": str(out) if ok else None,
                    "stderr": (proc.stderr[-300:] if proc.stderr else None) if not ok else None,
                })
            except subprocess.TimeoutExpired:
                results.append({"sha256": sha, "ok": False, "out": None, "stderr": "timeout"})

        return {
            "out_dir": str(out_dir),
            "total": len(results),
            "exported": sum(1 for r in results if r["ok"]),
            "results": results,
        }

    @app.post("/api/edit/open")
    def open_in_editor(payload: dict = Body(...)):
        """Launch the chosen OSS editor with the original photo paths for the
        given sha256s. Editor runs detached — server returns immediately.

        Body: {sha256s: list[str], editor?: "darktable"|"rawtherapee"|"gimp"}.
        """
        import shutil
        import subprocess

        sha256s = payload.get("sha256s") or []
        editor = payload.get("editor") or "darktable"
        if not isinstance(sha256s, list) or not sha256s:
            raise HTTPException(400, detail="sha256s must be a non-empty list")

        editor_cmd = shutil.which(editor)
        if not editor_cmd:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"'{editor}' not found on PATH. Install it or pick a different "
                    "editor (darktable / rawtherapee / gimp)."
                ),
            )

        with session_scope(Session) as s:
            rows = s.query(Photo.path).filter(Photo.sha256.in_(sha256s)).all()
            paths = [r[0] for r in rows]
        if not paths:
            raise HTTPException(404, detail="no matching photos")

        try:
            subprocess.Popen(
                [editor_cmd, *paths], close_fds=True, **_detached_popen_kwargs()
            )
        except Exception as exc:
            raise HTTPException(500, detail=f"failed to launch {editor}: {exc}")
        return {"opened": len(paths), "editor": editor}

    # ── Aesthetic calibration ────────────────────────────────────────────────
    #
    # The on-disk CLIP-IQA score (Embedding.aesthetic_iqa) doesn't track human
    # aesthetic judgment well. This subsystem lets the user rate photos
    # (+1/-1/skip), trains a per-folder personal logistic regression on SigLIP
    # embeddings, and surfaces NIMA + AP-V2.5 + personal scores alongside the
    # original IQA so the user can compare and pick which signal to drive
    # story curation with.

    def _combined_percentile_pairs(s) -> list[tuple]:
        """Return list of (photo_id, sha256, taken_at, iqa, nima, ap25, personal,
        combined_pct) for every photo with both NIMA and AP25 scored, sorted by
        combined percentile ascending.

        combined_pct = mean(percentile_rank_in_nima, percentile_rank_in_ap25), 0–100.
        """
        rows = (
            s.query(
                Photo.id, Photo.sha256, Photo.taken_at,
                Embedding.aesthetic_iqa,
                AestheticScore.nima_score, AestheticScore.ap25_score,
                AestheticScore.personal_score,
            )
            .join(Embedding, Embedding.photo_id == Photo.id)
            .join(AestheticScore, AestheticScore.photo_id == Photo.id)
            .filter(AestheticScore.nima_score.isnot(None))
            .filter(AestheticScore.ap25_score.isnot(None))
            .all()
        )
        if not rows:
            return []
        # Compute percentile ranks within current library
        n = len(rows)
        # rank nima
        nima_sorted = sorted(range(n), key=lambda i: rows[i][4])
        nima_pct = [0.0] * n
        for rank, idx in enumerate(nima_sorted):
            nima_pct[idx] = (rank / max(1, n - 1)) * 100
        ap_sorted = sorted(range(n), key=lambda i: rows[i][5])
        ap_pct = [0.0] * n
        for rank, idx in enumerate(ap_sorted):
            ap_pct[idx] = (rank / max(1, n - 1)) * 100
        out = []
        for i, r in enumerate(rows):
            combined = (nima_pct[i] + ap_pct[i]) / 2.0
            out.append((*r, combined))
        out.sort(key=lambda t: t[7])  # ascending by combined
        return out

    @app.get("/api/calibrate/extremes")
    def calibrate_extremes(bucket: str = Query("worst"), n: int = Query(30, ge=1, le=200)):
        """Return N unrated photos at the worst (bottom) or best (top) of the
        NIMA+AP combined percentile ranking.

        bucket='worst' → bottom N, presented as candidates for rescue (default
        rating -1, user flips ones they like to +1).
        bucket='best'  → top N, presented as candidates for demotion (default
        rating +1, user flips ones they don't like to -1).
        """
        if bucket not in ("worst", "best"):
            raise HTTPException(400, detail="bucket must be 'worst' or 'best'")

        with session_scope(Session) as s:
            rated_ids = {r[0] for r in s.query(PhotoRating.photo_id).all()}
            ranked = _combined_percentile_pairs(s)
        if not ranked:
            return {"photos": [], "total_indexed": 0, "rated_count": len(rated_ids)}

        unrated = [r for r in ranked if r[0] not in rated_ids]
        chosen = unrated[:n] if bucket == "worst" else list(reversed(unrated[-n:]))

        return {
            "photos": [
                {
                    "photo_id": r[0],
                    "sha256": r[1],
                    "taken_at": r[2].isoformat() if r[2] else None,
                    "thumb_url": f"/api/thumb/{r[1]}",
                    "preview_url": f"/api/preview/{r[1]}",
                    "scores": {
                        "iqa": r[3],
                        "nima": r[4],
                        "ap25": r[5],
                        "personal": r[6],
                        "combined": r[7],
                    },
                    "default_rating": -1 if bucket == "worst" else 1,
                }
                for r in chosen
            ],
            "bucket": bucket,
            "total_indexed": len(ranked),
            "rated_count": len(rated_ids),
        }

    @app.get("/api/calibrate/next")
    def calibrate_next():
        """Return one random photo that hasn't been rated yet, with all 4 scores.

        Kept for backward-compat; the primary calibration flow now uses
        /api/calibrate/extremes.
        """
        import random as _random

        with session_scope(Session) as s:
            rated_ids = {r[0] for r in s.query(PhotoRating.photo_id).all()}
            rows = (
                s.query(Photo.id, Photo.sha256, Photo.taken_at, Embedding.aesthetic_iqa)
                .join(Embedding, Embedding.photo_id == Photo.id)
                .all()
            )
            unrated = [r for r in rows if r[0] not in rated_ids]
            if not unrated:
                return {"done": True, "rated_count": len(rated_ids)}
            choice = _random.choice(unrated)
            pid = choice[0]
            aest = s.get(AestheticScore, pid)
            return {
                "done": False,
                "photo_id": pid,
                "sha256": choice[1],
                "taken_at": choice[2].isoformat() if choice[2] else None,
                "preview_url": f"/api/preview/{choice[1]}",
                "scores": {
                    "iqa": choice[3],
                    "nima": aest.nima_score if aest else None,
                    "ap25": aest.ap25_score if aest else None,
                    "personal": aest.personal_score if aest else None,
                },
                "rated_count": len(rated_ids),
                "total": len(rows),
            }

    @app.post("/api/calibrate/rate_batch")
    def calibrate_rate_batch(payload: dict = Body(...)):
        """Persist many ratings at once.
        Body: {ratings: [{photo_id: int, rating: -1|0|1}, ...]}.
        """
        from datetime import datetime as _dt
        ratings = payload.get("ratings") or []
        n_written = 0
        with session_scope(Session) as s:
            for item in ratings:
                pid = item.get("photo_id") if isinstance(item, dict) else None
                rating = item.get("rating") if isinstance(item, dict) else None
                if not isinstance(pid, int) or rating not in (-1, 0, 1):
                    continue
                existing = s.get(PhotoRating, pid)
                if existing:
                    existing.rating = rating
                    existing.rated_at = _dt.utcnow()
                else:
                    s.add(PhotoRating(photo_id=pid, rating=rating, rated_at=_dt.utcnow()))
                n_written += 1
        return {"ok": True, "n": n_written}

    @app.post("/api/calibrate/rate")
    def calibrate_rate(payload: dict = Body(...)):
        """Persist a single rating. Body: {photo_id: int, rating: -1|0|1}."""
        from datetime import datetime as _dt
        photo_id = payload.get("photo_id")
        rating = payload.get("rating")
        if not isinstance(photo_id, int):
            raise HTTPException(400, detail="photo_id must be an int")
        if rating not in (-1, 0, 1):
            raise HTTPException(400, detail="rating must be -1, 0, or 1")
        with session_scope(Session) as s:
            existing = s.get(PhotoRating, photo_id)
            if existing:
                existing.rating = rating
                existing.rated_at = _dt.utcnow()
            else:
                s.add(PhotoRating(photo_id=photo_id, rating=rating, rated_at=_dt.utcnow()))
        return {"ok": True}

    @app.get("/api/calibrate/agreement")
    def calibrate_agreement():
        """For each model, report the median percentile rank of the user's
        upvoted photos within the model's full-library ranking.

        Under the new semantics, only rating=+1 is a positive signal.
        A rating of -1 means "user reviewed and agreed with the ensemble's
        placement", carrying no training signal.

        Per-model output:
          - median_upvote_percentile: median percentile rank (0-100) of
            upvoted photos under this model. 50 = upvotes scattered evenly;
            90 = upvotes consistently near the top (good agreement).
          - n_scored_upvotes: how many upvoted photos this model has scored
        """
        import numpy as np

        with session_scope(Session) as s:
            upvoted_ids = {
                r[0]
                for r in s.query(PhotoRating.photo_id)
                .filter(PhotoRating.rating == 1).all()
            }
            rows = (
                s.query(
                    Photo.id,
                    Embedding.aesthetic_iqa,
                    AestheticScore.nima_score,
                    AestheticScore.ap25_score,
                    AestheticScore.personal_score,
                )
                .join(Embedding, Embedding.photo_id == Photo.id)
                .outerjoin(AestheticScore, AestheticScore.photo_id == Photo.id)
                .all()
            )

        if not upvoted_ids:
            return {"models": {}, "n_upvotes": 0, "message": "Upvote at least 1 photo."}

        photo_ids = np.array([r[0] for r in rows])
        model_cols = {"iqa": 1, "nima": 2, "ap25": 3, "personal": 4}
        out: dict[str, dict] = {}
        for name, col in model_cols.items():
            vals = np.array([
                r[col] if r[col] is not None else np.nan for r in rows
            ], dtype=float)
            mask = ~np.isnan(vals)
            if mask.sum() < 2:
                out[name] = {"median_upvote_percentile": None, "n_scored_upvotes": 0}
                continue
            v = vals[mask]
            ids_sub = photo_ids[mask]
            order = np.argsort(v)
            pct = np.empty_like(v)
            pct[order] = np.arange(len(v)) / max(1, len(v) - 1) * 100.0
            id_to_pct = dict(zip(ids_sub.tolist(), pct.tolist()))
            upvote_pcts = [id_to_pct[uid] for uid in upvoted_ids if uid in id_to_pct]
            out[name] = {
                "median_upvote_percentile": (
                    float(np.median(upvote_pcts)) if upvote_pcts else None
                ),
                "n_scored_upvotes": len(upvote_pcts),
            }

        # Also report the combined NIMA+AP percentile fusion as if it were a model
        nima_vals = np.array([
            r[model_cols["nima"]] if r[model_cols["nima"]] is not None else np.nan
            for r in rows
        ])
        ap_vals = np.array([
            r[model_cols["ap25"]] if r[model_cols["ap25"]] is not None else np.nan
            for r in rows
        ])
        m_combined = ~(np.isnan(nima_vals) | np.isnan(ap_vals))
        if m_combined.sum() >= 2:
            ids_c = photo_ids[m_combined]
            nima_sub = nima_vals[m_combined]
            ap_sub = ap_vals[m_combined]
            n_o = np.argsort(nima_sub)
            n_pct = np.empty_like(nima_sub)
            n_pct[n_o] = np.arange(len(nima_sub)) / max(1, len(nima_sub) - 1) * 100.0
            a_o = np.argsort(ap_sub)
            a_pct = np.empty_like(ap_sub)
            a_pct[a_o] = np.arange(len(ap_sub)) / max(1, len(ap_sub) - 1) * 100.0
            c_pct = (n_pct + a_pct) / 2.0
            id_to_c = dict(zip(ids_c.tolist(), c_pct.tolist()))
            c_upvote_pcts = [id_to_c[uid] for uid in upvoted_ids if uid in id_to_c]
            out["combined"] = {
                "median_upvote_percentile": (
                    float(np.median(c_upvote_pcts)) if c_upvote_pcts else None
                ),
                "n_scored_upvotes": len(c_upvote_pcts),
            }

        return {"models": out, "n_upvotes": len(upvoted_ids)}

    @app.post("/api/calibrate/retrain")
    def calibrate_retrain():
        """Train a personalized aesthetic signal from upvotes only.

        Method: unit-vector centroid of the SigLIP embeddings of all upvoted
        photos. personal_score for any photo = cosine similarity to that
        centroid. This is a one-class learner — it captures "what your
        upvotes look like" without treating non-upvotes as negative examples.

        Final story curation uses personal_score as a correction on top of
        NIMA+AP combined, not as a replacement.
        """
        import numpy as np

        with session_scope(Session) as s:
            upvoted = (
                s.query(PhotoRating.photo_id, Embedding.siglip)
                .join(Embedding, Embedding.photo_id == PhotoRating.photo_id)
                .filter(PhotoRating.rating == 1)
                .all()
            )
            if len(upvoted) < 3:
                raise HTTPException(
                    400,
                    detail=f"need at least 3 upvotes; have {len(upvoted)}.",
                )

            X_pos = np.stack([
                np.frombuffer(r[1], dtype=np.float16).astype(np.float32)
                for r in upvoted
            ])
            X_pos = X_pos / (np.linalg.norm(X_pos, axis=1, keepdims=True) + 1e-9)
            centroid = X_pos.mean(axis=0)
            centroid = centroid / (np.linalg.norm(centroid) + 1e-9)

            all_rows = s.query(Embedding.photo_id, Embedding.siglip).all()
            X_all = np.stack([
                np.frombuffer(r[1], dtype=np.float16).astype(np.float32)
                for r in all_rows
            ])
            X_all = X_all / (np.linalg.norm(X_all, axis=1, keepdims=True) + 1e-9)
            sims = X_all @ centroid  # [N], range [-1, 1]

            for (pid, _), sim in zip(all_rows, sims):
                existing = s.get(AestheticScore, pid)
                if existing:
                    existing.personal_score = float(sim)
                else:
                    s.add(AestheticScore(photo_id=pid, personal_score=float(sim)))

            mean_pos_sim = float((X_pos @ centroid).mean())

        return {
            "ok": True,
            "method": "centroid_cosine",
            "n_positive": len(upvoted),
            "n_scored": len(all_rows),
            "mean_positive_similarity": mean_pos_sim,
        }

    @app.get("/api/calibrate/dashboard")
    def calibrate_dashboard():
        """Return all photos with all 4 scores + user rating (if any) for the dashboard grid."""
        with session_scope(Session) as s:
            rows = (
                s.query(
                    Photo.id, Photo.sha256, Photo.taken_at,
                    Embedding.aesthetic_iqa,
                    AestheticScore.nima_score, AestheticScore.ap25_score,
                    AestheticScore.personal_score,
                    PhotoRating.rating,
                )
                .join(Embedding, Embedding.photo_id == Photo.id)
                .outerjoin(AestheticScore, AestheticScore.photo_id == Photo.id)
                .outerjoin(PhotoRating, PhotoRating.photo_id == Photo.id)
                .all()
            )
        return {
            "photos": [
                {
                    "photo_id": r[0],
                    "sha256": r[1],
                    "taken_at": r[2].isoformat() if r[2] else None,
                    "thumb_url": f"/api/thumb/{r[1]}",
                    "preview_url": f"/api/preview/{r[1]}",
                    "scores": {
                        "iqa": r[3],
                        "nima": r[4],
                        "ap25": r[5],
                        "personal": r[6],
                    },
                    "rating": r[7],
                }
                for r in rows
            ]
        }


    # ── Best-Of / curated facets ─────────────────────────────────────────────
    @app.get("/api/curate")
    def curate_scope(
        facet: str = Query(..., description="day|place|person|category"),
        value: str = Query(..., description="facet value"),
        limit: int = Query(200, ge=1, le=1000),
        scope_pct: float = Query(None, description="override per-scope percentile gate"),
        library_pct: float = Query(None, description="override library-wide percentile floor"),
    ):
        """Return a curated ranked set of photos for a Best-Of facet.

        Pipeline: scope filter → library-wide top-25% aesthetic gate →
        burst dedup → sort by combined_aesthetic desc.

        Facets:
          - day=YYYY-MM-DD
          - place=NAME           (matches Visit.name)
          - person=N             (Person.id)
          - category=landscape|portrait|object|unclassified
        """
        from selects.ml.curation import curate

        with session_scope(Session) as s:
            # Resolve the scope to a set of photo IDs
            if facet == "day":
                from sqlalchemy import text as _text
                scope_ids = [
                    row[0]
                    for row in s.execute(
                        _text("SELECT id FROM photos WHERE strftime('%Y-%m-%d', taken_at) = :d"),
                        {"d": value},
                    ).fetchall()
                ]
            elif facet == "place":
                # photos at any Visit with this name — by overlapping taken_at
                visits = s.query(Visit).filter(Visit.name == value).all()
                if not visits:
                    return {"facet": facet, "value": value, "total": 0, "photos": []}
                # Photos taken between any visit's arrived_at and departed_at
                scope_ids = []
                for v in visits:
                    rows = (
                        s.query(Photo.id)
                        .filter(Photo.taken_at >= v.arrived_at)
                        .filter(Photo.taken_at <= v.departed_at)
                        .all()
                    )
                    scope_ids.extend(r[0] for r in rows)
                scope_ids = list(set(scope_ids))
            elif facet == "person":
                try:
                    pid_int = int(value)
                except ValueError:
                    raise HTTPException(400, detail="person value must be an integer id")
                rows = s.query(PhotoPerson.photo_id).filter(PhotoPerson.person_id == pid_int).all()
                scope_ids = [r[0] for r in rows]
            elif facet == "category":
                if value not in ("landscape", "portrait", "object", "unclassified"):
                    raise HTTPException(400, detail="unknown category")
                rows = (
                    s.query(PhotoCategory.photo_id)
                    .filter(PhotoCategory.primary_category == value)
                    .all()
                )
                scope_ids = [r[0] for r in rows]
            else:
                raise HTTPException(400, detail=f"unknown facet '{facet}'")

            if not scope_ids:
                return {"facet": facet, "value": value, "total": 0, "photos": []}

            eff_scope = scope_pct if scope_pct is not None else cfg.aesthetic_per_scope_pct
            eff_library = library_pct if library_pct is not None else cfg.aesthetic_library_pct
            curated_list = curate(
                s, scope_ids,
                sort="score",
                ap_w=cfg.ap_weight, nima_w=cfg.nima_weight,
                pct_floor=eff_scope,
                library_pct_floor=eff_library,
            )
            curated_list = curated_list[:limit]

            return {
                "facet": facet,
                "value": value,
                "total": len(curated_list),
                "photos": [
                    {
                        "photo_id": c.photo_id,
                        "sha256": c.sha256,
                        "taken_at": c.taken_at,
                        "thumb_url": f"/api/thumb/{c.sha256}",
                        "preview_url": f"/api/preview/{c.sha256}",
                        "combined": c.combined,
                        "ap25": c.ap25,
                        "nima": c.nima,
                        "moment_id": c.moment_id,
                        "moment_size": c.moment_size,
                    }
                    for c in curated_list
                ],
            }

    @app.patch("/api/moments/{moment_id}/primary")
    def set_moment_primary(moment_id: int, payload: dict = Body(...)):
        """Set the top-of-stack photo for a moment.

        Body: {photo_id: int}

        Persists the user's choice so subsequent curated views surface this
        photo as the visible member of the burst stack.
        """
        photo_id = payload.get("photo_id")
        if not isinstance(photo_id, int):
            raise HTTPException(400, detail="photo_id must be an int")

        with session_scope(Session) as s:
            mom = s.get(Moment, moment_id)
            if mom is None:
                raise HTTPException(404, detail="moment not found")
            member = (
                s.query(MomentMember)
                .filter(MomentMember.moment_id == moment_id)
                .filter(MomentMember.photo_id == photo_id)
                .first()
            )
            if member is None:
                raise HTTPException(
                    400, detail=f"photo_id {photo_id} is not a member of moment {moment_id}",
                )
            mom.primary_photo_id = photo_id

            # Re-order MomentMember.rank so the chosen photo is rank 0
            all_members = (
                s.query(MomentMember)
                .filter(MomentMember.moment_id == moment_id)
                .order_by(MomentMember.rank)
                .all()
            )
            # Pull the chosen one to the front, keep others in their existing order
            new_order = [mm for mm in all_members if mm.photo_id == photo_id]
            new_order.extend(mm for mm in all_members if mm.photo_id != photo_id)
            for rank, mm in enumerate(new_order):
                mm.rank = rank

        return {"ok": True, "moment_id": moment_id, "primary_photo_id": photo_id}

    @app.get("/api/curate/facets")
    def curate_facets():
        """Return available facet values for the Best-Of dropdown."""
        with session_scope(Session) as s:
            # Days with at least one photo
            from sqlalchemy import text as _text
            day_rows = s.execute(_text(
                "SELECT strftime('%Y-%m-%d', taken_at) d, COUNT(*) n FROM photos "
                "WHERE taken_at IS NOT NULL GROUP BY d ORDER BY d"
            )).fetchall()
            # Places: distinct Visit.name with count
            place_rows = (
                s.query(Visit.name, Visit.photo_count)
                .order_by(Visit.photo_count.desc())
                .all()
            )
            # Aggregate place by name
            place_agg: dict[str, int] = {}
            for name, n in place_rows:
                place_agg[name] = place_agg.get(name, 0) + (n or 0)
            # Persons
            from selects.db.models import Person
            persons = s.query(Person.id, Person.label, Person.photo_count).all()
            # Categories
            cat_rows = s.execute(_text(
                "SELECT primary_category, COUNT(*) FROM photo_categories "
                "GROUP BY primary_category"
            )).fetchall()
            return {
                "days": [{"value": d, "count": n} for d, n in day_rows if d],
                "places": [
                    {"value": k, "count": v}
                    for k, v in sorted(place_agg.items(), key=lambda kv: -kv[1])
                ],
                "persons": [
                    {"value": str(pid), "label": label or f"P{pid}", "count": n}
                    for pid, label, n in sorted(persons, key=lambda p: -(p[2] or 0))
                ],
                "categories": [
                    {"value": cat, "count": n}
                    for cat, n in cat_rows if cat
                ],
            }


def _serve_image_for(cfg: FolderConfig, sha256: str, kind: str):
    parent = cfg.thumbs_dir if kind == "thumb" else cfg.previews_dir
    path = parent / f"{sha256}.jpg"
    if not path.exists():
        raise HTTPException(404, detail=f"{kind} not found")
    return FileResponse(path, media_type="image/jpeg")
