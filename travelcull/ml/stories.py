"""Scene clustering and story building from per-day photo sequences."""
from __future__ import annotations

import logging
import math
from collections import defaultdict
from typing import Callable

import numpy as np

from travelcull.config import FolderConfig
from travelcull.db import init_db, session_scope
from travelcull.db.models import ClassicalScore, Embedding, Photo, Story, StoryItem, Visit

log = logging.getLogger(__name__)

# Minimum photos for a day to get its own story
MIN_DAY_PHOTOS = 8
# Max photos per story (final cap) — bumped to let dense days breathe
MAX_STORY_PHOTOS = 30
# Minimum photos for a place to get its own (non-day) story
MIN_PLACE_PHOTOS = 30
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

    from travelcull.db.models import Moment, MomentMember
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
        # Moment dedup: photos that are non-primary members of a moment get filtered.
        # Only the moment.primary_photo_id survives — the burst's representative.
        primary_ids = {m.primary_photo_id for m in s.query(Moment).all()}
        sibling_ids = {
            mm.photo_id
            for mm in s.query(MomentMember).all()
            if mm.photo_id not in primary_ids
        }

    before = len(rows)
    rows = [r for r in rows if r.id not in sibling_ids]
    log.info("story stage: dropped %d moment siblings (1002 -> %d)", before - len(rows), len(rows))

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

    # ── Per-place stories ────────────────────────────────────────────────────
    n_stories += _build_place_stories(cfg, Session, rows, on_progress)
    # ── Per-people stories ───────────────────────────────────────────────────
    n_stories += _build_people_stories(cfg, Session, rows, on_progress)
    # ── Per-pattern stories ──────────────────────────────────────────────────
    n_stories += _build_pattern_stories(cfg, Session, rows, on_progress)

    log.info("story stage: built %d stories total", n_stories)
    return n_stories


def _build_people_stories(cfg, Session, rows, on_progress=None) -> int:
    """Build stories grouped by who's in the photo.

    For a couple trip (top 2 persons dominate), creates:
      - "Just us"      : photos with both dominant persons present
      - "Solo of P1"   : photos with only the top person
      - "Solo of P2"   : photos with only the second person
      - "With others"  : photos with any dominant + a stranger / 3rd person
    """
    from travelcull.db.models import Person, PhotoPerson

    with session_scope(Session) as s:
        person_rows = (
            s.query(Person.id, Person.label, Person.photo_count)
            .order_by(Person.photo_count.desc())
            .limit(2)
            .all()
        )
        if len(person_rows) < 1:
            return 0
        person_membership: dict[int, set[int]] = defaultdict(set)
        for ppid, photo_id in s.query(PhotoPerson.person_id, PhotoPerson.photo_id).all():
            person_membership[photo_id].add(ppid)

    p1_id, p1_label, _ = person_rows[0]
    p2_id, p2_label, _ = (person_rows[1] if len(person_rows) > 1 else (None, None, 0))
    p1_name = p1_label or f"P{p1_id}"
    p2_name = p2_label or (f"P{p2_id}" if p2_id else None)

    buckets: dict[str, list] = defaultdict(list)
    for r in rows:
        ppl = person_membership.get(r.id, set())
        if not ppl:
            continue
        has_p1 = p1_id in ppl
        has_p2 = p2_id is not None and p2_id in ppl
        if has_p1 and has_p2 and len(ppl) == 2:
            buckets["Just us"].append(r)
        elif has_p1 and not has_p2 and len(ppl) == 1:
            buckets[f"Solo of {p1_name}"].append(r)
        elif has_p2 and not has_p1 and len(ppl) == 1:
            buckets[f"Solo of {p2_name}"].append(r)
        elif (has_p1 or has_p2) and len(ppl) >= 3:
            buckets["With others"].append(r)

    return _persist_themed_stories(cfg, Session, buckets, prefix="people:")


