"""Compose thematic clusters from structured metadata.

Replaces pure-visual HDBSCAN+VLM-naming with a rule-driven composition
over what we already know about each photo: location (Visit), persons,
faces, time-of-day, aesthetic score, capture moment.

Why: pure visual clustering produced 66 groups with overlapping fuzzy
names like "snowy mountains 3", "mountain journey", "winter landscape 7".
Useful for nothing. The metadata-driven approach yields ~12-15 clusters
with literal, useful labels: "Pangong Tso", "Hemis Monastery", "Yaks &
wildlife", "Golden hour", "Indoor scenes", etc.

Algorithm:
  1. Location buckets — every Visit with photo_count >= MIN_LOCATION
     becomes its own cluster, named with the Visit.name.
  2. Cross-cut buckets (orthogonal to location, never both):
       - "Food"            : photos whose primary visual tag includes
                             a food concept (kept as 2nd source)
       - "Yaks & wildlife" : any cluster name containing animal/yak
       - "Golden hour"     : taken_at in 16:30-19:30
       - "Night"           : taken_at in 20:00-23:59
       - "Indoor"          : visual tag includes "interior" or "indoor"
       - "People moments"  : faces_count >= 2 AND any labeled person
       - "Just us"         : both dominant persons present (couple trips)
       - "On the road"     : visual tag mentions "road" or "transit"
  3. Photos that don't match any cross-cut and aren't in a top-N location
     get bucketed into "Other moments" so nothing disappears.

For each cluster we cap at MAX_PHOTOS_PER_CLUSTER members ordered by
aesthetic_iqa desc — this lets the UI show a tight, high-quality grid.
"""
from __future__ import annotations

import logging
import re
from collections import defaultdict
from datetime import time
from typing import Callable

from sqlalchemy import select

from travelcull.config import FolderConfig
from travelcull.db import init_db, session_scope
from travelcull.db.models import (
    ClassicalScore,
    Embedding,
    Person,
    PhotoPerson,
    PhotoTag,
    Photo,
    Visit,
)

log = logging.getLogger(__name__)

MIN_LOCATION_PHOTOS = 10        # only surface visits with ≥ this many photos
TOP_LOCATIONS = 12               # cap how many location clusters we surface
MAX_PHOTOS_PER_CLUSTER = 120     # don't blow out cluster cards
MERGE_KM = 5.0                   # collapse named visits within this radius into one cluster
GOLDEN_HOUR = (time(16, 30), time(19, 30))
NIGHT = (time(20, 0), time(23, 59))
COUPLE_BOTH_DOMINANT_THRESHOLD = 0.05   # 5% of photos => "dominant"

# The theme keyword taxonomy (formerly a set of divergent Ladakh-specific lists
# duplicated from stories.py) now lives in trip_data.DEFAULT_KEYWORDS and is
# loaded per-library via trip_data.load_keywords(cfg).


def run_date_stage(
    cfg: FolderConfig,
    on_progress: Callable[[int, int, str], None] | None = None,
) -> int:
    """Simplest possible clustering: one cluster per calendar date.

    Fallback when smart clustering produces nonsense. Always works because
    every photo has a taken_at timestamp.
    """
    Session = init_db(cfg.db_path)

    with session_scope(Session) as s:
        rows = s.execute(
            select(Photo.id, Photo.taken_at, ClassicalScore.auto_reject, Embedding.aesthetic_iqa)
            .outerjoin(ClassicalScore, ClassicalScore.photo_id == Photo.id)
            .outerjoin(Embedding, Embedding.photo_id == Photo.id)
            .where(Photo.taken_at.is_not(None))
        ).all()

    clusters: dict[str, list[tuple[int, float]]] = defaultdict(list)
    for p in rows:
        if p.auto_reject:
            continue
        day = p.taken_at.date().isoformat()
        clusters[day].append((p.id, float(p.aesthetic_iqa or 0.0)))

    # cap each day
    for day, members in clusters.items():
        members.sort(key=lambda kv: -kv[1])
        clusters[day] = members[:MAX_PHOTOS_PER_CLUSTER]

    with session_scope(Session) as s:
        s.query(PhotoTag).filter(PhotoTag.source == "date").delete(synchronize_session=False)
        s.flush()
        for day, members in clusters.items():
            for photo_id, score in members:
                s.add(PhotoTag(photo_id=photo_id, tag=day, score=score, source="date"))
    return len(clusters)


