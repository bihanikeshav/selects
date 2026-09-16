"""SQLAlchemy 2.0 ORM models for selects."""
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
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from selects.util import utcnow


class Base(DeclarativeBase):
    pass


class Photo(Base):
    """One row per still image file (JPEG, HEIC, RAW)."""

    __tablename__ = "photos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    path: Mapped[str] = mapped_column(String(4096), nullable=False, unique=True, index=True)
    sha256: Mapped[Optional[str]] = mapped_column(String(64), index=True)
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
    sha256: Mapped[Optional[str]] = mapped_column(String(64), index=True)
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
    # --- video-analysis columns (alembic rev d4e5f6a7b8c9) ---------------- #
    fps: Mapped[Optional[float]] = mapped_column(Float)
    frame_count: Mapped[Optional[int]] = mapped_column(Integer)
    best_frame_index: Mapped[Optional[int]] = mapped_column(Integer)
    sharpness: Mapped[Optional[float]] = mapped_column(Float)      # best-frame Laplacian variance
    exposure: Mapped[Optional[float]] = mapped_column(Float)       # best-frame exposure score [0,1]
    dead_footage: Mapped[Optional[bool]] = mapped_column(Boolean)  # NULL = not analysed yet
    frames_json: Mapped[Optional[str]] = mapped_column(Text)       # sampled-frame metrics
    highlights_json: Mapped[Optional[str]] = mapped_column(Text)   # [{start,end,frames}]
    siglip: Mapped[Optional[bytes]] = mapped_column(LargeBinary)   # best-frame SigLIP fp16 blob
    processed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    # --- cull v2 summaries (alembic rev c9d0e1f2a3b4) ---------------------- #
    dead_ratio: Mapped[Optional[float]] = mapped_column(Float)
    low_activity_ratio: Mapped[Optional[float]] = mapped_column(Float)
    usable_ratio: Mapped[Optional[float]] = mapped_column(Float)
    best_score: Mapped[Optional[float]] = mapped_column(Float)
    analysis_version: Mapped[Optional[str]] = mapped_column(String(64))


class VideoKeyframe(Base):
    """Cached representative frame and optional visual embedding for a video."""

    __tablename__ = "video_keyframes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    video_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("videos.id", ondelete="CASCADE"), nullable=False, index=True
    )
    frame_index: Mapped[int] = mapped_column(Integer, nullable=False)
    timestamp_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default="scene")
    image_path: Mapped[Optional[str]] = mapped_column(String(4096))
    quality: Mapped[Optional[float]] = mapped_column(Float)
    sharpness: Mapped[Optional[float]] = mapped_column(Float)
    exposure: Mapped[Optional[float]] = mapped_column(Float)
    iqa: Mapped[Optional[float]] = mapped_column(Float)
    motion: Mapped[Optional[float]] = mapped_column(Float)
    face_presence: Mapped[Optional[float]] = mapped_column(Float)
    siglip: Mapped[Optional[bytes]] = mapped_column(LargeBinary)
    source_fingerprint: Mapped[Optional[str]] = mapped_column(String(128), index=True)
    processor_version: Mapped[Optional[str]] = mapped_column(String(64))
    model_version: Mapped[Optional[str]] = mapped_column(String(128))
    index_version: Mapped[Optional[str]] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    __table_args__ = (
        UniqueConstraint("video_id", "frame_index", name="uq_video_keyframes_video_frame"),
        Index("ix_video_keyframes_video_time", "video_id", "timestamp_ms"),
        Index("ix_video_keyframes_video_kind", "video_id", "kind"),
    )


class VideoSegment(Base):
    """Time range used for highlights, scenes, and pooled semantic search."""

    __tablename__ = "video_segments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    video_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("videos.id", ondelete="CASCADE"), nullable=False, index=True
    )
    start_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    end_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default="scene")
    score: Mapped[Optional[float]] = mapped_column(Float)
    representative_keyframe_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("video_keyframes.id", ondelete="CASCADE")
    )
    embedding: Mapped[Optional[bytes]] = mapped_column(LargeBinary)
    ocr_text: Mapped[Optional[str]] = mapped_column(Text)
    decision: Mapped[Optional[str]] = mapped_column(String(16))  # keep | skip | NULL
    source_fingerprint: Mapped[Optional[str]] = mapped_column(String(128), index=True)
    processor_version: Mapped[Optional[str]] = mapped_column(String(64))
    model_version: Mapped[Optional[str]] = mapped_column(String(128))
    index_version: Mapped[Optional[str]] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    __table_args__ = (
        UniqueConstraint(
            "video_id", "start_ms", "end_ms", "kind", name="uq_video_segments_range"
        ),
        Index("ix_video_segments_video_time", "video_id", "start_ms", "end_ms"),
        Index("ix_video_segments_video_kind", "video_id", "kind"),
    )


