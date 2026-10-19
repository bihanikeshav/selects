"""Track successful metadata reads so reindexing only repairs older readers."""
from alembic import op
import sqlalchemy as sa

revision = "f8b9c0d1e2f3"
down_revision = "c9d0e1f2a3b4"
branch_labels = None
depends_on = None


def upgrade():
    existing = set(sa.inspect(op.get_bind()).get_table_names())
    for table in ("photos", "videos"):
        if table not in existing:
            continue
        columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}
        if "metadata_version" not in columns:
            op.add_column(table, sa.Column("metadata_version", sa.Integer(), nullable=True))


def downgrade():
    existing = set(sa.inspect(op.get_bind()).get_table_names())
    for table in ("photos", "videos"):
        if table in existing:
            op.drop_column(table, "metadata_version")
