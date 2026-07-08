"""add cached luma stats to classical_scores

Adds nullable ``luma_mean``, ``clipped_high`` and ``clipped_low`` columns to
``classical_scores``. The Doctor endpoint used to open and analyse every
preview JPEG on every request; these columns let it compute the luminance
stats once and reuse them. Existing rows keep NULLs and are backfilled lazily
the first time Doctor classifies each photo.

Guarded no-op per column so it is safe against databases created by
``create_all`` at a schema point that already has the columns.

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-07-07 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e5f6a7b8c9d0"
down_revision: Union[str, None] = "d4e5f6a7b8c9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_NEW_COLUMNS: tuple[tuple[str, sa.types.TypeEngine], ...] = (
    ("luma_mean", sa.Float()),
    ("clipped_high", sa.Float()),
    ("clipped_low", sa.Float()),
)


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    if "classical_scores" not in insp.get_table_names():
        return

    existing = {c["name"] for c in insp.get_columns("classical_scores")}
    missing = [(name, type_) for name, type_ in _NEW_COLUMNS if name not in existing]
    if not missing:
        return

    with op.batch_alter_table("classical_scores", schema=None) as batch_op:
        for name, type_ in missing:
            batch_op.add_column(sa.Column(name, type_, nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    if "classical_scores" not in insp.get_table_names():
        return

    existing = {c["name"] for c in insp.get_columns("classical_scores")}
    present = [name for name, _ in _NEW_COLUMNS if name in existing]
    if not present:
        return

    with op.batch_alter_table("classical_scores", schema=None) as batch_op:
        for name in present:
            batch_op.drop_column(name)