class VideoProxy(Base):
    """A cached, playback-friendly derivative of a source video."""

    __tablename__ = "video_proxies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    video_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("videos.id", ondelete="CASCADE"), nullable=False, index=True
    )
    preset: Mapped[str] = mapped_column(String(32), nullable=False)
    path: Mapped[str] = mapped_column(String(4096), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ready")
    format: Mapped[Optional[str]] = mapped_column(String(16))
    codec: Mapped[Optional[str]] = mapped_column(String(32))
    width: Mapped[Optional[int]] = mapped_column(Integer)
    height: Mapped[Optional[int]] = mapped_column(Integer)
    duration_ms: Mapped[Optional[int]] = mapped_column(Integer)
    size_bytes: Mapped[Optional[int]] = mapped_column(Integer)
    source_fingerprint: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    processor_version: Mapped[Optional[str]] = mapped_column(String(64))
    model_version: Mapped[Optional[str]] = mapped_column(String(128))
    index_version: Mapped[Optional[str]] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    __table_args__ = (
        UniqueConstraint("video_id", "preset", "source_fingerprint", name="uq_video_proxies_key"),
        Index("ix_video_proxies_status", "status"),
    )


class VideoAudioAnalysis(Base):
    """Cached low-rate audio features and compressed timeline artifacts."""

    __tablename__ = "video_audio_analysis"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    video_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("videos.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source_fingerprint: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    audio_present: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    channels: Mapped[Optional[int]] = mapped_column(Integer)
    sample_rate: Mapped[Optional[int]] = mapped_column(Integer)
    duration_ms: Mapped[Optional[int]] = mapped_column(Integer)
    integrated_lufs: Mapped[Optional[float]] = mapped_column(Float)
    true_peak_db: Mapped[Optional[float]] = mapped_column(Float)
    silence_ratio: Mapped[Optional[float]] = mapped_column(Float)
    speech_ratio: Mapped[Optional[float]] = mapped_column(Float)
    waveform_blob: Mapped[Optional[bytes]] = mapped_column(LargeBinary)
    silence_segments_json: Mapped[Optional[str]] = mapped_column(Text)
    speech_segments_json: Mapped[Optional[str]] = mapped_column(Text)
    processor_version: Mapped[Optional[str]] = mapped_column(String(64))
    model_version: Mapped[Optional[str]] = mapped_column(String(128))
    index_version: Mapped[Optional[str]] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    __table_args__ = (
        UniqueConstraint("video_id", "source_fingerprint", name="uq_video_audio_source"),
        Index("ix_video_audio_video_source", "video_id", "source_fingerprint"),
    )


class VideoTranscriptSegment(Base):
    """Timestamped optional transcript text, kept separate for FTS indexing."""

    __tablename__ = "video_transcript_segments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    video_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("videos.id", ondelete="CASCADE"), nullable=False, index=True
    )
    start_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    end_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    words_json: Mapped[Optional[str]] = mapped_column(Text)
    confidence: Mapped[Optional[float]] = mapped_column(Float)
    speaker: Mapped[Optional[str]] = mapped_column(String(128))
    language: Mapped[Optional[str]] = mapped_column(String(16))
    source_fingerprint: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    processor_version: Mapped[Optional[str]] = mapped_column(String(64))
    model_version: Mapped[Optional[str]] = mapped_column(String(128))
    index_version: Mapped[Optional[str]] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    __table_args__ = (
        Index("ix_video_transcript_video_time", "video_id", "start_ms", "end_ms"),
        Index("ix_video_transcript_video_source", "video_id", "source_fingerprint"),
    )


class VideoEdit(Base):
    """A non-destructive edit recipe; rendered output belongs to a job/export."""

    __tablename__ = "video_edits"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    video_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("videos.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[Optional[str]] = mapped_column(String(256))
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    recipe_json: Mapped[str] = mapped_column(Text, nullable=False)
    source_fingerprint: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    processor_version: Mapped[Optional[str]] = mapped_column(String(64))
    model_version: Mapped[Optional[str]] = mapped_column(String(128))
    index_version: Mapped[Optional[str]] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    __table_args__ = (
        Index("ix_video_edits_video_updated", "video_id", "updated_at"),
    )


class VideoRating(Base):
    """User rating for a video; uses the same -1/0/+1 vocabulary as PhotoRating."""

    __tablename__ = "video_ratings"

    video_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("videos.id", ondelete="CASCADE"), primary_key=True
    )
    rating: Mapped[int] = mapped_column(Integer, nullable=False)
    rated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class VideoTag(Base):
    """Tag attached to a video, with optional automatic confidence and source."""

    __tablename__ = "video_tags"

    video_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("videos.id", ondelete="CASCADE"), primary_key=True
    )
    tag: Mapped[str] = mapped_column(String(128), primary_key=True)
    source: Mapped[Optional[str]] = mapped_column(String(32), primary_key=True, nullable=True)
    score: Mapped[Optional[float]] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    __table_args__ = (
        Index("ix_video_tags_tag", "tag"),
        Index("ix_video_tags_source", "source"),
    )


