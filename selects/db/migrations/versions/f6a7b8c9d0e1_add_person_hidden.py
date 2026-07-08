"""add hidden flag to persons

Adds a boolean ``hidden`` column to ``persons`` (default 0) so the user can hide
face clusters of strangers/randoms from the People view without deleting them.

Guarded no-op so it is safe against databases created by ``create_all`` at a
schema point that already has the column.

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-07-08 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f6a7b8c9d0e1"
down_revision: Union[str, None] = "e5f6a7b8c9d0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    if "persons" not in insp.get_table_names():
        return
    # Recover from an earlier failed batch_alter_table attempt (its temp table is
    # left behind and makes every retry fail with "table ... already exists").
    op.execute("DROP TABLE IF EXISTS _alembic_tmp_persons")

    existing = {c["name"] for c in insp.get_columns("persons")}
    if "hidden" in existing:
        return

    # Plain ADD COLUMN — SQLite supports NOT NULL + constant DEFAULT directly, so
    # no table-recreating batch mode (which races across threads) is needed.
    op.add_column(
        "persons",
        sa.Column("hidden", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    if "persons" not in insp.get_table_names():
        return
    existing = {c["name"] for c in insp.get_columns("persons")}
    if "hidden" not in existing:
        return

    op.drop_column("persons", "hidden")
