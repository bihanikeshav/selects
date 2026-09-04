"""Pydantic response models shared by the domain route modules."""
from __future__ import annotations

import re
from typing import Literal, Optional

from fastapi import HTTPException
from pydantic import BaseModel

_SHA256_RE = re.compile(r"[0-9a-f]{64}", re.I)

#: The four quick-sort quality buckets ``/api/photos`` and
#: ``/api/swipes/summary`` accept. Anything else is a 422.
QualityBucket = Optional[
    Literal["underexposed", "overexposed", "out_of_focus", "blurry_keepers"]
]


def require_sha256(sha256: str) -> None:
    """Reject anything that is not a bare 64-char hex digest.

    Shared by every route that turns a sha256 into a filesystem path, so a
    traversal attempt can never reach the state directory.
    """
    if (
        not _SHA256_RE.fullmatch(sha256 or "")
        or ".." in sha256
        or "/" in sha256
        or "\\" in sha256
    ):
        raise HTTPException(400, "invalid sha256")


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


def photo_out(photo, score=None, emb=None, **extra) -> PhotoOut:
    """Build a :class:`PhotoOut` from a ``(Photo, ClassicalScore, Embedding)`` row.

    ``score`` and ``emb`` may be ``None`` (outer joins). ``extra`` carries the
    per-route fields — ``moment_id`` / ``moment_size`` — so photos, clusters and
    persons all emit the same shape and can never drift apart again.
    """
    return PhotoOut(
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
        **extra,
    )


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
    moment_id: Optional[int] = None
    moment_size: Optional[int] = None


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


class PersonOut(BaseModel):
    id: int
    label: Optional[str]
    photo_count: int
    cover_url: str
    hidden: bool = False


class PersonList(BaseModel):
    total: int
    persons: list[PersonOut]
    speed_mode: str = "full"
    faces_ran: bool = True
