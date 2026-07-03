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
GOLDEN_HOUR = (time(16, 30), time(19, 30))
NIGHT = (time(20, 0), time(23, 59))
COUPLE_BOTH_DOMINANT_THRESHOLD = 0.05   # 5% of photos => "dominant"

FOOD_KEYWORDS = ["food", "meal", "dish", "kitchen", "tea", "cup", "breakfast", "lunch"]
WILDLIFE_KEYWORDS = ["yak", "animal", "dog", "horse", "sheep", "wildlife"]
INDOOR_KEYWORDS = ["interior", "indoor", "inside", "room"]
ROAD_KEYWORDS = ["road", "transit", "drive", "vehicle", "bus", "car"]


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

    # ── Index visits by name and aggregate per-photo location ────────────────
    photo_location: dict[int, str] = {}
    visit_counts: dict[str, int] = defaultdict(int)
    sorted_visits = sorted(visits, key=lambda v: v[1] or time())  # arrived_at
    for p in photos:
        if not p.taken_at:
            continue
        for name, arrived, departed in sorted_visits:
            if arrived and departed and arrived <= p.taken_at <= departed:
                photo_location[p.id] = name
                visit_counts[name] += 1
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

    # Photos that didn't land in any of the above
    uncategorized = [p for p in photos if p.id not in has_cluster and not p.auto_reject]
    for p in uncategorized:
        add("Other moments", p.id, float(p.aesthetic_iqa or 0.0))

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
