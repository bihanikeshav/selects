"""add additive video organization and editor schema

The existing ``photos`` and ``videos`` tables remain compatibility roots.  This
revision adds child tables for cached video derivatives, organization metadata,
non-destructive edit recipes, and durable media jobs.

Revision ID: f7a8b9c0d1e2
Revises: b1c2d3e4f5a6
Create Date: 2026-09-14 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "f7a8b9c0d1e2"
down_revision: Union[str, None] = "b1c2d3e4f5a6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    existing = set(sa.inspect(op.get_bind()).get_table_names())

    if "video_keyframes" not in existing:
        op.create_table(
            "video_keyframes",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("video_id", sa.Integer(), nullable=False),
            sa.Column("frame_index", sa.Integer(), nullable=False),
            sa.Column("timestamp_ms", sa.Integer(), nullable=False),
            sa.Column("kind", sa.String(length=16), nullable=False, server_default="scene"),
            sa.Column("image_path", sa.String(length=4096), nullable=True),
            sa.Column("quality", sa.Float(), nullable=True),
            sa.Column("sharpness", sa.Float(), nullable=True),
            sa.Column("exposure", sa.Float(), nullable=True),
            sa.Column("siglip", sa.LargeBinary(), nullable=True),
            sa.Column("source_fingerprint", sa.String(length=128), nullable=True),
            sa.Column("processor_version", sa.String(length=64), nullable=True),
            sa.Column("model_version", sa.String(length=128), nullable=True),
            sa.Column("index_version", sa.String(length=64), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["video_id"], ["videos.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("video_id", "frame_index", name="uq_video_keyframes_video_frame"),
        )
        op.create_index("ix_video_keyframes_video_id", "video_keyframes", ["video_id"])
        op.create_index(
            "ix_video_keyframes_source_fingerprint",
            "video_keyframes",
            ["source_fingerprint"],
        )
        op.create_index(
            "ix_video_keyframes_video_time", "video_keyframes", ["video_id", "timestamp_ms"]
        )
        op.create_index(
            "ix_video_keyframes_video_kind", "video_keyframes", ["video_id", "kind"]
        )

    if "video_segments" not in existing:
        op.create_table(
            "video_segments",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("video_id", sa.Integer(), nullable=False),
            sa.Column("start_ms", sa.Integer(), nullable=False),
            sa.Column("end_ms", sa.Integer(), nullable=False),
            sa.Column("kind", sa.String(length=16), nullable=False, server_default="scene"),
            sa.Column("score", sa.Float(), nullable=True),
            sa.Column("representative_keyframe_id", sa.Integer(), nullable=True),
            sa.Column("embedding", sa.LargeBinary(), nullable=True),
            sa.Column("ocr_text", sa.Text(), nullable=True),
            sa.Column("source_fingerprint", sa.String(length=128), nullable=True),
            sa.Column("processor_version", sa.String(length=64), nullable=True),
            sa.Column("model_version", sa.String(length=128), nullable=True),
            sa.Column("index_version", sa.String(length=64), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["video_id"], ["videos.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(
                ["representative_keyframe_id"],
                ["video_keyframes.id"],
                ondelete="CASCADE",
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "video_id", "start_ms", "end_ms", "kind", name="uq_video_segments_range"
            ),
        )
        op.create_index("ix_video_segments_video_id", "video_segments", ["video_id"])
        op.create_index(
            "ix_video_segments_source_fingerprint", "video_segments", ["source_fingerprint"]
        )
        op.create_index(
            "ix_video_segments_video_time",
            "video_segments",
            ["video_id", "start_ms", "end_ms"],
        )
        op.create_index(
            "ix_video_segments_video_kind", "video_segments", ["video_id", "kind"]
        )

    if "video_proxies" not in existing:
        op.create_table(
            "video_proxies",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("video_id", sa.Integer(), nullable=False),
            sa.Column("preset", sa.String(length=32), nullable=False),
            sa.Column("path", sa.String(length=4096), nullable=False),
            sa.Column("status", sa.String(length=16), nullable=False, server_default="ready"),
            sa.Column("format", sa.String(length=16), nullable=True),
            sa.Column("codec", sa.String(length=32), nullable=True),
            sa.Column("width", sa.Integer(), nullable=True),
            sa.Column("height", sa.Integer(), nullable=True),
            sa.Column("duration_ms", sa.Integer(), nullable=True),
            sa.Column("size_bytes", sa.Integer(), nullable=True),
            sa.Column("source_fingerprint", sa.String(length=128), nullable=False),
            sa.Column("processor_version", sa.String(length=64), nullable=True),
            sa.Column("model_version", sa.String(length=128), nullable=True),
            sa.Column("index_version", sa.String(length=64), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["video_id"], ["videos.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "video_id", "preset", "source_fingerprint", name="uq_video_proxies_key"
            ),
        )
        op.create_index("ix_video_proxies_video_id", "video_proxies", ["video_id"])
        op.create_index(
            "ix_video_proxies_source_fingerprint", "video_proxies", ["source_fingerprint"]
        )
        op.create_index("ix_video_proxies_status", "video_proxies", ["status"])

    if "video_audio_analysis" not in existing:
        op.create_table(
            "video_audio_analysis",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("video_id", sa.Integer(), nullable=False),
            sa.Column("source_fingerprint", sa.String(length=128), nullable=False),
            sa.Column("audio_present", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("channels", sa.Integer(), nullable=True),
            sa.Column("sample_rate", sa.Integer(), nullable=True),
            sa.Column("duration_ms", sa.Integer(), nullable=True),
            sa.Column("integrated_lufs", sa.Float(), nullable=True),
            sa.Column("true_peak_db", sa.Float(), nullable=True),
            sa.Column("silence_ratio", sa.Float(), nullable=True),
            sa.Column("speech_ratio", sa.Float(), nullable=True),
            sa.Column("waveform_blob", sa.LargeBinary(), nullable=True),
            sa.Column("silence_segments_json", sa.Text(), nullable=True),
            sa.Column("speech_segments_json", sa.Text(), nullable=True),
            sa.Column("processor_version", sa.String(length=64), nullable=True),
            sa.Column("model_version", sa.String(length=128), nullable=True),
            sa.Column("index_version", sa.String(length=64), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["video_id"], ["videos.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("video_id", "source_fingerprint", name="uq_video_audio_source"),
        )
        op.create_index("ix_video_audio_analysis_video_id", "video_audio_analysis", ["video_id"])
        op.create_index(
            "ix_video_audio_analysis_source_fingerprint",
            "video_audio_analysis",
            ["source_fingerprint"],
        )
        op.create_index(
            "ix_video_audio_video_source",
            "video_audio_analysis",
            ["video_id", "source_fingerprint"],
        )

    if "video_transcript_segments" not in existing:
        op.create_table(
            "video_transcript_segments",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("video_id", sa.Integer(), nullable=False),
            sa.Column("start_ms", sa.Integer(), nullable=False),
            sa.Column("end_ms", sa.Integer(), nullable=False),
            sa.Column("text", sa.Text(), nullable=False),
            sa.Column("confidence", sa.Float(), nullable=True),
            sa.Column("speaker", sa.String(length=128), nullable=True),
            sa.Column("language", sa.String(length=16), nullable=True),
            sa.Column("source_fingerprint", sa.String(length=128), nullable=False),
            sa.Column("processor_version", sa.String(length=64), nullable=True),
            sa.Column("model_version", sa.String(length=128), nullable=True),
            sa.Column("index_version", sa.String(length=64), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["video_id"], ["videos.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            "ix_video_transcript_segments_video_id",
            "video_transcript_segments",
            ["video_id"],
        )
        op.create_index(
            "ix_video_transcript_segments_source_fingerprint",
            "video_transcript_segments",
            ["source_fingerprint"],
        )
        op.create_index(
            "ix_video_transcript_video_time",
            "video_transcript_segments",
            ["video_id", "start_ms", "end_ms"],
        )
        op.create_index(
            "ix_video_transcript_video_source",
            "video_transcript_segments",
            ["video_id", "source_fingerprint"],
        )

    if "video_edits" not in existing:
        op.create_table(
            "video_edits",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("video_id", sa.Integer(), nullable=False),
            sa.Column("name", sa.String(length=256), nullable=True),
            sa.Column("schema_version", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("recipe_json", sa.Text(), nullable=False),
            sa.Column("source_fingerprint", sa.String(length=128), nullable=False),
            sa.Column("processor_version", sa.String(length=64), nullable=True),
            sa.Column("model_version", sa.String(length=128), nullable=True),
            sa.Column("index_version", sa.String(length=64), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["video_id"], ["videos.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_video_edits_video_id", "video_edits", ["video_id"])
        op.create_index(
            "ix_video_edits_source_fingerprint", "video_edits", ["source_fingerprint"]
        )
        op.create_index("ix_video_edits_video_updated", "video_edits", ["video_id", "updated_at"])

    if "video_ratings" not in existing:
        op.create_table(
            "video_ratings",
            sa.Column("video_id", sa.Integer(), nullable=False),
            sa.Column("rating", sa.Integer(), nullable=False),
            sa.Column("rated_at", sa.DateTime(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["video_id"], ["videos.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("video_id"),
        )

    if "video_tags" not in existing:
        op.create_table(
            "video_tags",
            sa.Column("video_id", sa.Integer(), nullable=False),
            sa.Column("tag", sa.String(length=128), nullable=False),
            sa.Column("source", sa.String(length=32), nullable=True),
            sa.Column("score", sa.Float(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["video_id"], ["videos.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("video_id", "tag", "source"),
        )
        op.create_index("ix_video_tags_tag", "video_tags", ["tag"])
        op.create_index("ix_video_tags_source", "video_tags", ["source"])

    if "video_collections" not in existing:
        op.create_table(
            "video_collections",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("name", sa.String(length=256), nullable=False),
            sa.Column("kind", sa.String(length=16), nullable=False, server_default="manual"),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("query_json", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("name"),
        )

    if "video_collection_items" not in existing:
        op.create_table(
            "video_collection_items",
            sa.Column("collection_id", sa.Integer(), nullable=False),
            sa.Column("video_id", sa.Integer(), nullable=False),
            sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("added_at", sa.DateTime(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(
                ["collection_id"], ["video_collections.id"], ondelete="CASCADE"
            ),
            sa.ForeignKeyConstraint(["video_id"], ["videos.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("collection_id", "video_id"),
        )
        op.create_index(
            "ix_video_collection_items_order",
            "video_collection_items",
            ["collection_id", "position"],
        )
        op.create_index("ix_video_collection_items_video", "video_collection_items", ["video_id"])

    if "media_jobs" not in existing:
        op.create_table(
            "media_jobs",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("video_id", sa.Integer(), nullable=True),
            sa.Column("job_type", sa.String(length=32), nullable=False),
            sa.Column("status", sa.String(length=16), nullable=False, server_default="queued"),
            sa.Column("progress", sa.Float(), nullable=False, server_default="0"),
            sa.Column("payload_json", sa.Text(), nullable=True),
            sa.Column("output_path", sa.String(length=4096), nullable=True),
            sa.Column("error_text", sa.Text(), nullable=True),
            sa.Column("cancel_requested", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("source_fingerprint", sa.String(length=128), nullable=True),
            sa.Column("processor_version", sa.String(length=64), nullable=True),
            sa.Column("model_version", sa.String(length=128), nullable=True),
            sa.Column("index_version", sa.String(length=64), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("started_at", sa.DateTime(), nullable=True),
            sa.Column("finished_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["video_id"], ["videos.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_media_jobs_video_id", "media_jobs", ["video_id"])
        op.create_index(
            "ix_media_jobs_source_fingerprint", "media_jobs", ["source_fingerprint"]
        )
        op.create_index("ix_media_jobs_status_created", "media_jobs", ["status", "created_at"])
        op.create_index("ix_media_jobs_video_status", "media_jobs", ["video_id", "status"])


def downgrade() -> None:
    bind = op.get_bind()
    existing = set(sa.inspect(bind).get_table_names())

    for table in (
        "media_jobs",
        "video_collection_items",
        "video_collections",
        "video_tags",
        "video_ratings",
        "video_edits",
        "video_transcript_segments",
        "video_audio_analysis",
        "video_proxies",
        "video_segments",
        "video_keyframes",
    ):
        if table in existing:
            op.drop_table(table)
