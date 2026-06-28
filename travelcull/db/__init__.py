"""travelcull database session management."""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from travelcull.db.models import Base


def _make_url(db_path: Path) -> str:
    return f"sqlite:///{db_path}"


def init_db(db_path: Path) -> sessionmaker[Session]:
    """Create all tables and return a sessionmaker bound to *db_path*."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(_make_url(db_path), echo=False)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


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
