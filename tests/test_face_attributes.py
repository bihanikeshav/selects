"""Tests for face attributes (eyes-closed / head-pose aware culling).

Covers:
  * EAR math with synthetic landmarks (open vs closed eyes)
  * kps-based head-pose estimation (frontal vs turned)
  * photo-level rollups (any_eyes_closed / all_looking_away)
  * contextual burst penalty rules (single face / 3+ frontal group / cap)
  * curation burst pick: eyes-open frame wins among near-equal candidates,
    a big aesthetic gap is never overridden
  * /api/photos/{sha256}/face_quality endpoint
  * alembic migration upgrade on a legacy DB missing the attribute columns
"""
from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from travelcull.config import get_folder_config
from travelcull.db import _ENGINES, _ENGINES_LOCK, init_db, session_scope
from travelcull.db.models import (
    AestheticScore, FaceEmbedding, Moment, MomentMember, Photo,
)
from travelcull.ml.face_attributes import (
    EYES_CLOSED_THRESHOLD,
    PENALTY_CAP,
    FaceAttrs,
    estimate_pose_from_kps,
    eyes_open_score,
    face_quality_penalty,
    rollup_face_quality,
)


# ── synthetic landmark helpers ────────────────────────────────────────────────

def _synthetic_landmarks(eye_ratio: float) -> tuple[np.ndarray, np.ndarray]:
    """Build a 106-ish landmark array with two eye clusters whose
    height/width extent ratio is *eye_ratio*, plus far-away filler points.
    Returns (landmarks, kps)."""
    eye_w = 20.0
    eye_h = eye_ratio * eye_w
    centers = np.array([[40.0, 50.0], [80.0, 50.0]])  # left eye, right eye

    pts = []
    for cx, cy in centers:
        # 8 points on an ellipse around the eye center
        for ang in np.linspace(0, 2 * np.pi, 8, endpoint=False):
            pts.append([cx + (eye_w / 2) * np.cos(ang), cy + (eye_h / 2) * np.sin(ang)])
    # Filler points far from both eyes (mouth/contour region)
    for i in range(90):
        pts.append([10.0 + i, 200.0 + (i % 7)])
    landmarks = np.array(pts, dtype=np.float64)

    kps = np.array(
        [[40.0, 50.0], [80.0, 50.0], [60.0, 75.0], [48.0, 100.0], [72.0, 100.0]]
    )
    return landmarks, kps


def test_eyes_open_score_open_vs_closed() -> None:
    open_lmk, kps = _synthetic_landmarks(eye_ratio=0.35)
    closed_lmk, _ = _synthetic_landmarks(eye_ratio=0.05)

    open_score = eyes_open_score(open_lmk, kps)
    closed_score = eyes_open_score(closed_lmk, kps)

    assert open_score > 0.8
    assert closed_score < 0.1
    assert closed_score < EYES_CLOSED_THRESHOLD < open_score


def test_eyes_open_score_intermediate_is_monotonic() -> None:
    _, kps = _synthetic_landmarks(0.35)
    scores = [
        eyes_open_score(_synthetic_landmarks(r)[0], kps)
        for r in (0.05, 0.15, 0.25, 0.35)
    ]
    assert scores == sorted(scores)
    assert all(0.0 <= s <= 1.0 for s in scores)


def test_pose_frontal_vs_turned() -> None:
    frontal = np.array(
        [[40.0, 50.0], [80.0, 50.0], [60.0, 72.0], [48.0, 100.0], [72.0, 100.0]]
    )
    yaw, pitch = estimate_pose_from_kps(frontal)
    assert abs(yaw) < 10
    assert abs(pitch) < 25

    # Nose displaced almost a full inter-eye distance to the side.
    turned = frontal.copy()
    turned[2, 0] = 60.0 + 35.0
    yaw_t, _ = estimate_pose_from_kps(turned)
    assert abs(yaw_t) > 45
    # Direction: nose moved toward +x
    assert yaw_t > 0


def test_pose_degenerate_kps_returns_zero() -> None:
    same = np.zeros((5, 2))
    assert estimate_pose_from_kps(same) == (0.0, 0.0)


# ── rollups ───────────────────────────────────────────────────────────────────

