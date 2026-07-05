"""travelcull database session management."""
from __future__ import annotations

import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, Generator, Tuple

from sqlalchemy import create_engine, event, inspect
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from travelcull.db.models import Base

# Directory holding the packaged Alembic environment (env.py, versions/). Ships
# inside the travelcull.db package so migrations are available wherever the
# package is installed, without relying on an alembic.ini in the CWD.
_MIGRATIONS_DIR = Path(__file__).parent / "migrations"

# Cache of (engine, sessionmaker) keyed by resolved db path, so repeated
# init_db() calls with the same path reuse a single Engine/pool instead of
# creating a new one each time (important since init_db is invoked once per
# ML pipeline stage, ~20 call sites, while the app also serves concurrent
# requests + background indexing writes against the same SQLite file).
_ENGINES: Dict[str, Tuple[Engine, "sessionmaker[Session]"]] = {}
_ENGINES_LOCK = threading.Lock()


def _make_url(db_path: Path) -> str:
    return f"sqlite:///{db_path}"


def _set_sqlite_pragmas(dbapi_connection, connection_record) -> None:  # noqa: ANN001
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=5000")
    finally:
        cursor.close()


def _alembic_config(connection):  # noqa: ANN001, ANN202
    """Build an in-code Alembic Config pointing at the packaged migrations dir
    and bound to an already-open *connection* (so migrations run on the same
    engine + WAL pragmas as the rest of the app, no alembic.ini required)."""
    from alembic.config import Config

    cfg = Config()
    cfg.set_main_option("script_location", str(_MIGRATIONS_DIR))
    cfg.attributes["connection"] = connection
    return cfg


def _ensure_schema(engine: Engine) -> None:
    """Bring *engine*'s database to the current schema via Alembic.

    Three cases:
      * Fresh DB (no tables): ``create_all`` then stamp head — fast path that
        avoids replaying every migration on brand-new databases.
      * Existing DB without an ``alembic_version`` table (every pre-Alembic
        travelcull DB in the wild): stamp the baseline revision, then upgrade to
        head. The follow-up revision that adds ``source`` to the photo_tags PK is
        a guarded no-op when the column is already present, so this is safe for
        DBs at any historical photo_tags shape.
      * DB already under Alembic control: upgrade to head.
    """
    from alembic import command
    from alembic.script import ScriptDirectory

    table_names = set(inspect(engine).get_table_names())

    if not table_names:
        with engine.begin() as conn:
            Base.metadata.create_all(conn)
        with engine.connect() as conn:
            command.stamp(_alembic_config(conn), "head")
        return

    if "alembic_version" not in table_names:
        with engine.connect() as conn:
            cfg = _alembic_config(conn)
            baseline = ScriptDirectory.from_config(cfg).get_base()
            command.stamp(cfg, baseline)
        with engine.connect() as conn:
            command.upgrade(_alembic_config(conn), "head")
        return

    with engine.connect() as conn:
        command.upgrade(_alembic_config(conn), "head")


def init_db(db_path: Path) -> sessionmaker[Session]:
    """Create all tables and return a sessionmaker bound to *db_path*.

    Idempotent: repeated calls with the same (resolved) path reuse the same
    cached Engine/sessionmaker rather than creating a new Engine each time,
    and ``create_all`` only runs once per path per process.
    """
    key = str(Path(db_path).resolve())
    with _ENGINES_LOCK:
        cached = _ENGINES.get(key)
        if cached is not None:
            return cached[1]

        db_path.parent.mkdir(parents=True, exist_ok=True)
        engine = create_engine(_make_url(db_path), echo=False)
        event.listens_for(engine, "connect")(_set_sqlite_pragmas)
        _ensure_schema(engine)
        factory = sessionmaker(bind=engine, expire_on_commit=False)
        _ENGINES[key] = (engine, factory)
        return factory


@contextmanager
def session_scope(Session: sessionmaker[Session]) -> Generator[Session, None, None]:  # noqa: N803
    """Provide a transactional session scope, committing on success."""
    session = Session()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
