"""AP-V2.5 head scoring (no network — injected numpy layers)."""
from __future__ import annotations

import numpy as np
import pytest

from selects.ml import aesthetic as aesthetic_mod
from selects.ml.aesthetic import ensemble_score, score_embeddings


@pytest.fixture(autouse=True)
def _tiny_head(monkeypatch):
    """Five tiny linear maps: 8-d fake space is too small; use real 1152 with zeros+one."""
    rng = np.random.default_rng(0)
    layers = []
    dims = [(1024, 1152), (128, 1024), (64, 128), (16, 64), (1, 16)]
    for out, inn in dims:
        w = rng.normal(0, 0.01, size=(out, inn)).astype(np.float32)
        b = np.zeros((out,), dtype=np.float32)
        layers.append((w, b))
    monkeypatch.setattr(aesthetic_mod, "_LAYERS", layers)
    yield
    monkeypatch.setattr(aesthetic_mod, "_LAYERS", None)


def test_score_embeddings_shape_and_finite():
    x = np.random.default_rng(1).normal(size=(4, 1152)).astype(np.float32)
    s = score_embeddings(x)
    assert s.shape == (4,)
    assert np.isfinite(s).all()


def test_score_embeddings_l2_invariant_direction():
    x = np.ones((1, 1152), dtype=np.float32)
    a = score_embeddings(x)
    b = score_embeddings(x * 3.0)
    assert a.shape == b.shape
    np.testing.assert_allclose(a, b, rtol=1e-5, atol=1e-5)


def test_run_aesthetic_stage_writes_ap25(tmp_path, monkeypatch):
    from selects.config import get_folder_config
    from selects.db import init_db, session_scope
    from selects.db.models import AestheticScore, Embedding, Photo
    from selects.ml.aesthetic import run_aesthetic_stage

    cfg = get_folder_config(tmp_path)
    Session = init_db(cfg.db_path)
    feat = np.ones(1152, dtype=np.float16).tobytes()
    with session_scope(Session) as s:
        p = Photo(path=str(tmp_path / "a.jpg"), sha256="a" * 64)
        s.add(p)
        s.flush()
        s.add(Embedding(photo_id=p.id, siglip=feat, aesthetic_iqa=0.2))
        pid = p.id
    n = run_aesthetic_stage(cfg)
    assert n == 1
    with session_scope(Session) as s:
        row = s.get(AestheticScore, pid)
        assert row is not None
        assert row.ap25_score is not None
        assert np.isfinite(row.ap25_score)


def test_ensemble_prefers_ap_nima_then_ap_then_iqa():
    assert ensemble_score(6.0, 5.0, 0.2) == pytest.approx(0.6 * 6.0 + 0.4 * 5.0)
    assert ensemble_score(6.0, None, 0.2) == pytest.approx(6.0)
    assert ensemble_score(None, None, 0.2) == pytest.approx(2.0)
    assert ensemble_score(None, None, None) is None
