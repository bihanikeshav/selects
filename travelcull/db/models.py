"""SQLAlchemy 2.0 ORM models for travelcull."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Photo(Base):
    """One row per still image file (JPEG, HEIC, RAW)."""

    __tablename__ = "photos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    path: Mapped[str] = mapped_column(String(4096), nullable=False, unique=True, index=True)
    sha256: Mapped[Optional[str]] = mapped_column(String(64))
    mtime: Mapped[Optional[float]] = mapped_column(Float)
    size_bytes: Mapped[Optional[int]] = mapped_column(Integer)
    format: Mapped[Optional[str]] = mapped_column(String(16))  # JPEG / HEIC / RAW
    width: Mapped[Optional[int]] = mapped_column(Integer)
    height: Mapped[Optional[int]] = mapped_column(Integer)
    taken_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    gps_lat: Mapped[Optional[float]] = mapped_column(Float)
    gps_lon: Mapped[Optional[float]] = mapped_column(Float)
    camera: Mapped[Optional[str]] = mapped_column(String(256))
    thumb_path: Mapped[Optional[str]] = mapped_column(String(4096))
    preview_path: Mapped[Optional[str]] = mapped_column(String(4096))
    indexed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)

    classical_score: Mapped[Optional["ClassicalScore"]] = relationship(
        "ClassicalScore", back_populates="photo", uselist=False, cascade="all, delete-orphan"
    )
    pipeline_state: Mapped[Optional["PipelineState"]] = relationship(
        "PipelineState", back_populates="photo", uselist=False, cascade="all, delete-orphan"
    )


class Video(Base):
    """One row per video file (MP4, MOV, etc.)."""

    __tablename__ = "videos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    path: Mapped[str] = mapped_column(String(4096), nullable=False, unique=True, index=True)
    sha256: Mapped[Optional[str]] = mapped_column(String(64))
    mtime: Mapped[Optional[float]] = mapped_column(Float)
    size_bytes: Mapped[Optional[int]] = mapped_column(Integer)
    format: Mapped[Optional[str]] = mapped_column(String(16))  # MP4 / MOV / MKV
    width: Mapped[Optional[int]] = mapped_column(Integer)
    height: Mapped[Optional[int]] = mapped_column(Integer)
    duration_sec: Mapped[Optional[float]] = mapped_column(Float)
    taken_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    thumb_path: Mapped[Optional[str]] = mapped_column(String(4096))
    preview_path: Mapped[Optional[str]] = mapped_column(String(4096))
    indexed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)


class ClassicalScore(Base):
    """Classical (non-ML) quality scores for a photo."""

    __tablename__ = "classical_scores"

    photo_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("photos.id", ondelete="CASCADE"), primary_key=True
    )
    blur: Mapped[Optional[float]] = mapped_column(Float)
    exposure: Mapped[Optional[float]] = mapped_column(Float)
    faces_count: Mapped[Optional[int]] = mapped_column(Integer)
    eyes_open_ratio: Mapped[Optional[float]] = mapped_column(Float)
    auto_reject: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    reject_reason: Mapped[Optional[str]] = mapped_column(Text)

    photo: Mapped["Photo"] = relationship("Photo", back_populates="classical_score")


class PipelineState(Base):
    """Tracks which processing stages have completed for a photo."""

    __tablename__ = "pipeline_states"

    photo_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("photos.id", ondelete="CASCADE"), primary_key=True
    )
    classical_done: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    embedding_done: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    vl_done: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    ordering_done: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    error: Mapped[Optional[str]] = mapped_column(Text)

    photo: Mapped["Photo"] = relationship("Photo", back_populates="pipeline_state")

    __table_args__ = (
        Index("ix_pipeline_states_work", "classical_done", "embedding_done"),
    )


class Embedding(Base):
    """SigLIP image embedding + IQA score for a photo."""

    __tablename__ = "embeddings"

    photo_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("photos.id", ondelete="CASCADE"), primary_key=True
    )
    siglip: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)  # 1152 * 2 bytes (fp16) = 2304 bytes
    aesthetic_iqa: Mapped[Optional[float]] = mapped_column(Float, default=None)

    photo: Mapped["Photo"] = relationship("Photo")


class PhotoTag(Base):
    """Zero-shot tag assigned to a photo via SigLIP similarity."""

    __tablename__ = "photo_tags"

    photo_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("photos.id", ondelete="CASCADE"), primary_key=True
    )
    tag: Mapped[str] = mapped_column(String(64), primary_key=True)
    score: Mapped[float] = mapped_column(Float, nullable=False)

    __table_args__ = (
        Index("ix_photo_tags_tag", "tag"),
    )
