"""add photo_edits table (in-app editor params)

Stores the non-destructive editor slider params (JSON) per photo. Guarded no-op
so it is safe against databases already created by create_all with the table.

Revision ID: a7b8c9d0e1f2
Revises: f6a7b8c9d0e1
Create Date: 2026-07-08 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a7b8c9d0e1f2"
down_revision: Union[str, None] = "f6a7b8c9d0e1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "photo_edits" in insp.get_table_names():
        return
    op.create_table(
        "photo_edits",
        sa.Column("photo_id", sa.Integer(), sa.ForeignKey("photos.id"), primary_key=True),
        sa.Column("params", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "photo_edits" in insp.get_table_names():
        op.drop_table("photo_edits")
