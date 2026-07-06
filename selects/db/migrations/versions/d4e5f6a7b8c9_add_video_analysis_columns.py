"""add video analysis columns to videos

Adds nullable analysis columns to ``videos`` for video-culling parity:
``fps``, ``frame_count``, ``best_frame_index``, ``sharpness``, ``exposure``,
``dead_footage``, ``frames_json``, ``highlights_json``, ``siglip`` and
``processed_at``. Existing rows keep NULLs and are backfilled lazily by
``selects.video.run_video_stage``.

Guarded no-op per column so it is safe against databases created by
``create_all`` at a schema point that already has the columns, and against a
legacy DB that never had a ``videos`` table at all.

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-07-06 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d4e5f6a7b8c9"
down_revision: Union[str, None] = "c3d4e5f6a7b8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_NEW_COLUMNS: tuple[tuple[str, sa.types.TypeEngine], ...] = (
    ("fps", sa.Float()),
    ("frame_count", sa.Integer()),
    ("best_frame_index", sa.Integer()),
    ("sharpness", sa.Float()),
    ("exposure", sa.Float()),
    ("dead_footage", sa.Boolean()),
    ("frames_json", sa.Text()),
    ("highlights_json", sa.Text()),
    ("siglip", sa.LargeBinary()),
    ("processed_at", sa.DateTime()),
)


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    if "videos" not in insp.get_table_names():
        return

    existing = {c["name"] for c in insp.get_columns("videos")}
    missing = [(name, type_) for name, type_ in _NEW_COLUMNS if name not in existing]
    if not missing:
        return

    # batch mode: SQLite-safe table alteration.
    with op.batch_alter_table("videos", schema=None) as batch_op:
        for name, type_ in missing:
            batch_op.add_column(sa.Column(name, type_, nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    if "videos" not in insp.get_table_names():
        return

    existing = {c["name"] for c in insp.get_columns("videos")}
    present = [name for name, _ in _NEW_COLUMNS if name in existing]
    if not present:
        return

    with op.batch_alter_table("videos", schema=None) as batch_op:
        for name in present:
            batch_op.drop_column(name)
