"""travelcull database session management."""
from __future__ import annotations

import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, Generator, Tuple

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from travelcull.db.models import Base

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
        Base.metadata.create_all(engine)
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