def test_rollup_any_eyes_closed_and_all_looking_away() -> None:
    open_face = FaceAttrs(eyes_open=0.9, yaw=5.0)
    closed_face = FaceAttrs(eyes_open=0.1, yaw=0.0)
    away = FaceAttrs(eyes_open=0.8, yaw=70.0)

    r = rollup_face_quality([open_face, closed_face])
    assert r["any_eyes_closed"] is True
    assert r["all_looking_away"] is False

    r = rollup_face_quality([away, FaceAttrs(eyes_open=0.9, yaw=-60.0)])
    assert r["any_eyes_closed"] is False
    assert r["all_looking_away"] is True

    # One frontal face rescues the rollup
    r = rollup_face_quality([away, open_face])
    assert r["all_looking_away"] is False


def test_rollup_handles_missing_attributes() -> None:
    r = rollup_face_quality([FaceAttrs(), FaceAttrs(eyes_open=None, yaw=None)])
    assert r == {"any_eyes_closed": False, "all_looking_away": False}
    assert rollup_face_quality([]) == {
        "any_eyes_closed": False,
        "all_looking_away": False,
    }


# ── contextual penalty rules ──────────────────────────────────────────────────

def test_penalty_single_face_closed_eyes_only() -> None:
    closed_solo = [FaceAttrs(eyes_open=0.1, yaw=0.0, area_ratio=0.1)]
    assert 0.0 < face_quality_penalty(closed_solo) <= PENALTY_CAP

    # Solo subject looking away with eyes open: NEVER penalized.
    averted_solo = [FaceAttrs(eyes_open=0.9, yaw=80.0, area_ratio=0.1)]
    assert face_quality_penalty(averted_solo) == 0.0

    open_solo = [FaceAttrs(eyes_open=0.9, yaw=0.0, area_ratio=0.1)]
    assert face_quality_penalty(open_solo) == 0.0


def test_penalty_group_frontal_stronger_than_single() -> None:
    group = [
        FaceAttrs(eyes_open=0.1, yaw=0.0, area_ratio=0.05),   # closed
        FaceAttrs(eyes_open=0.9, yaw=10.0, area_ratio=0.05),
        FaceAttrs(eyes_open=0.9, yaw=-20.0, area_ratio=0.05),
    ]
    solo = [FaceAttrs(eyes_open=0.1, yaw=0.0, area_ratio=0.05)]
    assert face_quality_penalty(group) > face_quality_penalty(solo)


def test_penalty_capped() -> None:
    many_closed = [
        FaceAttrs(eyes_open=0.05, yaw=0.0, area_ratio=0.05) for _ in range(6)
    ]
    assert face_quality_penalty(many_closed) == PENALTY_CAP


def test_penalty_ignores_tiny_background_faces() -> None:
    faces = [
        FaceAttrs(eyes_open=0.9, yaw=0.0, area_ratio=0.1),      # subject, fine
        FaceAttrs(eyes_open=0.05, yaw=0.0, area_ratio=0.0001),  # background blink
    ]
    assert face_quality_penalty(faces) == 0.0


def test_penalty_no_faces_or_unknown_is_zero() -> None:
    assert face_quality_penalty([]) == 0.0
    assert face_quality_penalty([FaceAttrs()]) == 0.0


# ── curation burst pick blend ─────────────────────────────────────────────────

