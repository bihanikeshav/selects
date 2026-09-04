"""Day/people stories: listing, single story, and story export."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Query

from selects.config import FolderConfig
from selects.db import init_db, session_scope
from selects.db.models import Embedding, Photo, PhotoTag, Story, StoryItem, Visit
from selects.util import KEEP_DECISIONS
from selects.server.schemas import (
    StoryItemOut, StoryList, StoryOut, VisitOut,
)

log = logging.getLogger(__name__)


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
    from selects.ml.stories import strip_place_suffix

    parts = []
    for v in visits:
        label = strip_place_suffix(v.name)
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

    from selects.ml.stories import strip_place_suffixes_in_title

    return StoryOut(
        id=st.id,
        day=st.day,
        title=strip_place_suffixes_in_title(st.title),
        photo_count=st.photo_count,
        items=items,
        visits=visits_out,
        cover_url=cover_url,
        itinerary_breadcrumb=breadcrumb,
    )


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


def register_stories_routes(app: FastAPI, cfg: FolderConfig) -> None:
    def Session():
        return init_db(cfg.db_path)()

    @app.get("/api/stories", response_model=StoryList)
    def list_stories(
        include_tags: Optional[str] = Query(None, description="Legacy tag filter"),
        exclude_tags: Optional[str] = Query(None, description="Legacy tag filter"),
        curated: bool = Query(True, description="Apply aesthetic curation pipeline"),
        liked_only: bool = Query(
            False,
            description="Restrict each story to photos the user kept (Swipe.decision in keep/silver)",
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
        from selects.ml.curation import compute_rank_threshold, curate

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

        eff_library = library_pct if library_pct is not None else cfg.aesthetic_library_pct

        with session_scope(Session) as s:
            # The library-wide gate is the same for every story in this
            # response, so resolve it once instead of once per story.
            library_floor = (
                compute_rank_threshold(
                    s, ap_w=cfg.ap_weight, nima_w=cfg.nima_weight, pct_floor=eff_library,
                )
                if curated
                else None
            )
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

                # "Curated only" mode: restrict each story to kept photos.
                if liked_only and items_rows:
                    from selects.db.models import Swipe as _Swipe
                    pids = [_pid(r) for r in items_rows]
                    liked_pids = {
                        r[0]
                        for r in s.query(_Swipe.photo_id)
                        .filter(_Swipe.photo_id.in_(pids))
                        .filter(_Swipe.decision.in_(KEEP_DECISIONS))
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
                    curated_list = curate(
                        s, photo_ids,
                        sort="chronological",
                        ap_w=cfg.ap_weight, nima_w=cfg.nima_weight,
                        pct_floor=eff_scope,
                        library_pct_floor=eff_library,
                        library_threshold=library_floor,
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
