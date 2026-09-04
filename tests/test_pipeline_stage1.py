import shutil
from pathlib import Path

import pytest

from selects.config import get_folder_config
from selects.db import init_db, session_scope
from selects.db.models import ClassicalScore, PipelineState
from selects.indexer.orchestrator import index_folder
from selects.pipeline import run_classical_stage

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def populated_folder(tmp_path) -> Path:
    """Override conftest's populated_folder with real-file copies for decode-dependent tests."""
    for f in FIXTURES_DIR.iterdir():
        if f.is_file() and f.suffix.lower() in {".jpg", ".jpeg", ".heic", ".heif", ".mp4"}:
            shutil.copy(f, tmp_path / f.name)
    return tmp_path


def test_stage1_writes_classical_scores(populated_folder):
    cfg = get_folder_config(populated_folder)
    Session = init_db(cfg.db_path)
    index_folder(cfg)
    run_classical_stage(cfg)
    with session_scope(Session) as s:
        scores = s.query(ClassicalScore).all()
        states = s.query(PipelineState).all()
        assert len(scores) >= 2
        assert all(p.classical_done for p in states)


def test_stage1_is_idempotent(populated_folder):
    cfg = get_folder_config(populated_folder)
    init_db(cfg.db_path)
    index_folder(cfg)
    run_classical_stage(cfg)
    n2 = run_classical_stage(cfg)
    assert n2 == 0


def test_score_one_writes_eyes_open_ratio(populated_folder, monkeypatch):
    from types import SimpleNamespace

    import numpy as np

    from selects.ml.face_attributes import photo_eyes_open_ratio

    cfg = get_folder_config(populated_folder)
    Session = init_db(cfg.db_path)
    index_folder(cfg)

    eye_w, eye_h = 20.0, 0.35 * 20.0
    centers = np.array([[40.0, 50.0], [80.0, 50.0]])
    pts = []
    for cx, cy in centers:
        for ang in np.linspace(0, 2 * np.pi, 8, endpoint=False):
            pts.append([cx + (eye_w / 2) * np.cos(ang), cy + (eye_h / 2) * np.sin(ang)])
    for i in range(90):
        pts.append([10.0 + i, 200.0 + (i % 7)])
    landmarks = np.array(pts, dtype=np.float64)
    kps = np.array(
        [[40.0, 50.0], [80.0, 50.0], [60.0, 75.0], [48.0, 100.0], [72.0, 100.0]]
    )
    fake_face = SimpleNamespace(
        x=10, y=10, w=40, h=40, confidence=0.9, embedding=None, kps=kps, landmark_2d_106=landmarks, pose=None
    )
    assert photo_eyes_open_ratio([fake_face]) == 1.0

    monkeypatch.setattr("selects.pipeline.detect_faces", lambda _img: [fake_face])
    run_classical_stage(cfg)
    with session_scope(Session) as s:
        scores = s.query(ClassicalScore).all()
        assert scores
        assert all(sc.eyes_open_ratio == 1.0 for sc in scores)
        assert all(sc.faces_count == 1 for sc in scores)
