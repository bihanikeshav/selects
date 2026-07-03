"""Detect trip type from person clustering signals.

Categories:
  solo    : one person dominates, no other person above the floor
  couple  : exactly two persons dominate together
  group   : 3-6 dominant persons
  event   : 7+ persons (wedding, conference, big family gathering)
  unknown : no faces or insufficient signal
"""
from __future__ import annotations

from dataclasses import dataclass

from travelcull.config import FolderConfig
from travelcull.db import init_db, session_scope
from travelcull.db.models import Person, Photo


@dataclass
class TripType:
    kind: str            # solo | couple | group | event | unknown
    dominant_count: int
    dominant_persons: list[int]  # person IDs sorted by photo_count desc
    total_persons: int
    total_photos_with_faces: int
    rationale: str


def classify_trip(cfg: FolderConfig) -> TripType:
    Session = init_db(cfg.db_path)

    with session_scope(Session) as s:
        total_photos = s.query(Photo).count()
        persons = (
            s.query(Person.id, Person.photo_count)
            .order_by(Person.photo_count.desc())
            .all()
        )

    if not persons or total_photos == 0:
        return TripType(
            kind="unknown",
            dominant_count=0,
            dominant_persons=[],
            total_persons=0,
            total_photos_with_faces=0,
            rationale="no persons or photos",
        )

    # "Dominant" = person appears in >= 5% of all photos
    floor = max(3, int(total_photos * 0.05))
    dominant = [(pid, n) for pid, n in persons if n >= floor]

    photos_with_faces_total = sum(n for _, n in persons)

    if len(dominant) == 0:
        kind = "unknown"
        rationale = (
            f"{len(persons)} identities but none in 5%+ of {total_photos} photos"
        )
    elif len(dominant) == 1:
        kind = "solo"
        rationale = f"P{dominant[0][0]} appears in {dominant[0][1]} photos; no other above floor"
    elif len(dominant) == 2:
        kind = "couple"
        rationale = (
            f"two dominant identities: P{dominant[0][0]} ({dominant[0][1]}p) "
            f"+ P{dominant[1][0]} ({dominant[1][1]}p)"
        )
    elif len(dominant) <= 6:
        kind = "group"
        rationale = f"{len(dominant)} dominant identities; group trip"
    else:
        kind = "event"
        rationale = f"{len(dominant)} dominant identities (event/family/wedding)"

    return TripType(
        kind=kind,
        dominant_count=len(dominant),
        dominant_persons=[pid for pid, _ in dominant],
        total_persons=len(persons),
        total_photos_with_faces=photos_with_faces_total,
        rationale=rationale,
    )
