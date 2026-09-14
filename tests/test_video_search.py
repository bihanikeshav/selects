"""Targeted tests for sparse video semantic indexing and search."""
from __future__ import annotations

from datetime import datetime

import numpy as np
from fastapi import FastAPI
from fastapi.testclient import TestClient

from selects.config import get_folder_config
from selects.db import init_db, session_scope
from selects.db.models import Embedding, Photo, Video, VideoKeyframe, VideoSegment
from selects.ml import video_search
from selects.server.search2_routes import register_search2_routes


def _blob(vector: np.ndarray) -> bytes:
    vector = vector.astype(np.float32)
    vector /= np.linalg.norm(vector) + 1e-12
    return vector.astype(np.float16).tobytes()


def _frame(value: int, width: int = 64, height: int = 36) -> np.ndarray:
    return np.full((height, width, 3), value, dtype=np.uint8)


def test_frame_cap_has_three_bounded_duration_tiers():
    assert video_search.frame_cap(12) == 24
    assert video_search.frame_cap(60) == 24
    assert video_search.frame_cap(61) == 48
    assert video_search.frame_cap(600) == 48
    assert video_search.frame_cap(601) == 64


def test_adaptive_selection_keeps_anchors_and_adds_scene_change():
    rows = [(index, float(index), _frame(0 if index < 8 else 255)) for index in range(16)]
    selected = video_search.select_adaptive_frames(rows, duration_sec=16, cap=16, scene_threshold=0.05)

    assert len(selected) <= 16
    assert selected[0].t_sec == 0.0
    assert selected[-1].t_sec == 15.0
    assert any(frame.kind == "scene" for frame in selected)
    assert all(len(frame.scene_hash) == 16 for frame in selected)


def test_segment_plan_is_timecoded_and_bounded():
    rows = [
        video_search.SampledFrame(i, float(i * 2), _frame(i), "anchor", str(i))
        for i in range(6)
    ]
    plans = video_search.build_segment_plan(rows, duration_sec=20)

    assert [(plan.start_sec, plan.end_sec) for plan in plans] == [(0.0, 8.0), (8.0, 20.0)]
    assert plans[0].frame_indexes == (0, 1, 2, 3)
    assert plans[1].frame_indexes == (4, 5)


def test_keyframes_are_embedded_in_batches(monkeypatch):
    calls: list[int] = []

    def fake_encode(images):
        calls.append(len(images))
        return np.ones((len(images), 4), dtype=np.float32), np.zeros(len(images), dtype=np.float32)

    monkeypatch.setattr("selects.ml.embed.encode_image_batch", fake_encode)
    monkeypatch.setattr("selects.ml.onnx_rt.all_present", lambda: True)
    rows = [
        video_search.SampledFrame(i, float(i), _frame(i), "anchor", str(i))
        for i in range(5)
    ]

    blobs = video_search.encode_keyframes(rows, batch_size=2)

    assert calls == [2, 2, 1]
    assert len(blobs) == 5
    assert all(blob is not None and len(blob) == 8 for blob in blobs)


def test_segment_match_replaces_best_frame_when_refined_score_is_better(monkeypatch):
    cfg = object()
    query = np.array([1.0, 0.0], dtype=np.float32)
    monkeypatch.setattr(
        video_search,
        "best_frame_matrix",
        lambda _cfg: (np.array([[0.7, 0.71414286]], dtype=np.float32), [{"video_id": 7, "sha256": "v" * 64}]),
    )
    monkeypatch.setattr(
        video_search,
        "segment_matrix",
        lambda _cfg: (
            np.array([[0.95, 0.3122499], [0.8, 0.6]], dtype=np.float32),
            [
                {"segment_id": 1, "video_id": 7, "sha256": "v" * 64, "start_sec": 10.0, "end_sec": 18.0, "kind": "scene"},
                {"segment_id": 2, "video_id": 7, "sha256": "v" * 64, "start_sec": 30.0, "end_sec": 35.0, "kind": "scene"},
            ],
        ),
    )

    results = video_search.score_video_search(cfg, query, limit=5)

    assert results[0]["match_kind"] == "segment"
    assert results[0]["match_start_sec"] == 10.0
    assert results[0]["match_end_sec"] == 18.0