class VideoCollection(Base):
    """Named manual or smart collection of videos."""

    __tablename__ = "video_collections"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(256), nullable=False, unique=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default="manual")
    description: Mapped[Optional[str]] = mapped_column(Text)
    query_json: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class VideoCollectionItem(Base):
    """Ordered membership link between a collection and a video."""

    __tablename__ = "video_collection_items"

    collection_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("video_collections.id", ondelete="CASCADE"), primary_key=True
    )
    video_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("videos.id", ondelete="CASCADE"), primary_key=True
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    added_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    __table_args__ = (
        Index("ix_video_collection_items_order", "collection_id", "position"),
        Index("ix_video_collection_items_video", "video_id"),
    )


class MediaJob(Base):
    """Durable job record for video analysis, proxy, edit, and export work."""

    __tablename__ = "media_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    video_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("videos.id", ondelete="CASCADE"), index=True
    )
    job_type: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="queued")
    progress: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    payload_json: Mapped[Optional[str]] = mapped_column(Text)
    output_path: Mapped[Optional[str]] = mapped_column(String(4096))
    error_text: Mapped[Optional[str]] = mapped_column(Text)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    source_fingerprint: Mapped[Optional[str]] = mapped_column(String(128), index=True)
    processor_version: Mapped[Optional[str]] = mapped_column(String(64))
    model_version: Mapped[Optional[str]] = mapped_column(String(128))
    index_version: Mapped[Optional[str]] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    __table_args__ = (
        Index("ix_media_jobs_status_created", "status", "created_at"),
        Index("ix_media_jobs_video_status", "video_id", "status"),
    )


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

    # Cached luminance stats from the preview (mean / clipping), computed once by
    # the Doctor endpoint and reused on later visits so it never re-opens every
    # preview JPEG. Content-addressed previews make these stable per photo.
    luma_mean: Mapped[Optional[float]] = mapped_column(Float)
    clipped_high: Mapped[Optional[float]] = mapped_column(Float)
    clipped_low: Mapped[Optional[float]] = mapped_column(Float)

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
    """Tag assigned to a photo; source distinguishes origin.

    source values:
      'ram'      — RAM++ multi-label tag (per-photo object tags)
      'posting'  — tight HDBSCAN cluster within a session block (carousel groups)
      'lookback' — broad HDBSCAN cluster globally (theme browsing)
      NULL       — legacy zero-shot SigLIP tags (backward compat)

    Primary key: (photo_id, tag, source) — supports same tag from different sources.
    For backward compat with existing tables that have PK (photo_id, tag), the
    Alembic revision 'add source to photo_tags PK' (run by init_db) upgrades the
    schema on first open.
    """

    __tablename__ = "photo_tags"

    photo_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("photos.id", ondelete="CASCADE"), primary_key=True
    )
    tag: Mapped[str] = mapped_column(String(128), primary_key=True)
    score: Mapped[float] = mapped_column(Float, nullable=False)
    # source is part of the PK — allows same tag name from different sources.
    # An Alembic migration recreates the table with this PK on first open of an
    # existing (pre-source) database.
    source: Mapped[Optional[str]] = mapped_column(String(16), nullable=True, primary_key=True)

    __table_args__ = (
        Index("ix_photo_tags_tag", "tag"),
        Index("ix_photo_tags_source", "source"),
    )


