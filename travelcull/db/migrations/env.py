"""Alembic environment for travelcull.

Designed to be driven programmatically via the Alembic Python API (see
``travelcull.db._ensure_schema``) rather than an ``alembic.ini`` on disk. The
target database URL is supplied through ``config`` — either as an already-open
Connection in ``config.attributes['connection']`` or via the ``sqlalchemy.url``
main option. ``render_as_batch=True`` is enabled so future column ALTERs work on
SQLite (which has no native ALTER for most operations).
"""
from __future__ import annotations

from alembic import context
from sqlalchemy import engine_from_config, pool

from travelcull.db.models import Base

config = context.config

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def _run_with_connection(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=True,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    # Reuse a caller-provided connection when available so migrations run on the
    # same engine (and WAL pragmas) as the rest of the app.
    connection = config.attributes.get("connection", None)
    if connection is not None:
        _run_with_connection(connection)
        return

    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as conn:
        _run_with_connection(conn)


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
