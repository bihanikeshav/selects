"""Natural-language story composition.

Parses a free-text query against the structured photo metadata pack
(persons, tags, locations, time-of-day, aesthetic score) and builds
an ad-hoc story.

For v1 the query parser is a deterministic keyword matcher — fast,
explainable, no model required. The query string is matched against:
  - person labels (after user has labeled persons; falls back to "P1"/"P2")
  - the trip's known visit names (from Visit table)
  - the cluster tags from photo_tags
  - time-of-day cues ("golden hour", "sunset", "morning", "night")
  - aesthetic gates ("best", "favorite", "top")

The returned shape mirrors a normal Story so the frontend can reuse the
same rendering.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import time
from typing import Iterable

import numpy as np

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


TIME_OF_DAY = {
    "golden hour": (time(16, 30), time(19, 30)),
    "sunset":      (time(17, 30), time(19, 30)),
    "morning":     (time(5, 0), time(11, 0)),
    "midday":      (time(11, 0), time(15, 0)),
    "afternoon":   (time(13, 0), time(17, 0)),
    "evening":     (time(17, 0), time(21, 0)),
    "night":       (time(20, 0), time(23, 59)),
}


@dataclass
class StoryRequest:
    persons: list[int] = field(default_factory=list)        # required person IDs (AND)
    any_persons: list[int] = field(default_factory=list)    # any of these (OR)
    tags: list[str] = field(default_factory=list)           # required tags
    location_names: list[str] = field(default_factory=list)
    time_of_day: str | None = None
    min_aesthetic: float = 0.0
    only_best: bool = False                                 # top-N by score
    max_photos: int = 24


@dataclass
class NLStoryPhoto:
    photo_id: int
    sha256: str
    thumb_url: str
    preview_url: str
    taken_at: str | None
    score: float


@dataclass
class NLStory:
    query: str
    parsed: StoryRequest
    items: list[NLStoryPhoto]
    rationale: str


# ─── parser ───────────────────────────────────────────────────────────────────

def parse_query(cfg: FolderConfig, query: str) -> StoryRequest:
    """Turn a free-text query into a StoryRequest using DB-known labels."""
    Session = init_db(cfg.db_path)
    q = query.lower().strip()
    req = StoryRequest()

    with session_scope(Session) as s:
        persons = s.query(Person.id, Person.label).all()
        visits = s.query(Visit.name).distinct().all()
        tag_names = [
            r[0] for r in s.query(PhotoTag.tag).distinct().all()
        ]

    # Persons — match labeled person names, also accept "me" / "us" / "P<id>"
    for pid, label in persons:
        if label and label.lower() in q:
            req.persons.append(pid)
        if re.search(rf"\bp{pid}\b", q):
            req.persons.append(pid)
    if "me" in q.split() or "myself" in q.split() or " i " in f" {q} ":
        # treat the largest-photo-count person as "me" unless an explicit label matched
        if not req.persons and persons:
            req.persons.append(persons[0][0])
    if "us" in q.split() or "together" in q.split():
        # "us" → top two persons
        if not req.persons:
            for pid, _ in persons[:2]:
                req.persons.append(pid)

    # Locations — exact substring match against visit names
    for (name,) in visits:
        if name and name.lower() in q:
            req.location_names.append(name)

    # Tags — substring match against any known tag
    for tag in tag_names:
        if tag and tag.lower() in q:
            req.tags.append(tag)

    # Time of day
    for cue in TIME_OF_DAY:
        if cue in q:
            req.time_of_day = cue
            break

    # Aesthetic / "best"
    if any(w in q for w in ("best", "top", "favorite", "favourite", "highlights")):
        req.only_best = True
        req.min_aesthetic = 0.3
    if "candid" in q:
        req.min_aesthetic = max(req.min_aesthetic, 0.1)

    # Photo cap heuristic
    m = re.search(r"\btop\s+(\d{1,2})\b", q)
    if m:
        req.max_photos = min(48, max(1, int(m.group(1))))

    return req


# ─── compose ──────────────────────────────────────────────────────────────────

def compose_story(cfg: FolderConfig, query: str) -> NLStory:
    Session = init_db(cfg.db_path)
    req = parse_query(cfg, query)

    with session_scope(Session) as s:
        base = (
            s.query(
                Photo.id,
                Photo.sha256,
                Photo.taken_at,
                ClassicalScore.blur,
                ClassicalScore.auto_reject,
                Embedding.aesthetic_iqa,
            )
            .outerjoin(Embedding, Embedding.photo_id == Photo.id)
            .outerjoin(ClassicalScore, ClassicalScore.photo_id == Photo.id)
            .filter(Photo.taken_at.is_not(None))
        )
        rows = base.all()

        # Person filter (require ALL listed persons in photo)
        if req.persons:
            person_membership: dict[int, set[int]] = {}
            for pid, ppid in s.query(PhotoPerson.photo_id, PhotoPerson.person_id).all():
                person_membership.setdefault(pid, set()).add(ppid)
            wanted = set(req.persons)
            rows = [r for r in rows if wanted.issubset(person_membership.get(r.id, set()))]

        # Tag filter (require ANY listed tag)
        if req.tags:
            tag_membership: dict[int, set[str]] = {}
            for pid, tag in s.query(PhotoTag.photo_id, PhotoTag.tag).all():
                tag_membership.setdefault(pid, set()).add(tag)
            wanted_tags = {t.lower() for t in req.tags}
            rows = [
                r for r in rows
                if any(t.lower() in wanted_tags for t in tag_membership.get(r.id, set()))
            ]

        # Location filter via Visit time-windows: for each named visit, pull its time range
        if req.location_names:
            visit_rows = (
                s.query(Visit.name, Visit.arrived_at, Visit.departed_at)
                .filter(Visit.name.in_(req.location_names))
                .all()
            )
            kept = []
            for r in rows:
                for _, arrived, departed in visit_rows:
                    if arrived <= r.taken_at <= departed:
                        kept.append(r)
                        break
            rows = kept

    # Filter auto-rejects and apply gates
    rows = [r for r in rows if not (r.auto_reject or False)]

    if req.time_of_day:
        start, end = TIME_OF_DAY[req.time_of_day]
        rows = [r for r in rows if start <= r.taken_at.time() <= end]

    if req.min_aesthetic > 0:
        rows = [r for r in rows if (r.aesthetic_iqa or 0) >= req.min_aesthetic]

    # Score and order — chronological by default, by score if "best"
    blur_max = max((r.blur or 0) for r in rows) if rows else 1.0
    blur_max = max(blur_max, 1.0)

    scored = []
    for r in rows:
        score = (
            0.6 * (r.aesthetic_iqa or 0.0)
            + 0.4 * min((r.blur or 0) / blur_max, 1.0)
        )
        scored.append((score, r))

    if req.only_best:
        scored.sort(key=lambda kv: -kv[0])
    else:
        scored.sort(key=lambda kv: kv[1].taken_at)

    final = scored[: req.max_photos]

    items = [
        NLStoryPhoto(
            photo_id=r.id,
            sha256=r.sha256,
            thumb_url=f"/api/thumb/{r.sha256}",
            preview_url=f"/api/preview/{r.sha256}",
            taken_at=r.taken_at.isoformat() if r.taken_at else None,
            score=float(score),
        )
        for score, r in final
    ]

    rationale = _describe(req, len(items), len(rows))
    return NLStory(query=query, parsed=req, items=items, rationale=rationale)


def _describe(req: StoryRequest, kept: int, candidates: int) -> str:
    parts = []
    if req.persons:
        parts.append(f"persons={req.persons}")
    if req.tags:
        parts.append(f"tags={req.tags}")
    if req.location_names:
        parts.append(f"locations={req.location_names}")
    if req.time_of_day:
        parts.append(f"time={req.time_of_day}")
    if req.min_aesthetic > 0:
        parts.append(f"min_aesthetic={req.min_aesthetic}")
    if req.only_best:
        parts.append("ranked by score")
    return (
        f"{kept} photos from {candidates} candidates | "
        + (", ".join(parts) if parts else "no filters")
    )
