"""Tests for selects.ml.moments: _matches() and run_moment_stage()."""
from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pytest

from selects.ml.moments import _matches, run_moment_stage, TIME_GAP_S, VISUAL_SIM_THRESH


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _unit_emb(seed: int, dim: int = 1152) -> np.ndarray:
    """Return a deterministic unit-norm vector of length dim."""
    rng = np.random.default_rng(seed)
    v = rng.standard_normal(dim).astype(np.float32)
    return v / np.linalg.norm(v)


def _face_emb(seed: int, dim: int = 512) -> np.ndarray:
    rng = np.random.default_rng(seed)
    v = rng.standard_normal(dim).astype(np.float32)
    return v / np.linalg.norm(v)


def _photo(
    *,
    taken_at: datetime,
    emb: np.ndarray | None = None,
    lat: float | None = None,
    lon: float | None = None,
    faces: list[np.ndarray] | None = None,
    blur: float = 500.0,
    iqa: float = 0.5,
    seed: int = 0,
) -> dict:
    if emb is None:
        emb = _unit_emb(seed)
    return {
        "id": seed,
        "taken_at": taken_at,
        "lat": lat,
        "lon": lon,
        "emb": emb,
        "faces": faces or [],
        "blur": blur,
        "iqa": iqa,
    }


BASE_TIME = datetime(2024, 8, 15, 14, 0, 0)

# A shared SigLIP embedding (identical scene)
SHARED_EMB = _unit_emb(42)


# ---------------------------------------------------------------------------
# _matches unit tests
# ---------------------------------------------------------------------------

class TestMatches:
    def _pair(self, **overrides_b):
        a = _photo(taken_at=BASE_TIME, emb=SHARED_EMB, seed=1)
        b_kwargs = dict(taken_at=BASE_TIME, emb=SHARED_EMB, seed=2)
        b_kwargs.update(overrides_b)
        b = _photo(**b_kwargs)
        return a, b

    def test_identical_scene_no_faces_matches(self):
        a, b = self._pair()
        assert _matches(a, b)

    def test_time_gap_too_large_rejects(self):
        a = _photo(taken_at=BASE_TIME, emb=SHARED_EMB, seed=1)
        b = _photo(taken_at=BASE_TIME + timedelta(seconds=TIME_GAP_S + 1), emb=SHARED_EMB, seed=2)
        assert not _matches(a, b)

    def test_exact_time_gap_boundary_passes(self):
        a = _photo(taken_at=BASE_TIME, emb=SHARED_EMB, seed=1)
        b = _photo(taken_at=BASE_TIME + timedelta(seconds=TIME_GAP_S), emb=SHARED_EMB, seed=2)
        assert _matches(a, b)

    def test_different_visual_rejects(self):
        emb_a = _unit_emb(1)
        emb_b = _unit_emb(2)  # random — almost certainly < 0.90 cosine
        a = _photo(taken_at=BASE_TIME, emb=emb_a, seed=1)
        b = _photo(taken_at=BASE_TIME, emb=emb_b, seed=2)
        # Should only fail if cosine < 0.90
        cos = float(np.dot(emb_a, emb_b))
        if cos < VISUAL_SIM_THRESH:
            assert not _matches(a, b)

    def test_gps_too_far_rejects(self):
        a = _photo(taken_at=BASE_TIME, emb=SHARED_EMB, lat=34.0, lon=77.0, seed=1)
        b = _photo(taken_at=BASE_TIME, emb=SHARED_EMB, lat=34.01, lon=77.01, seed=2)
        # 0.01 deg ≈ 1.1 km — well beyond 0.0002 threshold
        assert not _matches(a, b)

    def test_gps_close_enough_passes(self):
        a = _photo(taken_at=BASE_TIME, emb=SHARED_EMB, lat=34.0, lon=77.0, seed=1)
        b = _photo(taken_at=BASE_TIME, emb=SHARED_EMB, lat=34.00015, lon=77.00015, seed=2)
        # sqrt(0.00015^2 + 0.00015^2) ≈ 0.000212 — just above 0.0002; should fail
        # Actually 0.000212 > 0.0002 so this is rejected.
        # Let's use 0.0001 each — distance ~0.000141 which is < 0.0002 → passes
        a2 = _photo(taken_at=BASE_TIME, emb=SHARED_EMB, lat=34.0, lon=77.0, seed=1)
        b2 = _photo(taken_at=BASE_TIME, emb=SHARED_EMB, lat=34.0001, lon=77.0001, seed=2)
        assert _matches(a2, b2)

    def test_missing_gps_on_one_side_ignores_gps(self):
        a = _photo(taken_at=BASE_TIME, emb=SHARED_EMB, lat=34.0, lon=77.0, seed=1)
        b = _photo(taken_at=BASE_TIME, emb=SHARED_EMB, lat=None, lon=None, seed=2)
        # No GPS on b → fall through to visual check only
        assert _matches(a, b)

    def test_one_has_faces_other_doesnt_rejects(self):
        face = _face_emb(7)
        a = _photo(taken_at=BASE_TIME, emb=SHARED_EMB, faces=[face], seed=1)
        b = _photo(taken_at=BASE_TIME, emb=SHARED_EMB, faces=[], seed=2)
        assert not _matches(a, b)

    def test_same_face_matches(self):
        face = _face_emb(7)
        a = _photo(taken_at=BASE_TIME, emb=SHARED_EMB, faces=[face], seed=1)
        b = _photo(taken_at=BASE_TIME, emb=SHARED_EMB, faces=[face], seed=2)
        assert _matches(a, b)

    def test_different_faces_rejects(self):
        face_a = _face_emb(1)
        face_b = _face_emb(2)
        # Make them orthogonal
        # Create perpendicular vectors
        dim = 512
        v1 = np.zeros(dim, dtype=np.float32)
        v1[0] = 1.0
        v2 = np.zeros(dim, dtype=np.float32)
        v2[1] = 1.0  # orthogonal to v1, cosine = 0
        a = _photo(taken_at=BASE_TIME, emb=SHARED_EMB, faces=[v1], seed=1)
        b = _photo(taken_at=BASE_TIME, emb=SHARED_EMB, faces=[v2], seed=2)
        assert not _matches(a, b)  # cosine = 0.0 < 0.55

    def test_multiple_faces_at_least_one_shared_matches(self):
        shared_face = _face_emb(10)
        v1 = np.zeros(512, dtype=np.float32); v1[0] = 1.0
        v2 = np.zeros(512, dtype=np.float32); v2[1] = 1.0
        a = _photo(taken_at=BASE_TIME, emb=SHARED_EMB, faces=[v1, shared_face], seed=1)
        b = _photo(taken_at=BASE_TIME, emb=SHARED_EMB, faces=[v2, shared_face], seed=2)
        assert _matches(a, b)  # v1 vs v2 fails, but shared_face vs shared_face passes


