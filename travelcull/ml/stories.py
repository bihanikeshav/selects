"""Scene clustering and story building from per-day photo sequences."""
from __future__ import annotations

import logging
from collections import defaultdict
from typing import Callable

import numpy as np

from travelcull.config import FolderConfig
from travelcull.db import init_db, session_scope
from travelcull.db.models import ClassicalScore, Embedding, Photo, Story, StoryItem, Visit

log = logging.getLogger(__name__)

# Minimum photos for a day to get its own story
MIN_DAY_PHOTOS = 10
# Max photos per story (final cap)
MAX_STORY_PHOTOS = 15
# Time gap (seconds) that splits scenes when visual similarity is also low
SCENE_TIME_GAP_S = 600  # 10 minutes
# Cosine similarity threshold: above this, same scene; below + time gap, new scene
SCENE_SIM_THRESHOLD = 0.75


def run_story_stage(
    cfg: FolderConfig,
    on_progress: Callable[[int, int, str], None] | None = None,
) -> int:
    """Build per-day Story rows from photos with embeddings + classical scores.

    Idempotent: drops + rebuilds Stories on each run.
    Returns count of stories built.
    """
    Session = init_db(cfg.db_path)

    with session_scope(Session) as s:
        rows = (
            s.query(
                Photo.id,
                Photo.taken_at,
                Photo.sha256,
                Photo.gps_lat,
                Photo.gps_lon,
                ClassicalScore.blur,
                ClassicalScore.faces_count,
                ClassicalScore.auto_reject,
                Embedding.siglip,
                Embedding.aesthetic_iqa,
            )
            .join(Embedding, Embedding.photo_id == Photo.id)
            .outerjoin(ClassicalScore, ClassicalScore.photo_id == Photo.id)
            .filter(Photo.taken_at.is_not(None))
            .order_by(Photo.taken_at)
            .all()
        )

    # Skip auto-rejected
    rows = [r for r in rows if not (r.auto_reject or False)]

    # Group by day
    by_day: dict[str, list] = defaultdict(list)
    for r in rows:
        day = r.taken_at.date().isoformat()
        by_day[day].append(r)

    # Wipe old stories (Visit rows cascade-delete via FK)
    with session_scope(Session) as s:
        s.query(StoryItem).delete()
        s.query(Visit).delete()
        s.query(Story).delete()

    eligible_days = [
        (day, photos)
        for day, photos in sorted(by_day.items())
        if len(photos) >= MIN_DAY_PHOTOS
    ]
    n_stories = 0
    total_days = len(eligible_days)

    # Import here to avoid circular import at module load time
    from travelcull.ml.locations import build_visits_for_day

    for di, (day, photos) in enumerate(eligible_days):
        if on_progress:
            on_progress(di + 1, total_days, day)
        scenes = _segment_scenes(photos)
        representatives = _pick_representatives(scenes)
        # Order chronologically, cap to MAX_STORY_PHOTOS keeping strongest if over cap
        representatives.sort(key=lambda x: x["taken_at"])
        if len(representatives) > MAX_STORY_PHOTOS:
            representatives.sort(key=lambda x: x["score"], reverse=True)
            representatives = representatives[:MAX_STORY_PHOTOS]
            representatives.sort(key=lambda x: x["taken_at"])

        # Build GPS-grounded visits for this day
        # Prepare photo dicts with GPS data (from the original DB rows)
        photo_dicts = [
            {
                "photo_id": r.id,
                "taken_at": r.taken_at,
                "gps_lat": r.gps_lat,
                "gps_lon": r.gps_lon,
                "aesthetic_iqa": r.aesthetic_iqa or 0.0,
            }
            for r in photos
        ]
        visit_data_list = build_visits_for_day(0, photo_dicts, Session)  # story_id filled in loop below

        # Build itinerary title from first/last visit
        title = _day_title_with_visits(day, len(photos), len(scenes), visit_data_list)

        with session_scope(Session) as s:
            story = Story(
                day=day,
                title=title,
                photo_count=len(representatives),
            )
            s.add(story)
            s.flush()
            story_id = story.id
            for rank, rep in enumerate(representatives):
                s.add(
                    StoryItem(
                        story_id=story_id,
                        rank=rank,
                        photo_id=rep["photo_id"],
                        scene_label=rep["scene_label"],
                        scene_rank=rep["scene_rank"],
                    )
                )
            # Insert Visit rows
            for vd in visit_data_list:
                s.add(Visit(
                    story_id=story_id,
                    rank=vd.rank,
                    name=vd.name,
                    summary=vd.summary,
                    lat=vd.lat,
                    lon=vd.lon,
                    elevation_m=vd.elevation_m,
                    arrived_at=vd.arrived_at,
                    departed_at=vd.departed_at,
                    photo_count=vd.photo_count,
                    cover_photo_id=vd.cover_photo_id,
                ))
        n_stories += 1

    log.info("story stage: built %d stories from %d eligible days", n_stories, total_days)
    return n_stories


