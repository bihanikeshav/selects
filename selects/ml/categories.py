"""Assign a coarse primary_category (portrait / landscape / object / unclassified)."""
from __future__ import annotations

import logging
from collections import defaultdict
from typing import Callable, Iterable

from selects.config import FolderConfig
from selects.db import init_db, session_scope
from selects.db.models import ClassicalScore, Photo, PhotoCategory, PhotoTag

log = logging.getLogger(__name__)

LANDSCAPE_WORDS = (
    "mountain",
    "landscape",
    "lake",
    "valley",
    "glacier",
    "desert",
    "ocean",
    "beach",
    "sky",
    "sunset",
    "sunrise",
    "forest",
)


def assign_primary_category(faces_count: int | None, tags: Iterable[str]) -> str:
    if (faces_count or 0) >= 1:
        return "portrait"
    lowered = [t.lower() for t in tags if t]
    if any(any(word in t for word in LANDSCAPE_WORDS) for t in lowered):
        return "landscape"
    if lowered:
        return "object"
    return "unclassified"


def run_category_stage(
    cfg: FolderConfig,
    on_progress: Callable[[int, int, str], None] | None = None,
) -> int:
    """Wipe and rewrite PhotoCategory rows. Returns the number of photos classified."""
    Session = init_db(cfg.db_path)
    with session_scope(Session) as s:
        rows = (
            s.query(Photo.id, ClassicalScore.faces_count)
            .outerjoin(ClassicalScore, ClassicalScore.photo_id == Photo.id)
            .order_by(Photo.id)
            .all()
        )
        tags_by_photo: dict[int, list[str]] = defaultdict(list)
        for pid, tag in s.query(PhotoTag.photo_id, PhotoTag.tag).all():
            tags_by_photo[pid].append(tag)

        s.query(PhotoCategory).delete()
        total = len(rows)
        n = 0
        for i, (photo_id, faces_count) in enumerate(rows):
            if on_progress:
                on_progress(i + 1, total, str(photo_id))
            category = assign_primary_category(faces_count, tags_by_photo.get(photo_id, []))
            s.add(PhotoCategory(photo_id=photo_id, primary_category=category))
            n += 1
        log.info("category stage: classified %d photos", n)
        return n