class Story(Base):
    """One per-day narrative story — a curated sequence of representative photos."""

    __tablename__ = "stories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    day: Mapped[str] = mapped_column(String(10), unique=True, index=True)  # YYYY-MM-DD
    title: Mapped[str] = mapped_column(Text, nullable=False)
    photo_count: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    items: Mapped[list["StoryItem"]] = relationship(
        "StoryItem", back_populates="story", cascade="all, delete-orphan", order_by="StoryItem.rank"
    )
    visits: Mapped[list["Visit"]] = relationship(
        "Visit", back_populates="story", cascade="all, delete-orphan", order_by="Visit.rank"
    )


class StoryItem(Base):
    """One photo slot within a Story, in rank order."""

    __tablename__ = "story_items"

    story_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("stories.id", ondelete="CASCADE"), primary_key=True
    )
    rank: Mapped[int] = mapped_column(Integer, primary_key=True)
    photo_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("photos.id", ondelete="CASCADE"), nullable=False
    )
    scene_label: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    scene_rank: Mapped[Optional[int]] = mapped_column(Integer, default=None)

    story: Mapped["Story"] = relationship("Story", back_populates="items")
    photo: Mapped["Photo"] = relationship("Photo")


class FaceEmbedding(Base):
    """Per-face ArcFace embedding extracted by insightface buffalo_l."""

    __tablename__ = "face_embeddings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    photo_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("photos.id", ondelete="CASCADE"), index=True
    )
    face_index: Mapped[int] = mapped_column(Integer, nullable=False)   # 0-based within photo
    embedding: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)  # 512 * 2 bytes (fp16)
    bbox_x: Mapped[int] = mapped_column(Integer, nullable=False)
    bbox_y: Mapped[int] = mapped_column(Integer, nullable=False)
    bbox_w: Mapped[int] = mapped_column(Integer, nullable=False)
    bbox_h: Mapped[int] = mapped_column(Integer, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    # Face attributes (nullable — backfilled lazily for pre-existing rows).
    eyes_open: Mapped[Optional[float]] = mapped_column(Float, default=None)        # [0,1], 1 = open
    yaw: Mapped[Optional[float]] = mapped_column(Float, default=None)              # degrees
    pitch: Mapped[Optional[float]] = mapped_column(Float, default=None)            # degrees
    face_area_ratio: Mapped[Optional[float]] = mapped_column(Float, default=None)  # bbox/image area

    photo: Mapped["Photo"] = relationship("Photo")


class Moment(Base):
    """A group of photos: same person(s) at the same place in a short time window."""

    __tablename__ = "moments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    primary_photo_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("photos.id", ondelete="CASCADE"), index=True
    )
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    ended_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    size: Mapped[int] = mapped_column(Integer, nullable=False)

    members: Mapped[list["MomentMember"]] = relationship(
        "MomentMember", back_populates="moment", cascade="all, delete-orphan",
        order_by="MomentMember.rank",
    )


class MomentMember(Base):
    """Membership link between a Moment and a Photo, with rank (0 = primary)."""

    __tablename__ = "moment_members"

    moment_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("moments.id", ondelete="CASCADE"), primary_key=True
    )
    photo_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("photos.id", ondelete="CASCADE"), primary_key=True
    )
    rank: Mapped[int] = mapped_column(Integer, nullable=False)  # 0 = primary

    moment: Mapped["Moment"] = relationship("Moment", back_populates="members")


class GeocodeCache(Base):
    """Cache for Nominatim reverse geocode results, keyed on ~110m grid."""

    __tablename__ = "geocode_cache"

    lat_round: Mapped[float] = mapped_column(Float, primary_key=True)
    lon_round: Mapped[float] = mapped_column(Float, primary_key=True)
    payload: Mapped[Optional[str]] = mapped_column(Text, default=None)          # JSON from Nominatim
    display_name: Mapped[Optional[str]] = mapped_column(Text, default=None)     # resolved human name
    wikipedia_summary: Mapped[Optional[str]] = mapped_column(Text, default=None)  # Wikipedia blurb


