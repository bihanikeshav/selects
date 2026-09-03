"""index photos.sha256 and videos.sha256

Thumb/editor/moment lookups filter by sha256; the column was unindexed.

Guarded so ``create_all`` databases that already have the index are a no-op.

Revision ID: a8b9c0d1e2f3
Revises: a7b8c9d0e1f2
Create Date: 2026-09-03 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a8b9c0d1e2f3"
down_revision: Union[str, None] = "a7b8c9d0e1f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    tables = set(insp.get_table_names())

    if "photos" in tables:
        cols = {c["name"] for c in insp.get_columns("photos")}
        existing = {ix["name"] for ix in insp.get_indexes("photos")}
        if "sha256" in cols and "ix_photos_sha256" not in existing:
            op.create_index("ix_photos_sha256", "photos", ["sha256"])
    if "videos" in tables:
        cols = {c["name"] for c in insp.get_columns("videos")}
        existing = {ix["name"] for ix in insp.get_indexes("videos")}
        if "sha256" in cols and "ix_videos_sha256" not in existing:
            op.create_index("ix_videos_sha256", "videos", ["sha256"])


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    tables = set(insp.get_table_names())
    if "videos" in tables:
        existing = {ix["name"] for ix in insp.get_indexes("videos")}
        if "ix_videos_sha256" in existing:
            op.drop_index("ix_videos_sha256", table_name="videos")
    if "photos" in tables:
        existing = {ix["name"] for ix in insp.get_indexes("photos")}
        if "ix_photos_sha256" in existing:
            op.drop_index("ix_photos_sha256", table_name="photos")