def _seed_burst(tmp_path: Path, *, combined_a: float, combined_b: float):
    """Two photos in one moment: A has a closed-eyes face, B has eyes open.
    combined = 0.6*ap25 + 0.4*nima; we set nima == ap25 == combined for
    simplicity. The moment's primary points at a third photo that is NOT in
    scope, so curate's aesthetic pick decides the stack top."""
    cfg = get_folder_config(tmp_path)
    Session = init_db(cfg.db_path)
    with session_scope(Session) as s:
        pa = Photo(path=str(tmp_path / "a.jpg"), sha256="a" * 64, taken_at=datetime(2024, 1, 1))
        pb = Photo(path=str(tmp_path / "b.jpg"), sha256="b" * 64, taken_at=datetime(2024, 1, 1))
        pc = Photo(path=str(tmp_path / "c.jpg"), sha256="c" * 64, taken_at=datetime(2024, 1, 1))
        s.add_all([pa, pb, pc])
        s.flush()
        s.add_all([
            AestheticScore(photo_id=pa.id, ap25_score=combined_a, nima_score=combined_a),
            AestheticScore(photo_id=pb.id, ap25_score=combined_b, nima_score=combined_b),
        ])
        m = Moment(
            primary_photo_id=pc.id,
            started_at=datetime(2024, 1, 1),
            ended_at=datetime(2024, 1, 1),
            size=2,
        )
        s.add(m)
        s.flush()
        s.add_all([
            MomentMember(moment_id=m.id, photo_id=pa.id, rank=0),
            MomentMember(moment_id=m.id, photo_id=pb.id, rank=1),
        ])
        s.add(FaceEmbedding(
            photo_id=pa.id, face_index=0, embedding=b"\x00" * 1024,
            bbox_x=0, bbox_y=0, bbox_w=100, bbox_h=100, confidence=0.9,
            eyes_open=0.05, yaw=0.0, pitch=0.0, face_area_ratio=0.1,
        ))
        s.add(FaceEmbedding(
            photo_id=pb.id, face_index=0, embedding=b"\x00" * 1024,
            bbox_x=0, bbox_y=0, bbox_w=100, bbox_h=100, confidence=0.9,
            eyes_open=0.95, yaw=0.0, pitch=0.0, face_area_ratio=0.1,
        ))
        ids = (pa.id, pb.id)
    return Session, ids


def test_curate_prefers_eyes_open_among_near_equal(tmp_path: Path) -> None:
    from travelcull.ml.curation import curate

    # A slightly better aesthetically (7.0 vs 6.9) but eyes closed.
    Session, (a_id, b_id) = _seed_burst(tmp_path, combined_a=7.0, combined_b=6.9)
    with session_scope(Session) as s:
        out = curate(s, [a_id, b_id], pct_floor=0.0)
    assert len(out) == 1
    assert out[0].photo_id == b_id  # eyes-open frame wins the near-tie


def test_curate_does_not_override_big_aesthetic_gap(tmp_path: Path) -> None:
    from travelcull.ml.curation import curate

    # A is much better (8.0 vs 6.5); the bounded penalty must not flip it.
    Session, (a_id, b_id) = _seed_burst(tmp_path, combined_a=8.0, combined_b=6.5)
    with session_scope(Session) as s:
        out = curate(s, [a_id, b_id], pct_floor=0.0)
    assert len(out) == 1
    assert out[0].photo_id == a_id


# ── endpoint ──────────────────────────────────────────────────────────────────

@pytest.fixture
def fq_app(tmp_path):
    from travelcull.server.faces2_routes import register_faces2_routes

    cfg = get_folder_config(tmp_path)
    Session = init_db(cfg.db_path)
    with session_scope(Session) as s:
        p1 = Photo(path=str(tmp_path / "a.jpg"), sha256="a" * 64)
        p2 = Photo(path=str(tmp_path / "b.jpg"), sha256="b" * 64)
        s.add_all([p1, p2])
        s.flush()
        s.add(FaceEmbedding(
            photo_id=p1.id, face_index=0, embedding=b"\x00" * 1024,
            bbox_x=0, bbox_y=0, bbox_w=50, bbox_h=50, confidence=0.9,
            eyes_open=0.1, yaw=2.0, pitch=1.0, face_area_ratio=0.08,
        ))
        s.add(FaceEmbedding(
            photo_id=p1.id, face_index=1, embedding=b"\x00" * 1024,
            bbox_x=60, bbox_y=0, bbox_w=50, bbox_h=50, confidence=0.9,
            eyes_open=0.9, yaw=60.0, pitch=0.0, face_area_ratio=0.08,
        ))

    app = FastAPI()
    register_faces2_routes(app, cfg)
    return app


def test_face_quality_endpoint(fq_app) -> None:
    client = TestClient(fq_app)
    r = client.get(f"/api/photos/{'a' * 64}/face_quality")
    assert r.status_code == 200
    body = r.json()
    assert len(body["faces"]) == 2
    assert body["any_eyes_closed"] is True
    assert body["all_looking_away"] is False
    assert body["faces"][0]["eyes_open"] == pytest.approx(0.1)
    assert body["faces"][1]["yaw"] == pytest.approx(60.0)


