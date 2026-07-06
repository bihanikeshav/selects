"""Tests for travelcull.video (frame sampling, dead-footage, highlights,
pipeline stage) and travelcull.server.video_routes."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from travelcull.config import get_folder_config
from travelcull.db import init_db, session_scope
from travelcull.db.models import Video
from travelcull.video import (
    FRAME_SAMPLES,
    VideoDecodeError,
    analyze_video,
    detect_highlights,
    extract_frames,
    frames_dir_for,
    run_video_stage,
)


# --------------------------------------------------------------------------- #
# Synthetic video helpers
# --------------------------------------------------------------------------- #

W, H, FPS = 128, 96, 15.0


def _write_video(path: Path, frames: list[np.ndarray], fps: float = FPS) -> None:
    import cv2

    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(str(path), fourcc, fps, (W, H))
    assert writer.isOpened(), "cv2.VideoWriter failed to open (mp4v)"
    try:
        for f in frames:
            writer.write(cv2.cvtColor(f, cv2.COLOR_RGB2BGR))
    finally:
        writer.release()


def _sharp_frame(seed: int) -> np.ndarray:
    """High-frequency noise: huge Laplacian variance, mid-gray exposure."""
    rng = np.random.default_rng(seed)
    return rng.integers(0, 256, size=(H, W, 3), dtype=np.uint8)


def _black_frame() -> np.ndarray:
    return np.zeros((H, W, 3), dtype=np.uint8)


@pytest.fixture()
def sharp_video(tmp_path: Path) -> Path:
    p = tmp_path / "sharp.mp4"
    _write_video(p, [_sharp_frame(i) for i in range(60)])
    return p


@pytest.fixture()
def black_video(tmp_path: Path) -> Path:
    p = tmp_path / "black.mp4"
    _write_video(p, [_black_frame() for _ in range(60)])
    return p


# --------------------------------------------------------------------------- #
# Frame extraction
# --------------------------------------------------------------------------- #


class TestExtractFrames:
    def test_extracts_n_evenly_spaced_frames(self, sharp_video: Path):
        info, frames = extract_frames(sharp_video, n=FRAME_SAMPLES)
        assert len(frames) == FRAME_SAMPLES
        assert info.width == W and info.height == H
        assert info.fps == pytest.approx(FPS, rel=0.01)
        assert info.duration_sec == pytest.approx(60 / FPS, rel=0.05)
        # timestamps strictly increasing and spanning the clip
        ts = [t for _, t, _ in frames]
        assert ts == sorted(ts)
        assert frames[0][0] == 0
        assert frames[-1][0] == 59
        # RGB uint8 arrays of the right shape
        assert frames[0][2].shape == (H, W, 3)
        assert frames[0][2].dtype == np.uint8

    def test_short_video_yields_fewer_frames(self, tmp_path: Path):
        p = tmp_path / "short.mp4"
        _write_video(p, [_sharp_frame(i) for i in range(5)])
        _, frames = extract_frames(p, n=FRAME_SAMPLES)
        assert 1 <= len(frames) <= 5

    def test_unopenable_file_raises(self, tmp_path: Path):
        p = tmp_path / "junk.mp4"
        p.write_bytes(b"\x00" * 200)
        with pytest.raises(VideoDecodeError):
            extract_frames(p)


# --------------------------------------------------------------------------- #
# Analysis: dead footage + highlights
# --------------------------------------------------------------------------- #


class TestAnalyzeVideo:
    def test_black_video_flagged_dead(self, black_video: Path):
        analysis, arrays = analyze_video(black_video)
        assert analysis.dead_footage is True
        assert analysis.highlights == []
        assert all(not f.good for f in analysis.frames)
        assert len(arrays) == len(analysis.frames)

    def test_sharp_video_not_dead_and_has_highlight(self, sharp_video: Path):
        analysis, _ = analyze_video(sharp_video)
        assert analysis.dead_footage is False
        assert all(f.good for f in analysis.frames)
        # one contiguous run spanning the whole strip
        assert len(analysis.highlights) == 1
        seg = analysis.highlights[0]
        assert seg["frames"] == len(analysis.frames)
        assert seg["start"] < seg["end"]
        assert analysis.best_index is not None
        assert 0.0 <= analysis.frames[analysis.best_index].quality <= 1.0

    def test_detect_highlights_runs(self):
        from travelcull.video import FrameScore

        def fs(i: int, good: bool) -> FrameScore:
            return FrameScore(
                index=i, frame_index=i, t_sec=float(i),
                blur=200.0 if good else 1.0,
                exposure=0.6 if good else 0.0,
                quality=0.8 if good else 0.1, good=good,
            )

        # good runs: [0..2], [5], [8..9] -> single-frame run dropped
        pattern = [True, True, True, False, False, True, False, False, True, True]
        segs = detect_highlights([fs(i, g) for i, g in enumerate(pattern)])
        assert segs == [
            {"start": 0.0, "end": 2.0, "frames": 3},
            {"start": 8.0, "end": 9.0, "frames": 2},
        ]


# --------------------------------------------------------------------------- #
# Pipeline stage
# --------------------------------------------------------------------------- #


def _ingest_video_row(cfg, path: Path, sha: str) -> int:
    Session = init_db(cfg.db_path)
    with session_scope(Session) as s:
        v = Video(path=str(path), sha256=sha, format="MP4")
        s.add(v)
        s.flush()
        return v.id


class TestRunVideoStage:
    def test_stage_populates_row_thumb_and_frames(self, tmp_path: Path, sharp_video: Path, monkeypatch):
        cfg = get_folder_config(tmp_path)
        sha = "a" * 64
        _ingest_video_row(cfg, sharp_video, sha)
        monkeypatch.setattr("travelcull.video._embed_best_frame", lambda img: b"\x01" * 8)

        progress: list[tuple[int, int, str]] = []
        n = run_video_stage(cfg, lambda i, t, name: progress.append((i, t, name)))
        assert n == 1
        assert progress and progress[-1][0] == progress[-1][1] == 1

        Session = init_db(cfg.db_path)
        with session_scope(Session) as s:
            v = s.query(Video).one()
            assert v.processed_at is not None
            assert v.dead_footage is False
            assert v.fps == pytest.approx(FPS, rel=0.01)
            assert v.frame_count == 60
            assert v.duration_sec == pytest.approx(4.0, rel=0.05)
            assert v.best_frame_index is not None
            assert v.sharpness is not None and v.sharpness > 0
            assert v.siglip == b"\x01" * 8
            frames = json.loads(v.frames_json)
            assert len(frames) == FRAME_SAMPLES
            assert len(json.loads(v.highlights_json)) == 1

        # best-frame thumb + preview written into the /api/thumb cache layout
        assert (cfg.thumbs_dir / f"{sha}.jpg").exists()
        assert (cfg.previews_dir / f"{sha}.jpg").exists()
        # filmstrip persisted
        strip = sorted(frames_dir_for(cfg, sha).glob("*.jpg"))
        assert len(strip) == FRAME_SAMPLES

    def test_stage_marks_black_video_dead(self, tmp_path: Path, black_video: Path):
        cfg = get_folder_config(tmp_path)
        _ingest_video_row(cfg, black_video, "b" * 64)
        assert run_video_stage(cfg, embed=False) == 1
        Session = init_db(cfg.db_path)
        with session_scope(Session) as s:
            v = s.query(Video).one()
            assert v.dead_footage is True
            assert v.highlights_json == "[]"

    def test_stage_survives_undecodable_video(self, tmp_path: Path):
        cfg = get_folder_config(tmp_path)
        bad = tmp_path / "bad.mp4"
        bad.write_bytes(b"\x00" * 300)
        _ingest_video_row(cfg, bad, "c" * 64)
        assert run_video_stage(cfg, embed=False) == 1
        Session = init_db(cfg.db_path)
        with session_scope(Session) as s:
            v = s.query(Video).one()
            # marked processed so the stage never loops on it
            assert v.processed_at is not None
            assert v.dead_footage is None
            assert json.loads(v.frames_json) == []

    def test_stage_idempotent(self, tmp_path: Path, black_video: Path):
        cfg = get_folder_config(tmp_path)
        _ingest_video_row(cfg, black_video, "d" * 64)
        assert run_video_stage(cfg, embed=False) == 1
        assert run_video_stage(cfg, embed=False) == 0


# --------------------------------------------------------------------------- #
# HTTP endpoints
# --------------------------------------------------------------------------- #


@pytest.fixture()
def client_with_videos(tmp_path: Path, sharp_video: Path, black_video: Path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from travelcull.server.video_routes import register_video_routes

    cfg = get_folder_config(tmp_path)
    _ingest_video_row(cfg, sharp_video, "a" * 64)
    _ingest_video_row(cfg, black_video, "b" * 64)
    monkeypatch.setattr("travelcull.video._embed_best_frame", lambda img: None)
    run_video_stage(cfg)

    app = FastAPI()
    register_video_routes(app, cfg)
    return TestClient(app)


class TestVideoRoutes:
    def test_list_videos(self, client_with_videos):
        r = client_with_videos.get("/api/videos")
        assert r.status_code == 200
        body = r.json()
        assert body["total"] == 2
        assert body["processed"] == 2
        assert body["dead_footage_count"] == 1
        by_sha = {v["sha256"]: v for v in body["videos"]}
        sharp, black = by_sha["a" * 64], by_sha["b" * 64]
        assert sharp["dead_footage"] is False
        assert sharp["highlight_count"] == 1
        assert sharp["thumb_url"] == f"/api/thumb/{'a' * 64}"
        assert sharp["sampled_frames"] == FRAME_SAMPLES
        assert black["dead_footage"] is True
        assert black["highlight_count"] == 0

    def test_frames_endpoint(self, client_with_videos):
        r = client_with_videos.get(f"/api/videos/{'a' * 64}/frames")
        assert r.status_code == 200
        body = r.json()
        assert len(body["frames"]) == FRAME_SAMPLES
        f0 = body["frames"][0]
        assert set(f0) >= {"index", "t_sec", "blur", "exposure", "quality", "good", "url"}
        assert f0["url"] == f"/api/videos/{'a' * 64}/frames/0"
        assert body["best_frame_index"] is not None
        assert len(body["highlights"]) == 1

    def test_frame_image_served(self, client_with_videos):
        r = client_with_videos.get(f"/api/videos/{'a' * 64}/frames/0")
        assert r.status_code == 200
        assert r.headers["content-type"] == "image/jpeg"
        assert r.content[:2] == b"\xff\xd8"

    def test_frames_404_unknown_sha(self, client_with_videos):
        assert client_with_videos.get(f"/api/videos/{'f' * 64}/frames").status_code == 404
        assert client_with_videos.get(f"/api/videos/{'a' * 64}/frames/99").status_code == 404
