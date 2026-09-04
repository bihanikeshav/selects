"""Shared pytest fixtures for selects tests."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

import pytest
from sqlalchemy import event


FIXTURES_DIR = Path(__file__).parent / "fixtures"


def engine_for(db_path) -> "object":
    """Return the cached SQLAlchemy engine ``init_db`` built for *db_path*."""
    from selects.db import _ENGINES, _ENGINES_LOCK

    with _ENGINES_LOCK:
        return _ENGINES[str(Path(db_path).resolve())][0]


@contextmanager
def sqlite_999_variables(engine, limit: int = 999):
    """Pin SQLite's bound-variable ceiling on *engine* for the block.

    The bundled SQLite here allows 32766 variables per statement, which hides
    "too many SQL variables" bugs that a user's system SQLite (999 on many
    builds) would hit. Pinning the classic 999 makes the regression tests fail
    on an unchunked ``IN (...)`` on every machine.

    The listener is always removed on the way out, and the pool is disposed on
    entry and exit so connections are rebuilt with (and then without) the cap.
    """
    def _cap_variables(dbapi_conn, _rec):  # pragma: no cover - trivial
        dbapi_conn.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, limit)

    registered = False
    try:
        # Registration and the entry dispose live inside the try, so a failure
        # on the way in still runs the removal below.
        event.listen(engine, "connect", _cap_variables)
        registered = True
        engine.dispose()
        yield engine
    finally:
        if registered:
            event.remove(engine, "connect", _cap_variables)
        engine.dispose()


@pytest.fixture()
def pin_sqlite_variables():
    """Fixture form of :func:`sqlite_999_variables` — call it with an engine or
    a db path and use the result as a context manager."""
    @contextmanager
    def _pin(engine_or_path, limit: int = 999):
        engine = engine_or_path
        if isinstance(engine_or_path, (str, Path)):
            engine = engine_for(engine_or_path)
        with sqlite_999_variables(engine, limit) as eng:
            yield eng

    return _pin


@pytest.fixture(autouse=True)
def isolate_registry(tmp_path_factory, monkeypatch) -> None:
    """Point the multi-library registry at an isolated temp file for every test
    so nothing ever touches the real ~/.selects/libraries.json."""
    reg = tmp_path_factory.mktemp("registry") / "libraries.json"
    monkeypatch.setenv("SELECTS_REGISTRY", str(reg))


@pytest.fixture()
def fixtures_dir() -> Path:
    """Return the path to the static fixtures directory."""
    return FIXTURES_DIR


@pytest.fixture()
def tmp_folder(tmp_path: Path) -> Path:
    """Return an empty temporary directory."""
    return tmp_path


@pytest.fixture()
def populated_folder(tmp_path: Path) -> Path:
    """Return a temporary directory pre-populated with sample files."""
    # Create a small directory tree with varied file types
    (tmp_path / "photos").mkdir()
    (tmp_path / "photos" / "sub").mkdir()
    (tmp_path / "videos").mkdir()
    (tmp_path / ".selects").mkdir()
    (tmp_path / ".git").mkdir()

    # Create dummy files
    (tmp_path / "photos" / "img001.jpg").write_bytes(b"\xff\xd8\xff" + b"\x00" * 100)
    (tmp_path / "photos" / "img002.HEIC").write_bytes(b"\x00" * 100)
    (tmp_path / "photos" / "sub" / "img003.jpeg").write_bytes(b"\xff\xd8\xff" + b"\x00" * 100)
    (tmp_path / "videos" / "clip001.mp4").write_bytes(b"\x00" * 200)
    (tmp_path / ".selects" / "hidden.jpg").write_bytes(b"\xff\xd8\xff" + b"\x00" * 100)
    (tmp_path / ".git" / "config").write_bytes(b"[core]\n")
    (tmp_path / "readme.txt").write_bytes(b"not a media file")

    return tmp_path
