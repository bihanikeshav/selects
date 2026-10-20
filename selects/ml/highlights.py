"""Suggest a bounded set of photos using quality and metadata overlap."""
from __future__ import annotations

import math
from collections import defaultdict

from sqlalchemy.orm import Session

from selects.db.models import ClassicalScore, Photo, PhotoCategory, PhotoPerson, StoryItem, Swipe
from selects.ml.curation import CuratedPhoto
from selects.util import KEEP_DECISIONS, chunked
from selects.selection import choose_diverse


def eligible_photo_ids(s: Session, ids: list[int]) -> list[int]:
    """Filter before percentile gating and burst collapse so alternatives survive."""
    eligible = []
    for batch in chunked(sorted(set(ids))):
        rows = (
            s.query(Photo.id, ClassicalScore.auto_reject, Swipe.decision)
            .outerjoin(ClassicalScore, ClassicalScore.photo_id == Photo.id)
            .outerjoin(Swipe, Swipe.photo_id == Photo.id)
            .filter(Photo.id.in_(batch)).order_by(Photo.id).all()
        )
        eligible.extend(pid for pid, reject, decision in rows
                        if decision != "reject" and (not reject or decision in KEEP_DECISIONS))
    return eligible


def select_highlights(
    s: Session, candidates: list[CuratedPhoto], budget: int, *, diverse: bool = True,
) -> tuple[list[CuratedPhoto], dict[int, list[str]], int]:
    """Select up to budget unique suggestions, evidence and eligible count.

    Only existing candidates participate. Never fill a budget by promoting
    rejected photos. User keeps override technical rejection; their stored
    decisions remain unchanged by this temporary shortlist.
    """
    if budget < 1:
        raise ValueError("budget must be positive")
    dimensions: dict[int, set[tuple[str, str]]] = defaultdict(set)
    reasons: dict[int, list[str]] = {}
    eligible = {}
    by_id = {c.photo_id: c for c in candidates}
    for batch in chunked(sorted(by_id)):
        rows = (
            s.query(Photo, ClassicalScore, Swipe.decision, PhotoCategory.primary_category)
            .outerjoin(ClassicalScore, ClassicalScore.photo_id == Photo.id)
            .outerjoin(Swipe, Swipe.photo_id == Photo.id)
            .outerjoin(PhotoCategory, PhotoCategory.photo_id == Photo.id)
            .filter(Photo.id.in_(batch)).order_by(Photo.id).all()
        )
        for p, tech, decision, category in rows:
            if decision == "reject" or (tech and tech.auto_reject and decision not in KEEP_DECISIONS):
                continue
            c = by_id[p.id]
            eligible[p.id] = c
            why = []
            if decision in KEEP_DECISIONS:
                why.append("You marked this as a keeper")
            if c.combined is not None:
                why.append("Passed the quality selection for this scope")
            else:
                why.append("Quality score unavailable; included for review")
            if c.moment_size > 1:
                why.append(f"Representative of a burst of {c.moment_size}")
            if tech and tech.eyes_open_ratio is not None and tech.eyes_open_ratio >= 1:
                why.append("Detected faces have an eyes-open signal")
            reasons[p.id] = why
            if p.taken_at:
                dimensions[p.id].add(("day", p.taken_at.date().isoformat()))
            if category and category != "unclassified":
                dimensions[p.id].add(("category", category))
            if p.gps_lat is not None and p.gps_lon is not None:
                dimensions[p.id].add(("location", f"{p.gps_lat:.2f},{p.gps_lon:.2f}"))
        for pid, person in s.query(PhotoPerson.photo_id, PhotoPerson.person_id).filter(PhotoPerson.photo_id.in_(batch)):
            dimensions[pid].add(("person", str(person)))
        for pid, story, scene in s.query(StoryItem.photo_id, StoryItem.story_id, StoryItem.scene_label).filter(StoryItem.photo_id.in_(batch)):
            dimensions[pid].add(("story", str(story)))
            if scene:
                dimensions[pid].add(("scene", f"{story}:{scene}"))

    def quality(c):
        score = c.final if c.final is not None else c.combined
        return float(score) if score is not None and math.isfinite(score) else 0.0

    unique = []
    hashes, moments = set(), set()
    for c in sorted(eligible.values(), key=lambda c: (-quality(c), c.photo_id)):
        if (c.sha256 and c.sha256 in hashes) or (c.moment_id is not None and c.moment_id in moments):
            continue
        unique.append(c)
        if c.sha256:
            hashes.add(c.sha256)
        if c.moment_id is not None:
            moments.add(c.moment_id)
    available = len(unique)
    selected = choose_diverse(
        unique, budget, score=quality,
        features=lambda c: frozenset(dimensions[c.photo_id]),
        identity=lambda c: c.photo_id, penalty=.12 if diverse else 0.0)
    covered = set()
    labels = dict(day="day", category="subject type", location="place",
                  person="person", story="story", scene="scene")
    if diverse:
        for candidate in selected:
            keys = dimensions[candidate.photo_id]
            added = sorted({axis for axis, value in keys - covered})
            if added:
                reasons[candidate.photo_id].append(
                    "Adds coverage: " + ", ".join(labels[axis] for axis in added))
            covered.update(keys)
    return selected, {c.photo_id: reasons[c.photo_id] for c in selected}, available
