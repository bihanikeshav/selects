from datetime import datetime

import pytest

from selects.config import get_folder_config
from selects.db import init_db, session_scope
from selects.db.models import Photo, PipelineState, Swipe
from selects.indexer.exif import ExifData
from selects.indexer.metadata import METADATA_VERSION
from selects.indexer.orchestrator import index_folder
from tests.test_exif import tagged_jpeg as tagged_jpeg
from tests.test_indexer_orchestrator import _dispose_engine


@pytest.fixture()
def legacy_library(tmp_path, tagged_jpeg):
    path = tmp_path / "семья" / "sample.jpg"
    path.parent.mkdir()
    path.write_bytes(tagged_jpeg)
    cfg = get_folder_config(tmp_path)
    try:
        assert index_folder(cfg) == 1
        with session_scope(init_db(cfg.db_path)) as session:
            photo = session.query(Photo).one()
            photo.taken_at = photo.camera = photo.gps_lat = photo.gps_lon = None
            photo.metadata_version = None
            state = session.get(PipelineState, photo.id)
            state.classical_done = state.embedding_done = state.vl_done = state.ordering_done = True
            session.add(Swipe(photo_id=photo.id, decision="keep"))
        yield cfg
    finally:
        _dispose_engine(cfg.db_path)


@pytest.mark.parametrize("partial", [False, True])
def test_reindex_recovers_metadata_without_reprocessing(legacy_library, monkeypatch, partial):
    cfg = legacy_library
    Session = init_db(cfg.db_path)
    with session_scope(Session) as session:
        photo = session.query(Photo).one()
        original_id, original_sha = photo.id, photo.sha256
        if partial:
            photo.camera = "Existing camera"
            photo.gps_lat = 0.0
        previews = [cfg.state_dir / photo.thumb_path, cfg.state_dir / photo.preview_path]
    original_mtimes = [path.stat().st_mtime_ns for path in previews]

    def unexpected(*args, **kwargs):
        pytest.fail("Metadata recovery must not decode photos or recreate previews")

    monkeypatch.setattr("selects.indexer.orchestrator.decode", unexpected)
    monkeypatch.setattr("selects.indexer.orchestrator.write_previews", unexpected)
    messages = []
    assert index_folder(cfg, on_progress=lambda i, total, text: messages.append(text)) == 1
    assert any("Recovered missing metadata for 1 file(s)" == text for text in messages)
    with session_scope(Session) as session:
        photo = session.query(Photo).one()
        assert (photo.id, photo.sha256) == (original_id, original_sha)
        assert photo.taken_at == datetime(2026, 1, 2, 3, 4, 5)
        assert photo.camera == ("Existing camera" if partial else "Test Камера")
        assert photo.gps_lat == (0.0 if partial else -12.5)
        assert photo.gps_lon == -45.25
        state = session.get(PipelineState, photo.id)
        assert state.classical_done and state.embedding_done and state.vl_done and state.ordering_done
        assert session.get(Swipe, photo.id).decision == "keep"
    assert [path.stat().st_mtime_ns for path in previews] == original_mtimes
    assert index_folder(cfg) == 0


def test_failed_backfill_can_be_retried(legacy_library, monkeypatch):
    cfg = legacy_library
    with monkeypatch.context() as patch:
        patch.setattr("selects.indexer.orchestrator.read_exif",
                      lambda path: ExifData(error="RuntimeError"))
        assert index_folder(cfg) == 0
    with session_scope(init_db(cfg.db_path)) as session:
        photo = session.query(Photo).one()
        assert photo.taken_at is photo.camera is photo.gps_lat is photo.gps_lon is None
    assert index_folder(cfg) == 1


@pytest.mark.parametrize("has_tags", [False, True])
def test_current_metadata_skips_all_metadata_work(legacy_library, monkeypatch, has_tags):
    cfg = legacy_library
    with monkeypatch.context() as patch:
        if not has_tags:
            patch.setattr("selects.indexer.orchestrator.read_exif", lambda path: ExifData())
        assert index_folder(cfg) == int(has_tags)
    with session_scope(init_db(cfg.db_path)) as session:
        assert session.query(Photo).one().metadata_version == METADATA_VERSION

    def unexpected(*args, **kwargs):
        pytest.fail("An unchanged file checked by this reader must skip all metadata work")

    monkeypatch.setattr("selects.indexer.orchestrator.read_exif", unexpected)
    monkeypatch.setattr("selects.indexer.orchestrator.probe", unexpected)
    monkeypatch.setattr("selects.indexer.orchestrator.decode", unexpected)
    monkeypatch.setattr("selects.indexer.orchestrator.write_previews", unexpected)
    assert index_folder(cfg) == 0


def test_recovered_metadata_triggers_downstream_rebuilds(legacy_library, monkeypatch):
    from selects.server import pipeline_runner

    called = []

    def get_stage(name):
        if name == "index":
            return index_folder

        def stage(cfg, on_progress=None):
            called.append(name)
            return 0

        return stage

    monkeypatch.setattr(pipeline_runner, "get_stage_callable", get_stage)
    monkeypatch.setattr(pipeline_runner, "_stage_has_output", lambda cfg, name: True)
    counts = pipeline_runner.run_pipeline_stages(legacy_library, lambda message: None)
    assert counts["index"] == 1
    assert {"moment", "story", "date"}.issubset(called)

    called.clear()
    counts = pipeline_runner.run_pipeline_stages(legacy_library, lambda message: None)
    assert counts["index"] == 0
    assert not {"moment", "story", "date"}.intersection(called)


async def test_reindex_button_endpoint_recovers_legacy_metadata(legacy_library, monkeypatch):
    from types import SimpleNamespace

    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from selects.server import libraries, pipeline_runner
    from selects.server.library_manager import LibraryManager

    cfg = legacy_library
    manager = LibraryManager(registry_path=cfg.folder / "registry.json")
    library = manager.add_library("Synthetic trip", str(cfg.folder))
    real_get_stage = pipeline_runner.get_stage_callable
    monkeypatch.setattr(pipeline_runner, "get_stage_callable", lambda name:
                        real_get_stage(name) if name == "index" else lambda *a, **kw: 0)

    class ImmediateThread:
        def __init__(self, target, **kwargs):
            self.target = target

        def start(self):
            self.target()

    monkeypatch.setattr(libraries, "threading", SimpleNamespace(Thread=ImmediateThread))
    app = FastAPI()
    libraries.register_libraries(app, manager, lambda message: None)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(f"/api/libraries/{library['id']}/index")
    assert response.status_code == 200
    assert response.json() == {"started": True}
    assert not manager.indexing
    with session_scope(init_db(cfg.db_path)) as session:
        photo = session.query(Photo).one()
        assert photo.taken_at == datetime(2026, 1, 2, 3, 4, 5)
        assert photo.camera == "Test Камера"
        assert photo.metadata_version == METADATA_VERSION


def test_existing_database_gets_recovery_marker_without_losing_rows(legacy_library):
    import sqlite3

    cfg = legacy_library
    _dispose_engine(cfg.db_path)
    with sqlite3.connect(cfg.db_path) as connection:
        connection.execute("ALTER TABLE photos DROP COLUMN metadata_version")
        connection.execute("ALTER TABLE videos DROP COLUMN metadata_version")
        connection.execute("UPDATE alembic_version SET version_num = 'c9d0e1f2a3b4'")
    Session = init_db(cfg.db_path)
    with session_scope(Session) as session:
        photo = session.query(Photo).one()
        assert photo.metadata_version is None
        assert session.get(Swipe, photo.id).decision == "keep"
    assert index_folder(cfg) == 1