class Visit(Base):
    """GPS-grounded location visit within a story day."""

    __tablename__ = "visits"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    story_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("stories.id", ondelete="CASCADE"), index=True
    )
    rank: Mapped[int] = mapped_column(Integer, nullable=False)          # chronological order within story
    name: Mapped[str] = mapped_column(Text, nullable=False)             # "Pangong Tso", "Khardung La"
    summary: Mapped[Optional[str]] = mapped_column(Text, default=None)  # Wikipedia blurb
    lat: Mapped[float] = mapped_column(Float, nullable=False)
    lon: Mapped[float] = mapped_column(Float, nullable=False)
    elevation_m: Mapped[Optional[int]] = mapped_column(Integer, default=None)
    arrived_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    departed_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    photo_count: Mapped[int] = mapped_column(Integer, nullable=False)
    cover_photo_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("photos.id", ondelete="SET NULL"), default=None
    )

    story: Mapped["Story"] = relationship("Story", back_populates="visits")
    cover_photo: Mapped[Optional["Photo"]] = relationship("Photo")

    __table_args__ = (
        Index("ix_visits_story_rank", "story_id", "rank"),
    )


class Person(Base):
    """A clustered face identity. User-labelable after detection."""

    __tablename__ = "persons"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    label: Mapped[Optional[str]] = mapped_column(Text, default=None)
    cover_face_embedding_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("face_embeddings.id", ondelete="SET NULL"), default=None
    )
    photo_count: Mapped[int] = mapped_column(Integer, default=0)
    centroid: Mapped[Optional[bytes]] = mapped_column(LargeBinary, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    # User can hide stranger/random clusters from the People view (not deleted).
    hidden: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class PhotoPerson(Base):
    """Association: which Persons appear in which Photo."""

    __tablename__ = "photo_persons"

    photo_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("photos.id", ondelete="CASCADE"), primary_key=True
    )
    person_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("persons.id", ondelete="CASCADE"), primary_key=True
    )
    face_embedding_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("face_embeddings.id", ondelete="CASCADE")
    )
    confidence: Mapped[float] = mapped_column(Float)


class PhotoEdit(Base):
    """Non-destructive editor state: the slider params (JSON) last applied to a
    photo in the in-app editor. The baked JPEG lives at <state>/edits/<sha>.jpg."""

    __tablename__ = "photo_edits"

    photo_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("photos.id", ondelete="CASCADE"), primary_key=True
    )
    params: Mapped[str] = mapped_column(Text, nullable=False)  # JSON of {key: value}
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Swipe(Base):
    """User keep/reject decision during cull."""

    __tablename__ = "swipes"

    photo_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("photos.id", ondelete="CASCADE"), primary_key=True
    )
    decision: Mapped[str] = mapped_column(Text)  # "keep" | "reject" | "silver" | "skip"
    swiped_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class AestheticScore(Base):
    """Alternative aesthetic-quality scores from off-the-shelf and personalized models.

    Held separate from Embedding so we can backfill / re-score without touching
    the SigLIP blob column. ``ap25_score`` is HyperIQA on a 1–10 scale.
    Prompt-IQA from SigLIP 2 stays on Embedding.aesthetic_iqa.
    """

    __tablename__ = "aesthetic_scores"

    photo_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("photos.id", ondelete="CASCADE"), primary_key=True
    )
    nima_score: Mapped[Optional[float]] = mapped_column(Float, default=None)
    ap25_score: Mapped[Optional[float]] = mapped_column(Float, default=None)
    personal_score: Mapped[Optional[float]] = mapped_column(Float, default=None)


class PhotoRating(Base):
    """User aesthetic rating used to train the personalized score.

    rating: +1 = thumbs up, -1 = thumbs down, 0 = skip (not used for training).
    """

    __tablename__ = "photo_ratings"

    photo_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("photos.id", ondelete="CASCADE"), primary_key=True
    )
    rating: Mapped[int] = mapped_column(Integer, nullable=False)
    rated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class PhotoCategory(Base):
    """Zero-shot SigLIP category probes — populates Best-Of facets for
    landscape, portrait, and object (still-life) photo subsets.
    """

    __tablename__ = "photo_categories"

    photo_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("photos.id", ondelete="CASCADE"), primary_key=True
    )
    landscape_sim: Mapped[Optional[float]] = mapped_column(Float, default=None)
    portrait_sim: Mapped[Optional[float]] = mapped_column(Float, default=None)
    object_sim: Mapped[Optional[float]] = mapped_column(Float, default=None)
    primary_category: Mapped[Optional[str]] = mapped_column(String(16), default=None)