def _build_pattern_stories(cfg, Session, rows, on_progress=None) -> int:
    """Stories grouped by what kind of photo it is. Buckets defined by visual tag
    keywords against the existing photo_tags content (any source).
    """
    KEYWORDS = {
        "Indoor moments":       ["interior", "indoor", "inside", "room"],
        "Mountain landscapes":  ["mountain", "snowy", "valley", "summit", "barren", "landscape"],
        "Monastery & shrines":  ["monastery", "temple", "buddhist", "stupa", "shrine"],
        "Food & dining":        ["food", "meal", "dish", "tea", "kitchen", "breakfast"],
        "Wildlife & animals":   ["yak", "animal", "dog", "horse", "sheep", "wildlife"],
        "On the road":          ["road", "transit", "drive", "vehicle", "bus", "car"],
    }
    from travelcull.db.models import PhotoTag
    with session_scope(Session) as s:
        tag_by_photo: dict[int, list[str]] = defaultdict(list)
        for pid, tag in s.query(PhotoTag.photo_id, PhotoTag.tag).all():
            tag_by_photo[pid].append(tag.lower())

    buckets: dict[str, list] = defaultdict(list)
    for r in rows:
        photo_tags = tag_by_photo.get(r.id, [])
        text = " ".join(photo_tags)
        for label, kws in KEYWORDS.items():
            if any(kw in text for kw in kws):
                buckets[label].append(r)
                break
    return _persist_themed_stories(cfg, Session, buckets, prefix="pattern:")


def _persist_themed_stories(cfg, Session, buckets, prefix: str, min_photos: int = 8) -> int:
    """Build a Story per non-empty bucket (with enough photos), reusing the
    scene-clustering + MMR-diversified representatives selection.
    """
    n_built = 0
    for name, members in buckets.items():
        if len(members) < min_photos:
            continue
        members = sorted(members, key=lambda r: r.taken_at)
        scenes = _segment_scenes(members)
        representatives = _pick_representatives(scenes)
        representatives.sort(key=lambda x: x["taken_at"])
        if len(representatives) > MAX_STORY_PHOTOS:
            representatives.sort(key=lambda x: x["score"], reverse=True)
            representatives = representatives[:MAX_STORY_PHOTOS]
            representatives.sort(key=lambda x: x["taken_at"])
        synthetic_day = f"{prefix}{name}"
        title = f"{name} · {len(members)} photos, {len(scenes)} scenes"
        with session_scope(Session) as s:
            story = Story(day=synthetic_day, title=title, photo_count=len(representatives))
            s.add(story)
            s.flush()
            for rank, rep in enumerate(representatives):
                s.add(StoryItem(
                    story_id=story.id,
                    rank=rank,
                    photo_id=rep["photo_id"],
                    scene_label=rep["scene_label"],
                    scene_rank=rep["scene_rank"],
                ))
        n_built += 1
    return n_built


