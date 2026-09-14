"""video cull v2 summaries, segment decisions, transcript words, FTS

Adds derived clip-level scoring columns, optional keyframe IQA/motion/face
signals, a keep/skip decision on video_segments, word timings on transcript
phrases, and an FTS5 table for transcript search.

Guarded no-op per column so it is safe against databases created by
``create_all`` at a schema point that already has the columns.

Revision ID: c9d0e1f2a3b4
Revises: f7a8b9c0d1e2
Create Date: 2026-09-14 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "c9d0e1f2a3b4"
down_revision: Union[str, None] = "f7a8b9c0d1e2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_VIDEO_COLUMNS: tuple[tuple[str, sa.types.TypeEngine], ...] = (
    ("dead_ratio", sa.Float()),
    ("low_activity_ratio", sa.Float()),
    ("usable_ratio", sa.Float()),
    ("best_score", sa.Float()),
    ("analysis_version", sa.String(length=64)),
)

_KEYFRAME_COLUMNS: tuple[tuple[str, sa.types.TypeEngine], ...] = (
    ("iqa", sa.Float()),
    ("motion", sa.Float()),
    ("face_presence", sa.Float()),
)


def _add_missing(table: str, columns: tuple[tuple[str, sa.types.TypeEngine], ...]) -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if table not in insp.get_table_names():
        return
    existing = {c["name"] for c in insp.get_columns(table)}
    missing = [(name, type_) for name, type_ in columns if name not in existing]
    if not missing:
        return
    with op.batch_alter_table(table, schema=None) as batch_op:
        for name, type_ in missing:
            batch_op.add_column(sa.Column(name, type_, nullable=True))


def _drop_present(table: str, columns: tuple[tuple[str, sa.types.TypeEngine], ...]) -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if table not in insp.get_table_names():
        return
    existing = {c["name"] for c in insp.get_columns(table)}
    present = [name for name, _ in columns if name in existing]
    if not present:
        return
    with op.batch_alter_table(table, schema=None) as batch_op:
        for name in present:
            batch_op.drop_column(name)


def upgrade() -> None:
    _add_missing("videos", _VIDEO_COLUMNS)
    _add_missing("video_keyframes", _KEYFRAME_COLUMNS)
    _add_missing("video_segments", (("decision", sa.String(length=16)),))
    _add_missing("video_transcript_segments", (("words_json", sa.Text()),))
    op.execute(
        "CREATE VIRTUAL TABLE IF NOT EXISTS video_transcript_fts "
        "USING fts5(text, content='', tokenize='porter')"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS video_transcript_fts")
    _drop_present("video_transcript_segments", (("words_json", sa.Text()),))
    _drop_present("video_segments", (("decision", sa.String(length=16)),))
    _drop_present("video_keyframes", _KEYFRAME_COLUMNS)
    _drop_present("videos", _VIDEO_COLUMNS)
