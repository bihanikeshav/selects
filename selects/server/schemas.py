"""Pydantic response models shared by the domain route modules."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


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
