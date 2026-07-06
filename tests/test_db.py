"""Tests for selects.db (models + session)."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from selects.db import init_db, session_scope
from selects.db.models import ClassicalScore, Photo, PipelineState, Video


@pytest.fixture()
def session_factory(tmp_path: Path):
    db_path = tmp_path / "test.db"
    return init_db(db_path)


class TestInitDb:
    def test_creates_db_file(self, tmp_path: Path) -> None:
        db_path = tmp_path / "sub" / "index.db"
        init_db(db_path)
        assert db_path.exists()

    def test_creates_tables(self, session_factory) -> None:
        with session_scope(session_factory) as session:
            # Simply querying each table confirms it exists
            assert session.query(Photo).count() == 0
            assert session.query(Video).count() == 0
            assert session.query(ClassicalScore).count() == 0
            assert session.query(PipelineState).count() == 0


class TestPhotoInsertRetrieve:
    def test_insert_and_retrieve_photo(self, session_factory) -> None:
        with session_scope(session_factory) as session:
            photo = Photo(
                path="/photos/img001.jpg",
                sha256="a" * 64,
                mtime=1_700_000_000.0,
                size_bytes=1_024_000,
                format="JPEG",
                width=4032,
                height=3024,
                taken_at=datetime(2024, 1, 15, 10, 30, 0),
                camera="Apple iPhone 15 Pro",
            )
            session.add(photo)

        with session_scope(session_factory) as session:
            retrieved = session.query(Photo).filter_by(path="/photos/img001.jpg").one()
            assert retrieved.sha256 == "a" * 64
            assert retrieved.width == 4032
            assert retrieved.camera == "Apple iPhone 15 Pro"

    def test_insert_video(self, session_factory) -> None:
        with session_scope(session_factory) as session:
            video = Video(
                path="/videos/clip.mp4",
                format="MP4",
                duration_sec=12.5,
            )
            session.add(video)

        with session_scope(session_factory) as session:
            retrieved = session.query(Video).filter_by(path="/videos/clip.mp4").one()
            assert retrieved.duration_sec == pytest.approx(12.5)


class TestPipelineStateDefaults:
    def test_pipeline_state_defaults_false(self, session_factory) -> None:
        with session_scope(session_factory) as session:
            photo = Photo(path="/photos/test.jpg")
            session.add(photo)
            session.flush()
            state = PipelineState(photo_id=photo.id)
            session.add(state)

        with session_scope(session_factory) as session:
            photo = session.query(Photo).filter_by(path="/photos/test.jpg").one()
            state = session.query(PipelineState).filter_by(photo_id=photo.id).one()
            assert state.classical_done is False
            assert state.embedding_done is False
            assert state.vl_done is False
            assert state.ordering_done is False
            assert state.error is None

    def test_classical_score_auto_reject_default(self, session_factory) -> None:
        with session_scope(session_factory) as session:
            photo = Photo(path="/photos/score_test.jpg")
            session.add(photo)
            session.flush()
            score = ClassicalScore(photo_id=photo.id, blur=120.5)
            session.add(score)

        with session_scope(session_factory) as session:
            photo = session.query(Photo).filter_by(path="/photos/score_test.jpg").one()
            score = session.query(ClassicalScore).filter_by(photo_id=photo.id).one()
            assert score.auto_reject is False
            assert score.blur == pytest.approx(120.5)