def _build_place_stories(cfg, Session, rows, on_progress=None) -> int:
    """Build one Story per named place with >= MIN_PLACE_PHOTOS photos."""
    with session_scope(Session) as s:
        visit_rows = s.query(Visit.name, Visit.arrived_at, Visit.departed_at).all()

    # Group photos by visit name across the whole trip
    photos_by_place: dict[str, list] = defaultdict(list)
    for r in rows:
        for name, arrived, departed in visit_rows:
            if arrived and departed and arrived <= r.taken_at <= departed:
                photos_by_place[name].append(r)
                break

    eligible = [
        (name, photos)
        for name, photos in photos_by_place.items()
        if len(photos) >= MIN_PLACE_PHOTOS
    ]
    eligible.sort(key=lambda kv: -len(kv[1]))

    n_built = 0
    for pi, (name, photos) in enumerate(eligible):
        if on_progress:
            on_progress(pi + 1, len(eligible), f"place: {name}")
        photos = sorted(photos, key=lambda r: r.taken_at)
        scenes = _segment_scenes(photos)
        representatives = _pick_representatives(scenes)
        representatives.sort(key=lambda x: x["taken_at"])
        if len(representatives) > MAX_STORY_PHOTOS:
            representatives.sort(key=lambda x: x["score"], reverse=True)
            representatives = representatives[:MAX_STORY_PHOTOS]
            representatives.sort(key=lambda x: x["taken_at"])

        title = f"{name} — {len(photos)} photos, {len(scenes)} scenes"
        synthetic_day = f"place:{name}"
        with session_scope(Session) as s:
            story = Story(day=synthetic_day, title=title, photo_count=len(representatives))
            s.add(story)
            s.flush()
            story_id = story.id
            for rank, rep in enumerate(representatives):
                s.add(StoryItem(
                    story_id=story_id,
                    rank=rank,
                    photo_id=rep["photo_id"],
                    scene_label=rep["scene_label"],
                    scene_rank=rep["scene_rank"],
                ))
        n_built += 1
    return n_built


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
    """Pick the best photo(s) per scene, diversified via MMR over SigLIP embeddings.

    Each scene is a list of {photo_id, embedding, iqa, blur, faces_count, ...}.
    Within a scene we score photos on (aesthetic, sharpness, faces) then greedily
    pick by Maximum Marginal Relevance against already-picked: each new pick must
    add visual novelty (penalized by max cosine sim to existing picks). This
    eliminates the "15 nearly-identical Pangong shots in a row" failure mode.
    """
    representatives = []
    all_blur = [it["blur"] for sc in scenes for it in sc if it["blur"] > 0]
    blur_p95 = float(np.percentile(all_blur, 95)) if all_blur else 1.0

    # MMR diversity penalty — 0.0 = pure quality (duplicate-prone), 1.0 = pure
    # spread (ignores quality). Tuned to keep enough variety without losing the
    # best shots from a scene.
    MMR_LAMBDA = 0.45
    # Don't add a photo if its max similarity to any already-picked exceeds this
    # (independent of MMR; absolute floor on duplicate avoidance). Tightened
    # because moment-dedup catches only same-person same-place near-bursts;
    # we still want to filter "two different people at the same wall" pairs.
    DUP_HARD_CEILING = 0.88

    for scene_idx, scene in enumerate(scenes):
        size = len(scene)
        if size == 0:
            continue
        if size >= 60:
            top_n = 15
        elif size >= 30:
            top_n = 10
        elif size >= 15:
            top_n = 6
        elif size >= 8:
            top_n = 3
        else:
            top_n = max(1, size // 3)

        # Pre-score every photo in the scene
        scored_items = []
        for rank_in, it in enumerate(scene):
            blur_norm = min(it["blur"] / blur_p95, 1.0)
            face_bonus = 1.0 if it["faces_count"] > 0 else 0.0
            quality = 0.5 * it["iqa"] + 0.3 * blur_norm + 0.2 * face_bonus
            scored_items.append({"quality": quality, "rank_in": rank_in, "it": it})

        # Sort by quality desc; first pick is the highest-quality photo
        scored_items.sort(key=lambda x: -x["quality"])
        picked: list[dict] = [scored_items[0]]
        remaining = scored_items[1:]

        while len(picked) < top_n and remaining:
            best_idx, best_mmr = -1, -math.inf
            for ri, cand in enumerate(remaining):
                # similarity = max cos against any already-picked
                sim = max(
                    float(np.dot(cand["it"]["embedding"], p["it"]["embedding"]))
                    for p in picked
                )
                if sim >= DUP_HARD_CEILING:
                    continue
                mmr = MMR_LAMBDA * cand["quality"] - (1 - MMR_LAMBDA) * sim
                if mmr > best_mmr:
                    best_mmr, best_idx = mmr, ri
            if best_idx == -1:
                break
            picked.append(remaining.pop(best_idx))

        for entry in picked:
            it = entry["it"]
            representatives.append(
                {
                    **it,
                    "score": entry["quality"],
                    "scene_label": f"scene_{scene_idx + 1}",
                    "scene_rank": entry["rank_in"],
                }
            )
    return representatives


def _day_title(day: str, n_photos: int, n_scenes: int) -> str:
    return f"{day} — {n_photos} photos, {n_scenes} scenes"


def _day_title_with_visits(day: str, n_photos: int, n_scenes: int, visits) -> str:
    """Build a richer title from visited locations.

    Single location:        "Exploring Leh"
    Same start/end, multi:  "Around Leh via Shanti Stupa, Old Town"
    Multi-leg:              "Leh to Pangong Tso via Chang La"
    """
    if not visits:
        return f"{day} · {n_photos} photos"

    names = [v.name for v in visits]
    # Dedupe preserving order
    seen = set()
    unique = [n for n in names if not (n in seen or seen.add(n))]

    if len(unique) == 1:
        route = f"Exploring {unique[0]}"
    elif unique[0] == unique[-1]:
        middles = ", ".join(unique[1:-1]) if len(unique) > 2 else unique[1] if len(unique) == 2 else ""
        route = f"Around {unique[0]}" + (f" via {middles}" if middles else "")
    elif len(unique) == 2:
        route = f"{unique[0]} to {unique[1]}"
    else:
        middles = ", ".join(unique[1:-1])
        route = f"{unique[0]} to {unique[-1]} via {middles}"
    return f"{day} · {route} · {n_photos} photos"
