"""Focused contract and migration tests for the additive video schema."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from alembic import command
from sqlalchemy import create_engine, inspect

from selects.db import (
    _ENGINES,
    _ENGINES_LOCK,
    _alembic_config,
    init_db,
    session_scope,
)
from selects.db.models import (
    Base,
    MediaJob,
    Video,
    VideoAudioAnalysis,
    VideoCollection,
    VideoCollectionItem,
    VideoEdit,
    VideoKeyframe,
    VideoProxy,
    VideoRating,
    VideoSegment,
    VideoTag,
    VideoTranscriptSegment,
)


PREVIOUS_HEAD = "b1c2d3e4f5a6"
CURRENT_HEAD = "f7a8b9c0d1e2"


def _forget_engine(db_path: Path) -> None:
    key = str(db_path.resolve())
    with _ENGINES_LOCK:
        cached = _ENGINES.pop(key, None)
    if cached is not None:
        cached[0].dispose()


def _build_db_at(db_path: Path, revision: str) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite:///{db_path}")
    try:
        with engine.connect() as connection:
            command.upgrade(_alembic_config(connection), revision)
            connection.commit()
    finally:
        engine.dispose()


def _fk_actions(db_path: Path, table: str) -> dict[str, tuple[str, str]]:
    connection = sqlite3.connect(str(db_path))
    try:
        rows = connection.execute(f"PRAGMA foreign_key_list({table})").fetchall()
        return {row[3]: (row[2], row[6]) for row in rows}
    finally:
        connection.close()


def test_video_schema_contract_is_additive() -> None:
    expected = {
        "video_keyframes",
        "video_segments",
        "video_proxies",
        "video_audio_analysis",
        "video_transcript_segments",
        "video_edits",
        "video_ratings",
        "video_tags",
        "video_collections",
        "video_collection_items",
        "media_jobs",
    }
    assert expected.issubset(Base.metadata.tables)
    assert {"photos", "videos"}.issubset(Base.metadata.tables)

    assert {
        "video_id",
        "frame_index",
        "timestamp_ms",
        "siglip",
        "source_fingerprint",
        "processor_version",
        "model_version",
        "index_version",
        "created_at",
        "updated_at",
    }.issubset(set(Base.metadata.tables["video_keyframes"].columns.keys()))
    assert {
        "video_id",
        "start_ms",
        "end_ms",
        "embedding",
        "ocr_text",
        "representative_keyframe_id",
    }.issubset(set(Base.metadata.tables["video_segments"].columns.keys()))
    assert {
        "video_id",
        "audio_present",
        "waveform_blob",
        "silence_segments_json",
        "speech_segments_json",
    }.issubset(set(Base.metadata.tables["video_audio_analysis"].columns.keys()))
    assert {"video_id", "start_ms", "end_ms", "text", "language"}.issubset(
        set(Base.metadata.tables["video_transcript_segments"].columns.keys())
    )
    assert {"video_id", "schema_version", "recipe_json", "source_fingerprint"}.issubset(
        set(Base.metadata.tables["video_edits"].columns.keys())
    )
    assert {"video_id", "rating", "rated_at"}.issubset(
        set(Base.metadata.tables["video_ratings"].columns.keys())
    )
    assert {"video_id", "tag", "source", "score"}.issubset(
        set(Base.metadata.tables["video_tags"].columns.keys())
    )
    assert {"name", "kind", "query_json", "created_at", "updated_at"}.issubset(
        set(Base.metadata.tables["video_collections"].columns.keys())
    )
    assert {"collection_id", "video_id", "position", "added_at"}.issubset(
        set(Base.metadata.tables["video_collection_items"].columns.keys())
    )
    assert {
        "video_id",
        "job_type",
        "status",
        "progress",
        "payload_json",
        "cancel_requested",
        "started_at",
        "finished_at",
    }.issubset(set(Base.metadata.tables["media_jobs"].columns.keys()))


def test_migration_adds_video_schema_and_cascade_graph(tmp_path: Path) -> None:
    db_path = tmp_path / ".selects" / "index.db"
    _build_db_at(db_path, PREVIOUS_HEAD)

    _forget_engine(db_path)
    Session = init_db(db_path)
    database_inspector = inspect(Session.kw["bind"])
    expected_tables = {
        "video_keyframes",
        "video_segments",
        "video_proxies",
        "video_audio_analysis",
        "video_transcript_segments",
        "video_edits",
        "video_ratings",
        "video_tags",
        "video_collections",
        "video_collection_items",
        "media_jobs",
    }
    assert expected_tables.issubset(set(database_inspector.get_table_names()))

    for table in expected_tables - {"video_collections"}:
        foreign_keys = _fk_actions(db_path, table)
        assert all(action == "CASCADE" for _, action in foreign_keys.values()), table

    with session_scope(Session) as session:
        video = Video(path="/library/clip.mp4", sha256="a" * 64, duration_sec=12.5)
        collection = VideoCollection(name="Trip selects")
        session.add_all([video, collection])
        session.flush()

        keyframe = VideoKeyframe(
            video_id=video.id,
            frame_index=3,
            timestamp_ms=2200,
            kind="scene",
            siglip=b"embedding",
            source_fingerprint="source-1",
            processor_version="keyframes-1",
            model_version="siglip-1",
            index_version="video-index-1",
        )
        session.add(keyframe)
        session.flush()
        session.add_all(
            [
                VideoSegment(
                    video_id=video.id,
                    start_ms=2000,
                    end_ms=4000,
                    representative_keyframe_id=keyframe.id,
                    embedding=b"segment",
                    source_fingerprint="source-1",
                    processor_version="segments-1",
                    model_version="siglip-1",
                    index_version="video-index-1",
                ),
                VideoProxy(
                    video_id=video.id,
                    preset="preview",
                    path="/cache/preview.mp4",
                    source_fingerprint="source-1",
                    processor_version="proxy-1",
                ),
                VideoAudioAnalysis(
                    video_id=video.id,
                    source_fingerprint="source-1",
                    audio_present=True,
                    waveform_blob=b"waveform",
                    processor_version="audio-1",
                ),
                VideoTranscriptSegment(
                    video_id=video.id,
                    start_ms=1000,
                    end_ms=2500,
                    text="mountain lake",
                    source_fingerprint="source-1",
                    processor_version="transcript-1",
                    model_version="whisper-1",
                ),
                VideoEdit(
                    video_id=video.id,
                    recipe_json='{"trim":{"in_ms":1000,"out_ms":4000}}',
                    source_fingerprint="source-1",
                    schema_version=1,
                ),
                VideoRating(video_id=video.id, rating=1),
                VideoTag(video_id=video.id, tag="lake", source="manual", score=1.0),
                MediaJob(
                    video_id=video.id,
                    job_type="export",
                    status="queued",
                    payload_json="{}",
                    source_fingerprint="source-1",
                ),
            ]
        )
        session.add(VideoCollectionItem(collection_id=collection.id, video_id=video.id, position=0))

    with session_scope(Session) as session:
        video = session.query(Video).one()
        session.delete(video)

    with session_scope(Session) as session:
        assert session.query(Video).count() == 0
        for model in (
            VideoKeyframe,
            VideoSegment,
            VideoProxy,
            VideoAudioAnalysis,
            VideoTranscriptSegment,
            VideoEdit,
            VideoRating,
            VideoTag,
            VideoCollectionItem,
            MediaJob,
        ):
            assert session.query(model).count() == 0, model.__tablename__
        assert session.query(VideoCollection).count() == 1

    assert _read_version(db_path) == CURRENT_HEAD


def _read_version(db_path: Path) -> str:
    connection = sqlite3.connect(str(db_path))
    try:
        return connection.execute("SELECT version_num FROM alembic_version").fetchone()[0]
    finally:
        connection.close()
