"""add face attribute columns to face_embeddings

Adds nullable ``eyes_open``, ``yaw``, ``pitch`` and ``face_area_ratio``
columns to ``face_embeddings`` for eyes-closed / head-pose aware culling.
Existing rows keep NULLs and are backfilled lazily by
``travelcull.ml.face_attributes.run_face_attribute_stage``.

Guarded no-op per column so it is safe against databases created by
``create_all`` at a schema point that already has the columns, and against a
legacy DB that never had a ``face_embeddings`` table at all.

Revision ID: c3d4e5f6a7b8
Revises: a1b2c3d4e5f6
Create Date: 2026-07-06 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c3d4e5f6a7b8"
down_revision: Union[str, None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_NEW_COLUMNS = ("eyes_open", "yaw", "pitch", "face_area_ratio")


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    if "face_embeddings" not in insp.get_table_names():
        return

    existing = {c["name"] for c in insp.get_columns("face_embeddings")}
    missing = [name for name in _NEW_COLUMNS if name not in existing]
    if not missing:
        return

    # batch mode: SQLite-safe table alteration.
    with op.batch_alter_table("face_embeddings", schema=None) as batch_op:
        for name in missing:
            batch_op.add_column(sa.Column(name, sa.Float(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    if "face_embeddings" not in insp.get_table_names():
        return

    existing = {c["name"] for c in insp.get_columns("face_embeddings")}
    present = [name for name in _NEW_COLUMNS if name in existing]
    if not present:
        return

    with op.batch_alter_table("face_embeddings", schema=None) as batch_op:
        for name in present:
            batch_op.drop_column(name)