# ---------------------------------------------------------------------------
# run_moment_stage integration test (in-memory DB)
# ---------------------------------------------------------------------------

def _make_cfg(tmp_path):
    from selects.config import get_folder_config
    cfg = get_folder_config(tmp_path)
    return cfg


def test_run_moment_stage_no_photos(tmp_path):
    """Stage returns 0 when there are no embeddings."""
    from selects.db import init_db
    cfg = _make_cfg(tmp_path)
    init_db(cfg.db_path)
    n = run_moment_stage(cfg)
    assert n == 0


def test_run_moment_stage_creates_moments(tmp_path):
    """Two very similar photos taken <TIME_GAP_S apart become one moment."""
    from selects.db import init_db, session_scope
    from selects.db.models import Embedding, Moment, MomentMember, Photo, PipelineState

    cfg = _make_cfg(tmp_path)
    Session = init_db(cfg.db_path)

    emb_blob = SHARED_EMB.astype(np.float16).tobytes()
    t0 = BASE_TIME
    t1 = BASE_TIME + timedelta(seconds=TIME_GAP_S - 7)

    with session_scope(Session) as s:
        for i, t in enumerate([t0, t1]):
            p = Photo(
                path=f"/fake/photo_{i}.jpg",
                sha256=f"abc{i:03d}",
                taken_at=t,
            )
            s.add(p)
            s.flush()
            ps = PipelineState(photo_id=p.id, classical_done=True, embedding_done=True)
            s.add(ps)
            emb = Embedding(photo_id=p.id, siglip=emb_blob, aesthetic_iqa=0.7)
            s.add(emb)

    n = run_moment_stage(cfg)
    assert n == 1

    with session_scope(Session) as s:
        assert s.query(Moment).count() == 1
        assert s.query(MomentMember).count() == 2
        m = s.query(Moment).first()
        assert m.size == 2


def test_run_moment_stage_dissimilar_photos_no_moments(tmp_path):
    """Photos with low visual similarity stay as singletons (no moments)."""
    from selects.db import init_db, session_scope
    from selects.db.models import Embedding, Moment, Photo, PipelineState

    cfg = _make_cfg(tmp_path)
    Session = init_db(cfg.db_path)

    emb_a = _unit_emb(100)
    emb_b = _unit_emb(200)
    # Ensure they are dissimilar
    # If cosine >= 0.90 by chance, force them orthogonal
    dim = len(emb_a)
    e_a = np.zeros(dim, dtype=np.float32); e_a[0] = 1.0
    e_b = np.zeros(dim, dtype=np.float32); e_b[1] = 1.0

    t0 = BASE_TIME
    t1 = BASE_TIME + timedelta(seconds=10)

    with session_scope(Session) as s:
        for i, (t, emb) in enumerate([(t0, e_a), (t1, e_b)]):
            p = Photo(path=f"/fake/dissim_{i}.jpg", sha256=f"dis{i:03d}", taken_at=t)
            s.add(p)
            s.flush()
            ps = PipelineState(photo_id=p.id, classical_done=True, embedding_done=True)
            s.add(ps)
            s.add(Embedding(photo_id=p.id, siglip=emb.astype(np.float16).tobytes(), aesthetic_iqa=0.5))

    n = run_moment_stage(cfg)
    assert n == 0
    with session_scope(Session) as s:
        assert s.query(Moment).count() == 0


def test_run_moment_stage_time_gap_prevents_linking(tmp_path):
    """Photos >60s apart must not form a moment even if visually identical."""
    from selects.db import init_db, session_scope
    from selects.db.models import Embedding, Moment, Photo, PipelineState

    cfg = _make_cfg(tmp_path)
    Session = init_db(cfg.db_path)

    emb_blob = SHARED_EMB.astype(np.float16).tobytes()
    t0 = BASE_TIME
    t1 = BASE_TIME + timedelta(seconds=TIME_GAP_S + 5)  # 65 s gap

    with session_scope(Session) as s:
        for i, t in enumerate([t0, t1]):
            p = Photo(path=f"/fake/gap_{i}.jpg", sha256=f"gap{i:03d}", taken_at=t)
            s.add(p)
            s.flush()
            ps = PipelineState(photo_id=p.id, classical_done=True, embedding_done=True)
            s.add(ps)
            s.add(Embedding(photo_id=p.id, siglip=emb_blob, aesthetic_iqa=0.6))

    n = run_moment_stage(cfg)
    assert n == 0
