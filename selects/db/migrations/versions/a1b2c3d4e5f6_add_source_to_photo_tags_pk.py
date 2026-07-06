"""add source column to photo_tags and rebuild PK

Ports the historical hand-rolled ``_migrate_add_source_column`` (formerly in
selects/ml/ram_tags.py). Adds the ``source`` column to ``photo_tags`` and
rebuilds the table so the primary key is ``(photo_id, tag, source)`` — allowing
the same tag name from different sources (RAM++, posting/lookback clusters,
legacy NULL-source SigLIP tags).

This revision is a **guarded no-op** when ``source`` is already part of the
primary key, so it is safe to run against databases that were either created by
``create_all`` at a schema point that already had the source-in-PK shape, or
already upgraded by the old hand-rolled migration.

Revision ID: a1b2c3d4e5f6
Revises: 8ff743c44fc7
Create Date: 2026-07-05 22:15:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, None] = "8ff743c44fc7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    if "photo_tags" not in insp.get_table_names():
        return

    pk_cols = insp.get_pk_constraint("photo_tags").get("constrained_columns") or []
    if "source" in pk_cols:
        # Already migrated (source is part of the PK) — nothing to do.
        return

    existing_cols = {c["name"] for c in insp.get_columns("photo_tags")}

    # Raw-SQL rebuild (mirrors the historical hand-rolled migration). We do not
    # toggle PRAGMA foreign_keys here because Alembic runs inside a transaction
    # (where the pragma is a no-op) and photo_tags is not referenced by any other
    # table's foreign key, so recreating it is safe.
    op.execute(
        """
        CREATE TABLE photo_tags_new (
            photo_id INTEGER NOT NULL,
            tag VARCHAR(128) NOT NULL,
            score FLOAT NOT NULL,
            source VARCHAR(16),
            PRIMARY KEY (photo_id, tag, source),
            FOREIGN KEY(photo_id) REFERENCES photos (id) ON DELETE CASCADE
        )
        """
    )
    if "source" in existing_cols:
        op.execute(
            "INSERT OR IGNORE INTO photo_tags_new (photo_id, tag, score, source) "
            "SELECT photo_id, tag, score, source FROM photo_tags"
        )
    else:
        op.execute(
            "INSERT OR IGNORE INTO photo_tags_new (photo_id, tag, score, source) "
            "SELECT photo_id, tag, score, NULL FROM photo_tags"
        )
    op.execute("DROP TABLE photo_tags")
    op.execute("ALTER TABLE photo_tags_new RENAME TO photo_tags")
    op.execute("CREATE INDEX IF NOT EXISTS ix_photo_tags_tag ON photo_tags (tag)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_photo_tags_source ON photo_tags (source)")


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    if "photo_tags" not in insp.get_table_names():
        return

    pk_cols = insp.get_pk_constraint("photo_tags").get("constrained_columns") or []
    if "source" not in pk_cols:
        return

    op.execute(
        """
        CREATE TABLE photo_tags_new (
            photo_id INTEGER NOT NULL,
            tag VARCHAR(128) NOT NULL,
            score FLOAT NOT NULL,
            PRIMARY KEY (photo_id, tag),
            FOREIGN KEY(photo_id) REFERENCES photos (id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        "INSERT OR IGNORE INTO photo_tags_new (photo_id, tag, score) "
        "SELECT photo_id, tag, score FROM photo_tags"
    )
    op.execute("DROP TABLE photo_tags")
    op.execute("ALTER TABLE photo_tags_new RENAME TO photo_tags")
    op.execute("CREATE INDEX IF NOT EXISTS ix_photo_tags_tag ON photo_tags (tag)")