def _segment_scenes(photos: list) -> list[list[dict]]:
    """Group adjacent photos into scenes based on time gap + visual similarity.

    photos is a list of row tuples from the query; already sorted chronologically.
    Returns a list of scenes; each scene is a list of dicts with photo metadata.
    """
    items = []
    for r in photos:
        emb = np.frombuffer(r.siglip, dtype=np.float16).astype(np.float32)
        norm = np.linalg.norm(emb)
        emb = emb / max(norm, 1e-6)
        items.append(
            {
                "photo_id": r.id,
                "taken_at": r.taken_at,
                "sha256": r.sha256,
                "blur": r.blur or 0.0,
                "faces_count": r.faces_count or 0,
                "embedding": emb,
                "iqa": r.aesthetic_iqa or 0.0,
            }
        )

    scenes: list[list[dict]] = []
    current: list[dict] = []
    for it in items:
        if not current:
            current = [it]
            continue
        prev = current[-1]
        dt = (it["taken_at"] - prev["taken_at"]).total_seconds()
        sim = float(np.dot(it["embedding"], prev["embedding"]))
        if dt > SCENE_TIME_GAP_S and sim < SCENE_SIM_THRESHOLD:
            scenes.append(current)
            current = [it]
        else:
            current.append(it)
    if current:
        scenes.append(current)
    return scenes


def _pick_representatives(scenes: list[list[dict]]) -> list[dict]:
    """Pick the best photo(s) from each scene by composite score."""
    representatives = []
    # Compute global blur normalization across all photos in all scenes
    all_blur = [it["blur"] for sc in scenes for it in sc if it["blur"] > 0]
    blur_p95 = float(np.percentile(all_blur, 95)) if all_blur else 1.0

    for scene_idx, scene in enumerate(scenes):
        scored = []
        for rank_in, it in enumerate(scene):
            blur_norm = min(it["blur"] / blur_p95, 1.0)
            face_bonus = 1.0 if it["faces_count"] > 0 else 0.0
            score = 0.5 * it["iqa"] + 0.3 * blur_norm + 0.2 * face_bonus
            scored.append((score, rank_in, it))
        scored.sort(reverse=True)
        # Pick top-1 per scene; if scene has >= 8 photos and quality varies, pick top-2
        top_n = 2 if len(scene) >= 8 else 1
        for s, rk, it in scored[:top_n]:
            representatives.append(
                {
                    **it,
                    "score": s,
                    "scene_label": f"scene_{scene_idx + 1}",
                    "scene_rank": rk,
                }
            )
    return representatives


def _day_title(day: str, n_photos: int, n_scenes: int) -> str:
    return f"{day} — {n_photos} photos, {n_scenes} scenes"


def _day_title_with_visits(day: str, n_photos: int, n_scenes: int, visits) -> str:
    """Build a richer title incorporating first/last visit location names."""
    if not visits:
        return _day_title(day, n_photos, n_scenes)
    names = [v.name for v in visits]
    if len(names) == 1:
        route = names[0]
    else:
        route = f"{names[0]} to {names[-1]}"
    return f"{day} — {route} · {n_photos} photos, {n_scenes} scenes"
