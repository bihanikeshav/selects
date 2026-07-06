"""Tests for Alembic-backed schema management in selects.db.init_db.

Covers:
  (a) fresh init_db -> alembic_version stamped at head, all tables present;
  (b) legacy DB (photo_tags WITHOUT source column, no alembic_version) is
      upgraded: source column added, PK includes source, stamped head;
  (c) post-hand-rolled DB (photo_tags WITH source, no alembic_version) is
      stamped + upgraded without error and without destroying rows;
  (d) init_db is idempotent across repeated calls.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from sqlalchemy import inspect

from selects.db import _MIGRATIONS_DIR, _ENGINES, _ENGINES_LOCK, init_db
from selects.db.models import Base


def _head_revision() -> str:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    cfg = Config()
    cfg.set_main_option("script_location", str(_MIGRATIONS_DIR))
    return ScriptDirectory.from_config(cfg).get_current_head()


def _stamped_revision(db_path: Path) -> str | None:
    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='alembic_version'"
        )
        if cur.fetchone() is None:
            return None
        row = conn.execute("SELECT version_num FROM alembic_version").fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def _photo_tags_pk(db_path: Path) -> list[str]:
    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.execute("PRAGMA table_info(photo_tags)")
        return [r[1] for r in cur.fetchall() if r[5] > 0]
    finally:
        conn.close()


def _photo_tags_columns(db_path: Path) -> set[str]:
    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.execute("PRAGMA table_info(photo_tags)")
        return {r[1] for r in cur.fetchall()}
    finally:
        conn.close()


def _forget_engine(db_path: Path) -> None:
    """Drop the cached engine for *db_path* so init_db re-runs _ensure_schema.

    Tests that pre-seed a DB on disk and then call init_db need the cache to be
    cold for that path within the process.
    """
    key = str(Path(db_path).resolve())
    with _ENGINES_LOCK:
        cached = _ENGINES.pop(key, None)
    if cached is not None:
        cached[0].dispose()


def test_fresh_init_db_stamps_head_and_creates_all_tables(tmp_path: Path) -> None:
    db_path = tmp_path / ".selects" / "index.db"

    init_db(db_path)

    head = _head_revision()
    assert _stamped_revision(db_path) == head

    tables = set(inspect(sqlite3_engine(db_path)).get_table_names())
    expected = set(Base.metadata.tables.keys())
    assert expected.issubset(tables)
    assert "alembic_version" in tables

    # Fresh DB gets the final source-in-PK shape directly from create_all.
    assert "source" in _photo_tags_columns(db_path)
    assert _photo_tags_pk(db_path) == ["photo_id", "tag", "source"]


def sqlite3_engine(db_path: Path):
    from sqlalchemy import create_engine

    return create_engine(f"sqlite:///{db_path}")


def _create_legacy_db(db_path: Path, *, with_source: bool) -> None:
    """Create a photo_tags DB the way create_all would have at an old schema
    point, WITHOUT an alembic_version table. If *with_source* is False the table
    predates the hand-rolled migration (PK photo_id, tag; no source column)."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "CREATE TABLE photos (id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "path VARCHAR(4096) NOT NULL)"
        )
        if with_source:
            conn.execute(
                "CREATE TABLE photo_tags ("
                "photo_id INTEGER NOT NULL, tag VARCHAR(128) NOT NULL, "
                "score FLOAT NOT NULL, source VARCHAR(16), "
                "PRIMARY KEY (photo_id, tag, source), "
                "FOREIGN KEY(photo_id) REFERENCES photos (id) ON DELETE CASCADE)"
            )
        else:
            conn.execute(
                "CREATE TABLE photo_tags ("
                "photo_id INTEGER NOT NULL, tag VARCHAR(128) NOT NULL, "
                "score FLOAT NOT NULL, "
                "PRIMARY KEY (photo_id, tag), "
                "FOREIGN KEY(photo_id) REFERENCES photos (id) ON DELETE CASCADE)"
            )
        conn.execute("CREATE INDEX ix_photo_tags_tag ON photo_tags (tag)")
        conn.commit()
    finally:
        conn.close()


def test_legacy_db_without_source_is_upgraded(tmp_path: Path) -> None:
    db_path = tmp_path / ".selects" / "index.db"
    _create_legacy_db(db_path, with_source=False)

    # Seed a pre-existing (NULL-source) tag row to prove data survives.
    conn = sqlite3.connect(str(db_path))
    conn.execute("INSERT INTO photos (id, path) VALUES (1, '/a.jpg')")
    conn.execute("INSERT INTO photo_tags (photo_id, tag, score) VALUES (1, 'mountain', 0.9)")
    conn.commit()
    conn.close()

    assert _stamped_revision(db_path) is None  # no alembic tracking yet

    _forget_engine(db_path)
    init_db(db_path)

    assert _stamped_revision(db_path) == _head_revision()
    assert "source" in _photo_tags_columns(db_path)
    assert _photo_tags_pk(db_path) == ["photo_id", "tag", "source"]

    # Legacy row preserved (source became NULL).
    conn = sqlite3.connect(str(db_path))
    rows = conn.execute("SELECT photo_id, tag, score, source FROM photo_tags").fetchall()
    conn.close()
    assert (1, "mountain", 0.9, None) in rows


def test_post_handrolled_db_with_source_stamps_and_preserves_rows(tmp_path: Path) -> None:
    db_path = tmp_path / ".selects" / "index.db"
    _create_legacy_db(db_path, with_source=True)

    conn = sqlite3.connect(str(db_path))
    conn.execute("INSERT INTO photos (id, path) VALUES (1, '/a.jpg')")
    conn.execute(
        "INSERT INTO photo_tags (photo_id, tag, score, source) VALUES (1, 'beach', 1.0, 'ram')"
    )
    conn.commit()
    conn.close()

    assert _stamped_revision(db_path) is None

    _forget_engine(db_path)
    init_db(db_path)

    assert _stamped_revision(db_path) == _head_revision()
    assert _photo_tags_pk(db_path) == ["photo_id", "tag", "source"]

    conn = sqlite3.connect(str(db_path))
    rows = conn.execute("SELECT photo_id, tag, score, source FROM photo_tags").fetchall()
    conn.close()
    assert (1, "beach", 1.0, "ram") in rows


def test_init_db_is_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / ".selects" / "index.db"

    factory1 = init_db(db_path)
    head = _stamped_revision(db_path)
    factory2 = init_db(db_path)

    # Same cached sessionmaker returned, schema unchanged / still at head.
    assert factory1 is factory2
    assert _stamped_revision(db_path) == head == _head_revision()

    # A second cold open (cache cleared) is still a no-op upgrade.
    _forget_engine(db_path)
    init_db(db_path)
    assert _stamped_revision(db_path) == _head_revision()