def test_face_quality_endpoint_no_faces(fq_app) -> None:
    client = TestClient(fq_app)
    r = client.get(f"/api/photos/{'b' * 64}/face_quality")
    assert r.status_code == 200
    assert r.json() == {
        "faces": [],
        "any_eyes_closed": False,
        "all_looking_away": False,
    }


def test_face_quality_endpoint_unknown_photo_404(fq_app) -> None:
    client = TestClient(fq_app)
    assert client.get(f"/api/photos/{'f' * 64}/face_quality").status_code == 404


# ── migration on a legacy DB ──────────────────────────────────────────────────

def _forget_engine(db_path: Path) -> None:
    key = str(Path(db_path).resolve())
    with _ENGINES_LOCK:
        cached = _ENGINES.pop(key, None)
    if cached is not None:
        cached[0].dispose()


def _face_embeddings_columns(db_path: Path) -> set[str]:
    conn = sqlite3.connect(str(db_path))
    try:
        return {r[1] for r in conn.execute("PRAGMA table_info(face_embeddings)")}
    finally:
        conn.close()


def test_legacy_db_gains_face_attribute_columns(tmp_path: Path) -> None:
    """A pre-Alembic DB whose face_embeddings table lacks the attribute
    columns is upgraded in place; existing rows survive with NULL attrs."""
    db_path = tmp_path / ".travelcull" / "index.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "CREATE TABLE photos (id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "path VARCHAR(4096) NOT NULL)"
        )
        conn.execute(
            "CREATE TABLE face_embeddings ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "photo_id INTEGER NOT NULL, face_index INTEGER NOT NULL, "
            "embedding BLOB NOT NULL, "
            "bbox_x INTEGER NOT NULL, bbox_y INTEGER NOT NULL, "
            "bbox_w INTEGER NOT NULL, bbox_h INTEGER NOT NULL, "
            "confidence FLOAT NOT NULL, "
            "FOREIGN KEY(photo_id) REFERENCES photos (id) ON DELETE CASCADE)"
        )
        conn.execute("INSERT INTO photos (id, path) VALUES (1, '/a.jpg')")
        conn.execute(
            "INSERT INTO face_embeddings (photo_id, face_index, embedding, "
            "bbox_x, bbox_y, bbox_w, bbox_h, confidence) "
            "VALUES (1, 0, X'00', 1, 2, 30, 40, 0.9)"
        )
        conn.commit()
    finally:
        conn.close()

    assert "eyes_open" not in _face_embeddings_columns(db_path)

    _forget_engine(db_path)
    init_db(db_path)

    cols = _face_embeddings_columns(db_path)
    assert {"eyes_open", "yaw", "pitch", "face_area_ratio"}.issubset(cols)

    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute(
            "SELECT photo_id, face_index, bbox_w, confidence, eyes_open, yaw, "
            "pitch, face_area_ratio FROM face_embeddings"
        ).fetchall()
    finally:
        conn.close()
    assert rows == [(1, 0, 30, 0.9, None, None, None, None)]


def test_fresh_db_stamped_at_new_head(tmp_path: Path) -> None:
    """Fresh init_db creates the attribute columns via create_all and stamps
    the new head revision (which includes this feature's migration)."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    from travelcull.db import _MIGRATIONS_DIR

    db_path = tmp_path / ".travelcull" / "index.db"
    init_db(db_path)

    cols = _face_embeddings_columns(db_path)
    assert {"eyes_open", "yaw", "pitch", "face_area_ratio"}.issubset(cols)

    cfg = Config()
    cfg.set_main_option("script_location", str(_MIGRATIONS_DIR))
    script = ScriptDirectory.from_config(cfg)
    head = script.get_current_head()
    # Head advances as later features chain migrations; what matters here is
    # that this feature's revision is an ancestor of the current head.
    assert "c3d4e5f6a7b8" in {rev.revision for rev in script.walk_revisions()}
    assert head == "d4e5f6a7b8c9"

    conn = sqlite3.connect(str(db_path))
    try:
        stamped = conn.execute("SELECT version_num FROM alembic_version").fetchone()[0]
    finally:
        conn.close()
    assert stamped == head
