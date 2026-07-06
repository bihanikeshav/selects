"""Tests for taste personalization (travelcull/ml/taste.py + curation blend)."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from travelcull.config import get_folder_config
from travelcull.db import init_db, session_scope
from travelcull.db.models import AestheticScore, Embedding, Photo, Swipe
from travelcull.ml import taste
from travelcull.ml.curation import curate


DIM = 64
RNG = np.random.default_rng(42)


def _fp16_blob(vec: np.ndarray) -> bytes:
    return vec.astype(np.float16).tobytes()


def _synthetic_embedding(positive: bool) -> np.ndarray:
    """Linearly separable clusters: positives shifted +1 on the first 8 dims."""
    v = RNG.normal(0.0, 1.0, DIM)
    v[:8] += 1.5 if positive else -1.5
    return v


def _make_library(tmp_path: Path, n_keep: int, n_reject: int, with_scores: bool = False):
    cfg = get_folder_config(tmp_path)
    Session = init_db(cfg.db_path)
    with session_scope(Session) as s:
        pid = 0
        for positive, count in ((True, n_keep), (False, n_reject)):
            for _ in range(count):
                pid += 1
                sha = f"sha{pid:06d}"
                s.add(Photo(id=pid, path=f"p{pid}.jpg", sha256=sha))
                s.flush()  # photos row must exist before FK dependents
                s.add(Embedding(photo_id=pid, siglip=_fp16_blob(_synthetic_embedding(positive))))
                s.add(Swipe(photo_id=pid, decision="keep" if positive else "reject"))
                if with_scores:
                    # All photos get the SAME aesthetic so any ordering change
                    # in curate() must come from the taste blend.
                    s.add(AestheticScore(photo_id=pid, ap25_score=0.5, nima_score=5.0))
    return cfg, Session


# --------------------------------------------------------------------------- #
# Ramp / blend math                                                            #
# --------------------------------------------------------------------------- #

def test_taste_weight_ramp():
    assert taste.taste_weight(0) == 0.0
    assert taste.taste_weight(100) == 0.0
    assert taste.taste_weight(550) == pytest.approx(0.2)
    assert taste.taste_weight(1000) == pytest.approx(0.4)
    assert taste.taste_weight(50_000) == pytest.approx(0.4)  # capped, never dominates


def test_taste_weight_monotonic():
    ws = [taste.taste_weight(n) for n in range(0, 2000, 50)]
    assert all(b >= a for a, b in zip(ws, ws[1:]))
    assert max(ws) <= 0.4


def test_blend_bounds():
    for n in (0, 100, 500, 1000, 10_000):
        for a in (0.0, 0.3, 1.0):
            for t in (0.0, 0.7, 1.0):
                v = taste.blend(a, t, n)
                assert 0.0 <= v <= 1.0
    # w=0 → pure aesthetic; w capped → taste share never exceeds 0.4
    assert taste.blend(0.8, 0.0, 100) == pytest.approx(0.8)
    assert taste.blend(0.0, 1.0, 10_000) == pytest.approx(0.4)


# --------------------------------------------------------------------------- #
# Training                                                                     #
# --------------------------------------------------------------------------- #

def test_train_requires_min_samples(tmp_path):
    cfg, _ = _make_library(tmp_path, n_keep=30, n_reject=30)
    with pytest.raises(taste.TasteTrainingError):
        taste.train_taste_model(cfg)


def test_train_requires_both_classes(tmp_path):
    cfg, _ = _make_library(tmp_path, n_keep=120, n_reject=5)
    with pytest.raises(taste.TasteTrainingError):
        taste.train_taste_model(cfg)


def test_train_separable_high_auc(tmp_path):
    cfg, _ = _make_library(tmp_path, n_keep=80, n_reject=80)
    result = taste.train_taste_model(cfg)

    assert result["n_samples"] == 160
    assert result["auc"] is not None and result["auc"] > 0.9
    assert 0.0 <= result["weight"] <= 0.4

    npz_path = cfg.state_dir / taste.TASTE_FILENAME
    assert npz_path.is_file()

    model = taste.load_model(cfg.state_dir)
    assert model is not None
    assert model.n_samples == 160
    assert model.auc == pytest.approx(result["auc"])
    assert model.trained_at  # ISO timestamp persisted


def test_taste_score_batch_separates_classes(tmp_path):
    cfg, _ = _make_library(tmp_path, n_keep=80, n_reject=80)
    taste.train_taste_model(cfg)

    keep_shas = [f"sha{i:06d}" for i in range(1, 81)]
    reject_shas = [f"sha{i:06d}" for i in range(81, 161)]
    scores = taste.taste_score(cfg, keep_shas + reject_shas + ["missing_sha"])

    assert "missing_sha" not in scores
    assert all(0.0 <= v <= 1.0 for v in scores.values())
    mean_keep = np.mean([scores[sh] for sh in keep_shas])
    mean_reject = np.mean([scores[sh] for sh in reject_shas])
    assert mean_keep > mean_reject + 0.3


def test_taste_score_without_model_is_empty(tmp_path):
    cfg, _ = _make_library(tmp_path, n_keep=10, n_reject=10)
    assert taste.taste_score(cfg, ["sha000001"]) == {}


def test_status_untrained_and_trained(tmp_path):
    cfg, _ = _make_library(tmp_path, n_keep=80, n_reject=80)

    st = taste.taste_status(cfg)
    assert st["trained"] is False
    assert st["weight"] == 0.0
    assert st["labeled_available"] == 160

    taste.train_taste_model(cfg)
    st = taste.taste_status(cfg)
    assert st["trained"] is True
    assert st["n_samples"] == 160
    assert st["auc"] > 0.9
    assert st["weight"] == taste.taste_weight(160)


# --------------------------------------------------------------------------- #
# Curation blend                                                               #
# --------------------------------------------------------------------------- #

def test_curate_unchanged_without_taste_model(tmp_path):
    cfg, Session = _make_library(tmp_path, n_keep=20, n_reject=20, with_scores=True)
    with session_scope(Session) as s:
        out = curate(s, list(range(1, 41)), pct_floor=0.0)
    assert out
    assert all(c.taste is None and c.final is None for c in out)


def test_curate_blends_taste_into_ranking(tmp_path):
    # 300 labeled examples → weight ~0.089 > 0; aesthetics identical, so
    # taste must decide the order: keeps rank above rejects.
    cfg, Session = _make_library(tmp_path, n_keep=150, n_reject=150, with_scores=True)
    taste.train_taste_model(cfg)

    with session_scope(Session) as s:
        out = curate(s, list(range(1, 301)), pct_floor=0.0)

    assert out
    assert all(c.final is not None and 0.0 <= c.final <= 1.0 for c in out)
    assert all(c.taste is not None and 0.0 <= c.taste <= 1.0 for c in out)

    # sorted by final desc
    finals = [c.final for c in out]
    assert finals == sorted(finals, reverse=True)

    keep_shas = {f"sha{i:06d}" for i in range(1, 151)}
    top_half = out[: len(out) // 2]
    keep_frac_top = sum(1 for c in top_half if c.sha256 in keep_shas) / len(top_half)
    assert keep_frac_top > 0.8


def test_curate_taste_never_dominates(tmp_path):
    # With a huge aesthetic gap, taste (capped at 0.4) cannot flip the leader.
    cfg, Session = _make_library(tmp_path, n_keep=150, n_reject=150, with_scores=True)
    # Give one *rejected* photo a massively better aesthetic score.
    with session_scope(Session) as s:
        sc = s.get(AestheticScore, 151)  # first reject
        sc.ap25_score = 10.0
        sc.nima_score = 10.0
    taste.train_taste_model(cfg)

    with session_scope(Session) as s:
        out = curate(s, list(range(1, 301)), pct_floor=0.0)

    # Its aesthetic percentile-rank is 1.0; with w<=0.4 the blended score is
    # >= 0.6, beating any pure-taste-1.0/aesthetic-0 photo only when w<0.5 —
    # so a big aesthetic winner stays near the very top.
    top_ids = [c.photo_id for c in out[:5]]
    assert 151 in top_ids
