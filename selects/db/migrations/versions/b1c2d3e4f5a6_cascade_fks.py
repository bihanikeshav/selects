"""cascade / set-null foreign keys for photo deletion

Deleting a Photo used to leave orphan rows (swipes, photo_persons, photo_edits,
moments) or fail outright, because those foreign keys carried no ON DELETE
action. Index pruning now deletes photo rows, so the actions have to exist.

SQLite cannot ALTER a constraint, so every affected table is rebuilt via batch
mode. A rebuild DROPs the old table, and with ``PRAGMA foreign_keys=ON`` that
would fire an implicit DELETE (cascading into moment_members, and erroring on
photo_persons -> persons), so foreign keys are switched off for the rebuild and
restored afterwards -- the standard SQLite table-rebuild procedure.

Revision ID: b1c2d3e4f5a6
Revises: a8b9c0d1e2f3
Create Date: 2026-09-04 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b1c2d3e4f5a6"
down_revision: Union[str, None] = "a8b9c0d1e2f3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Lets batch mode address the reflected (unnamed) SQLite constraints by name.
NAMING = {"fk": "fk_%(table_name)s_%(column_0_name)s"}

# (table, column, referred table, upgrade action, downgrade action)
_FKS = (
    ("swipes", "photo_id", "photos", "CASCADE", None),
    ("photo_edits", "photo_id", "photos", "CASCADE", None),
    ("moments", "primary_photo_id", "photos", "CASCADE", None),
    ("photo_persons", "photo_id", "photos", "CASCADE", None),
    ("photo_persons", "person_id", "persons", "CASCADE", None),
    ("photo_persons", "face_embedding_id", "face_embeddings", "CASCADE", None),
    ("visits", "cover_photo_id", "photos", "SET NULL", None),
    ("persons", "cover_face_embedding_id", "face_embeddings", "SET NULL", None),
)


def _apply(forward: bool) -> None:
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    is_sqlite = bind.dialect.name == "sqlite"

    fk_was_on = False
    if is_sqlite:
        fk_was_on = bool(bind.exec_driver_sql("PRAGMA foreign_keys").scalar())
        bind.exec_driver_sql("PRAGMA foreign_keys=OFF")
    try:
        # Group by table so each one is rebuilt exactly once.
        for table in dict.fromkeys(t for t, *_ in _FKS):
            if table not in tables:
                continue
            entries = [f for f in _FKS if f[0] == table]
            with op.batch_alter_table(table, naming_convention=NAMING) as batch:
                for _, column, referred, up_action, down_action in entries:
                    name = f"fk_{table}_{column}"
                    action = up_action if forward else down_action
                    batch.drop_constraint(name, type_="foreignkey")
                    batch.create_foreign_key(
                        name, referred, [column], ["id"], ondelete=action
                    )
    finally:
        if is_sqlite and fk_was_on:
            bind.exec_driver_sql("PRAGMA foreign_keys=ON")


def upgrade() -> None:
    _apply(forward=True)


def downgrade() -> None:
    _apply(forward=False)