def test_persist_index_uses_landed_millisecond_schema(tmp_path, monkeypatch):
    cfg = get_folder_config(tmp_path)
    Session = init_db(cfg.db_path)
    with session_scope(Session) as session:
        video = Video(path=str(tmp_path / "clip.mp4"), sha256="s" * 64, duration_sec=4.0)
        session.add(video)
        session.flush()
        video_id = video.id

    frames = [
        video_search.SampledFrame(0, 0.5, _frame(20), "anchor", "a" * 16),
        video_search.SampledFrame(30, 2.5, _frame(220), "scene", "b" * 16),
    ]
    monkeypatch.setattr(video_search, "adaptive_keyframes", lambda _path, _duration: (None, frames))
    monkeypatch.setattr(video_search, "encode_keyframes", lambda _frames: [_blob(np.array([1.0, 0.0, 0.0, 0.0])), _blob(np.array([0.0, 1.0, 0.0, 0.0]))])
    monkeypatch.setattr(video_search, "_score_selected_frame", lambda _image: (120.0, 0.7, 0.8, True))
    monkeypatch.setattr("selects.indexer.preview._resize_and_save", lambda *_args: None)

    assert video_search.persist_index(cfg, video_id, "s" * 64, tmp_path / "clip.mp4", 4.0, best_frame_index=30) == 2

    with session_scope(Session) as session:
        keyframes = session.query(VideoKeyframe).order_by(VideoKeyframe.timestamp_ms).all()
        segments = session.query(VideoSegment).all()
        assert [row.timestamp_ms for row in keyframes] == [500, 2500]
        assert keyframes[1].kind == "quality"
        assert keyframes[0].source_fingerprint == video_search.source_fingerprint("s" * 64)
        assert segments[0].start_ms == 500
        assert segments[0].end_ms == 4000
        assert segments[0].embedding is not None
        assert segments[0].representative_keyframe_id == keyframes[0].id

    matrix, metadata = video_search.segment_matrix(cfg)
    assert matrix.shape == (1, 4)
    assert metadata[0]["start_sec"] == 0.5
    assert metadata[0]["end_sec"] == 4.0


def test_search2_returns_immediate_best_frame_video_result(tmp_path, monkeypatch):
    cfg = get_folder_config(tmp_path)
    Session = init_db(cfg.db_path)
    vector = np.zeros(1152, dtype=np.float32)
    vector[0] = 1.0
    with session_scope(Session) as session:
        session.add(Video(
            path=str(tmp_path / "clip.mp4"),
            sha256="v" * 64,
            format="MP4",
            taken_at=datetime(2025, 1, 1),
            siglip=_blob(vector),
        ))

    monkeypatch.setattr("selects.ml.search.embed_query", lambda _query: vector)
    app = FastAPI()
    register_search2_routes(app, cfg)

    response = TestClient(app).get("/api/search2?q=lake&media=videos")

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    result = body["results"][0]
    assert result["asset_type"] == "video"
    assert result["video_id"]
    assert result["match_kind"] == "best-frame"
    assert result["match_start_sec"] is None


def test_search2_media_all_preserves_photo_fields_and_adds_video_shape(tmp_path, monkeypatch):
    cfg = get_folder_config(tmp_path)
    Session = init_db(cfg.db_path)
    vector = np.zeros(1152, dtype=np.float32)
    vector[0] = 1.0
    with session_scope(Session) as session:
        photo = Photo(path=str(tmp_path / "photo.jpg"), sha256="p" * 64, taken_at=datetime(2025, 1, 1))
        session.add(photo)
        session.flush()
        session.add(Embedding(photo_id=photo.id, siglip=_blob(vector), aesthetic_iqa=0.8))
        session.add(Video(path=str(tmp_path / "clip.mp4"), sha256="v" * 64, format="MP4", siglip=_blob(vector)))

    monkeypatch.setattr("selects.ml.search.embed_query", lambda _query: vector)
    app = FastAPI()
    register_search2_routes(app, cfg)

    response = TestClient(app).get("/api/search2?q=lake&media=all")

    assert response.status_code == 200
    results = response.json()["results"]
    assert {result["asset_type"] for result in results} == {"photo", "video"}
    photo = next(result for result in results if result["asset_type"] == "photo")
    assert {"photo_id", "sha256", "thumb_url", "preview_url"} <= photo.keys()
    video = next(result for result in results if result["asset_type"] == "video")
    assert {"video_id", "match_start_sec", "match_end_sec", "match_kind"} <= video.keys()


def test_search2_returns_transcript_range(tmp_path, monkeypatch):
    from sqlalchemy import text

    from selects.db.models import VideoTranscriptSegment

    cfg = get_folder_config(tmp_path)
    Session = init_db(cfg.db_path)
    vector = np.zeros(1152, dtype=np.float32)
    vector[0] = 1.0
    with session_scope(Session) as session:
        video = Video(path=str(tmp_path / "clip.mp4"), sha256="v" * 64, format="MP4", siglip=_blob(vector))
        session.add(video)
        session.flush()
        row = VideoTranscriptSegment(
            video_id=video.id,
            start_ms=12000,
            end_ms=15000,
            text="he laughed at the lake",
            source_fingerprint="src",
        )
        session.add(row)
        session.flush()
        session.execute(
            text("INSERT INTO video_transcript_fts(rowid, text) VALUES (:id, :text)"),
            {"id": row.id, "text": row.text},
        )

    monkeypatch.setattr("selects.ml.search.embed_query", lambda _query: vector)
    app = FastAPI()
    register_search2_routes(app, cfg)
    response = TestClient(app).get("/api/search2?q=laughed&media=videos")
    assert response.status_code == 200
    hit = next(item for item in response.json()["results"] if item["match_kind"] == "transcript")
    assert hit["match_start_sec"] == 12.0
    assert hit["match_end_sec"] == 15.0