def run_thematic_stage(
    cfg: FolderConfig,
    on_progress: Callable[[int, int, str], None] | None = None,
) -> int:
    """Rebuild photo_tags with source='thematic' from metadata-derived clusters."""
    Session = init_db(cfg.db_path)

    with session_scope(Session) as s:
        # Load photo metadata
        photos = s.execute(
            select(
                Photo.id,
                Photo.taken_at,
                Photo.sha256,
                ClassicalScore.faces_count,
                ClassicalScore.auto_reject,
                Embedding.aesthetic_iqa,
            )
            .outerjoin(ClassicalScore, ClassicalScore.photo_id == Photo.id)
            .outerjoin(Embedding, Embedding.photo_id == Photo.id)
        ).all()

        # Pre-existing visual tags (used as semantic hints for cross-cut routing)
        visual_tag_for: dict[int, str] = {}
        for pid, tag in s.query(PhotoTag.photo_id, PhotoTag.tag).all():
            # The first tag we see per photo wins as the "primary" hint
            visual_tag_for.setdefault(pid, tag.lower())

        # Visit time windows (photos taken_at within a visit's [arrived, departed])
        visits = s.query(Visit.name, Visit.arrived_at, Visit.departed_at).all()

        # Persons present per photo
        person_membership: dict[int, set[int]] = defaultdict(set)
        for pid, ppid in s.query(PhotoPerson.photo_id, PhotoPerson.person_id).all():
            person_membership[pid].add(ppid)

        # Top dominant persons (by photo_count) — the "couple"
        dominant_persons = [
            row[0]
            for row in s.query(Person.id, Person.photo_count)
            .order_by(Person.photo_count.desc())
            .limit(2)
            .all()
        ]

    # ── Merge near-duplicate visit names within MERGE_KM ─────────────────────
    # Get visit coords from the visits table
    import math
    from travelcull.db.models import Visit as VisitTable
    with session_scope(Session) as s:
        visit_coords_raw = s.query(VisitTable.name, VisitTable.lat, VisitTable.lon).all()
    # Take first centroid per name
    visit_centroid: dict[str, tuple[float, float]] = {}
    for name, lat, lon in visit_coords_raw:
        if name not in visit_centroid and lat is not None and lon is not None:
            visit_centroid[name] = (lat, lon)

    def km_between(a: tuple[float, float], b: tuple[float, float]) -> float:
        # equirectangular approx
        dx = (a[1] - b[1]) * math.cos(math.radians((a[0] + b[0]) / 2))
        dy = a[0] - b[0]
        return math.sqrt(dx * dx + dy * dy) * 111.0

    # Canonical name selection: for any pair of visits within MERGE_KM,
    # collapse to the better-named one. Better = (in this priority):
    #   1. Has a landmark keyword (Monastery / Stupa / Tso / Pass / Dunes / Valley)
    #   2. Has the higher photo_count in visits (more prominent on the trip)
    #   3. Is the longer name (more specific)
    visit_photo_counts: dict[str, int] = defaultdict(int)
    with session_scope(Session) as s:
        for name, n in s.query(VisitTable.name, VisitTable.photo_count).all():
            visit_photo_counts[name] += n or 0

    LANDMARK_KWS = ("Monastery", "Stupa", "Tso", "Pass", "Dunes", "Valley")

    def name_score(n: str) -> tuple[int, int, int]:
        # Higher tuple = better. (landmark, photo_count, name_length)
        has_landmark = 1 if any(k in n for k in LANDMARK_KWS) else 0
        return (has_landmark, visit_photo_counts.get(n, 0), len(n))

    names_sorted = sorted(visit_centroid.keys())
    canonical: dict[str, str] = {n: n for n in names_sorted}
    for i, n1 in enumerate(names_sorted):
        for n2 in names_sorted[i + 1:]:
            if canonical[n2] != n2 or canonical[n1] != n1:
                continue
            if km_between(visit_centroid[n1], visit_centroid[n2]) <= MERGE_KM:
                # Loser gets remapped to the better-scoring name
                winner = n1 if name_score(n1) >= name_score(n2) else n2
                loser = n2 if winner == n1 else n1
                canonical[loser] = winner

    # ── Index visits by name and aggregate per-photo location ────────────────
    photo_location: dict[int, str] = {}
    visit_counts: dict[str, int] = defaultdict(int)
    sorted_visits = sorted(visits, key=lambda v: v[1] or time())  # arrived_at
    for p in photos:
        if not p.taken_at:
            continue
        for name, arrived, departed in sorted_visits:
            if arrived and departed and arrived <= p.taken_at <= departed:
                merged = canonical.get(name, name)
                photo_location[p.id] = merged
                visit_counts[merged] += 1
                break  # first containing visit wins

    # Top location buckets
    top_locations = [
        name for name, count in sorted(visit_counts.items(), key=lambda kv: -kv[1])
        if count >= MIN_LOCATION_PHOTOS
    ][:TOP_LOCATIONS]
    log.info("top locations: %s", top_locations)

    # ── Build clusters ───────────────────────────────────────────────────────
    clusters: dict[str, list[tuple[int, float]]] = defaultdict(list)
    has_cluster: set[int] = set()

    def add(label: str, photo_id: int, score: float):
        clusters[label].append((photo_id, score))
        has_cluster.add(photo_id)

    couple_set = set(dominant_persons) if len(dominant_persons) == 2 else set()

    for p in photos:
        if p.auto_reject:
            continue
        score = float(p.aesthetic_iqa or 0.0)
        loc = photo_location.get(p.id)
        if loc and loc in top_locations:
            add(loc, p.id, score)

    # Photos that didn't land in a named location are left untagged; the
    # /api/clusters route surfaces them as a synthetic "Uncategorized" bucket.
    # No need to double-count as "Other moments".

    # Cap and order each cluster
    for label, members in clusters.items():
        members.sort(key=lambda kv: -kv[1])
        clusters[label] = members[:MAX_PHOTOS_PER_CLUSTER]

    if on_progress:
        on_progress(1, 2, "writing tags")

    # ── Persist ──────────────────────────────────────────────────────────────
    with session_scope(Session) as s:
        # Wipe prior thematic tags (keep visual/ram/etc. if they exist)
        s.query(PhotoTag).filter(PhotoTag.source == "thematic").delete(
            synchronize_session=False
        )
        s.flush()
        for label, members in clusters.items():
            for photo_id, score in members:
                s.add(PhotoTag(
                    photo_id=photo_id,
                    tag=label,
                    score=float(score),
                    source="thematic",
                ))

    if on_progress:
        on_progress(2, 2, "done")
    return len(clusters)
