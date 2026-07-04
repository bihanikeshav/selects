from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Optional

from fastapi import Body, FastAPI, HTTPException, Query, Response
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

    @app.get("/api/enhance/{sha256}")
    def enhance(sha256: str):
        """Auto-enhance preview for cull-time before/after comparison.

        Applies a simple Imagen-style boost: gentle auto-levels, +saturation,
        +contrast, +clarity (unsharp mask). Cached per-sha in
        .travelcull/enhanced/<sha>.jpg.
        """
        from io import BytesIO

        from PIL import Image, ImageEnhance, ImageOps

        cached = cfg.state_dir / "enhanced" / f"{sha256}.jpg"
        cached.parent.mkdir(parents=True, exist_ok=True)
        if cached.exists():
            return FileResponse(cached, media_type="image/jpeg")

        preview_path = cfg.previews_dir / f"{sha256}.jpg"
        if not preview_path.exists():
            raise HTTPException(404, detail="preview missing")

        with Image.open(preview_path) as im:
            im = im.convert("RGB")
            # Gentle autocontrast / black-point fix
            im = ImageOps.autocontrast(im, cutoff=1, preserve_tone=True)
            # Color pop
            im = ImageEnhance.Color(im).enhance(1.18)
            # Contrast bump
            im = ImageEnhance.Contrast(im).enhance(1.08)
            # Mild unsharp mask via Sharpness
            im = ImageEnhance.Sharpness(im).enhance(1.4)
            buf = BytesIO()
            im.save(buf, "JPEG", quality=88)
            cached.write_bytes(buf.getvalue())
            return Response(content=buf.getvalue(), media_type="image/jpeg")

    # ── Editor integration ───────────────────────────────────────────────────
    class OpenInEditorReq(BaseModel):
        sha256s: list[str]
        editor: str = "darktable"   # "darktable" | "rawtherapee" | "gimp"

    class SaveEditReq(BaseModel):
        data_url: str           # "data:image/jpeg;base64,..."
        mime: str = "image/jpeg"

    # ── Persons (face identity clusters) ─────────────────────────────────────
    class PersonOut(BaseModel):
        id: int
        label: Optional[str]
        photo_count: int
        cover_url: str

    class PersonList(BaseModel):
        total: int
        persons: list[PersonOut]

    class LabelReq(BaseModel):
        label: Optional[str]

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

        from travelcull.db.models import FaceEmbedding, Person, PhotoPerson

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

        from travelcull.db.models import FaceEmbedding

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
    def label_person(person_id: int, req: LabelReq):
        from travelcull.db.models import Person, Story

        with session_scope(Session) as s:
            person = s.get(Person, person_id)
            if not person:
                raise HTTPException(404, detail="person not found")
            old_label = person.label
            person.label = req.label
            s.flush()

            # Cascade rename to story titles + synthetic_day keys
            new_name = req.label or f"P{person_id}"
            old_name = old_label or f"P{person_id}"
            for story in s.query(Story).filter(Story.day.like("people:%")).all():
                if old_name in story.day or old_name in story.title:
                    story.day = story.day.replace(old_name, new_name)
                    story.title = story.title.replace(old_name, new_name)
                    s.add(story)

        return {"ok": True, "label": req.label}

    @app.get("/api/persons/{person_id}/photos", response_model=PhotoList)
    def person_photos(person_id: int, limit: int = Query(500, le=2000)):
        from travelcull.db.models import PhotoPerson

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
        from travelcull.db.models import Swipe

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

    @app.get("/api/swipes/summary")
    def swipes_summary():
        from sqlalchemy import func

        from travelcull.db.models import Swipe

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
        """Copy a story's photos (in story order) to .travelcull/exports/stories/<title>/.

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

    @app.post("/api/stories/{story_id}/caption")
    def generate_story_caption(story_id: int):
        """Generate an Instagram-ready caption + hashtags for a story via VLM."""
        from travelcull.ml.caption import generate_caption

        try:
            return generate_caption(cfg, story_id)
        except ValueError as e:
            raise HTTPException(404, detail=str(e))
        except Exception as e:
            raise HTTPException(500, detail=f"caption failed: {e}")

    @app.get("/api/search")
    def search(q: str = Query(..., min_length=1), k: int = Query(60, le=300)):
        """Free-text photo search via SigLIP image-text similarity."""
        from travelcull.ml.search import search_photos

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


    class DarktableLaunchReq(BaseModel):
        sha256s: list[str]

    @app.post("/api/edit/darktable")
    def launch_darktable(req: DarktableLaunchReq):
        """Launch darktable with the selected originals in a per-session library.

        Per-session library avoids polluting the user's main catalog and lets
        us round-trip XMP edits cleanly. Darktable writes XMPs next to the
        original file when the user saves — no further coordination needed.
        """
        import shutil
        import subprocess
        import uuid

        editor_cmd = shutil.which("darktable")
        if not editor_cmd:
            raise HTTPException(
                400,
                detail=(
                    "darktable not found on PATH. Install from "
                    "https://www.darktable.org/install/ and add the binary "
                    "to your shell PATH."
                ),
            )

        with session_scope(Session) as s:
            paths = [
                r[0]
                for r in s.query(Photo.path).filter(Photo.sha256.in_(req.sha256s)).all()
            ]
        if not paths:
            raise HTTPException(404, detail="no matching photos")

        session_id = uuid.uuid4().hex[:8]
        lib_dir = cfg.state_dir / "darktable-sessions"
        lib_dir.mkdir(parents=True, exist_ok=True)
        library_path = lib_dir / f"session-{session_id}.db"

        cmd = [editor_cmd, "--library", str(library_path), *paths]
        try:
            subprocess.Popen(
                cmd,
                creationflags=getattr(subprocess, "DETACHED_PROCESS", 0),
                close_fds=True,
            )
        except Exception as exc:
            raise HTTPException(500, detail=f"failed to launch darktable: {exc}")

        return {"opened": len(paths), "session": session_id, "library": str(library_path)}


    class ExportEditsReq(BaseModel):
        sha256s: list[str]
        cluster_name: str = "untitled"
        width: int = 2048
        height: int = 0           # 0 = proportional

    @app.post("/api/edits/export")
    def export_edits(req: ExportEditsReq):
        """Render XMP edits to JPEGs via darktable-cli into
        .travelcull/exports/<cluster>/<timestamp>/.
        """
        import shutil
        import subprocess
        from datetime import datetime

        dt_cli = shutil.which("darktable-cli")
        if not dt_cli:
            raise HTTPException(
                400,
                detail="darktable-cli not found. It ships with darktable; ensure the "
                       "darktable bin directory is on PATH.",
            )

        with session_scope(Session) as s:
            rows = s.query(Photo.sha256, Photo.path).filter(
                Photo.sha256.in_(req.sha256s)
            ).all()
        if not rows:
            raise HTTPException(404, detail="no matching photos")

        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        clean = "".join(c if c.isalnum() or c in "-_" else "_" for c in req.cluster_name) or "untitled"
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
                "--width", str(req.width),
                "--height", str(req.height),
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
    def open_in_editor(req: OpenInEditorReq):
        """Launch the chosen OSS editor with the original photo paths for the
        given sha256s. Editor runs detached — server returns immediately.
        """
        import shutil
        import subprocess

        editor_cmd = shutil.which(req.editor)
        if not editor_cmd:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"'{req.editor}' not found on PATH. Install it or pick a different "
                    "editor (darktable / rawtherapee / gimp)."
                ),
            )

        with session_scope(Session) as s:
            rows = s.query(Photo.path).filter(Photo.sha256.in_(req.sha256s)).all()
            paths = [r[0] for r in rows]
        if not paths:
            raise HTTPException(404, detail="no matching photos")

        try:
            subprocess.Popen(
                [editor_cmd, *paths],
                creationflags=getattr(subprocess, "DETACHED_PROCESS", 0),
                close_fds=True,
            )
        except Exception as exc:
            raise HTTPException(500, detail=f"failed to launch {req.editor}: {exc}")
        return {"opened": len(paths), "editor": req.editor}


def _serve_image_for(cfg: FolderConfig, sha256: str, kind: str):
    parent = cfg.thumbs_dir if kind == "thumb" else cfg.previews_dir
    path = parent / f"{sha256}.jpg"
    if not path.exists():
        raise HTTPException(404, detail=f"{kind} not found")
    return FileResponse(path, media_type="image/jpeg")
