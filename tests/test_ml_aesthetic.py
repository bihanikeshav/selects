"""HyperIQA scoring (no network — injected session)."""
from __future__ import annotations

import numpy as np
import pytest
from PIL import Image

from selects.ml.aesthetic import ensemble_score, score_images


class _FakeSess:
    def get_inputs(self):
        return [type("I", (), {"name": "input"})()]

    def run(self, _names, feed):
        n = feed["input"].shape[0]
        # 0-1 raw quality
        return [np.linspace(0.2, 0.8, n, dtype=np.float32)]


@pytest.fixture(autouse=True)
def _fake_hyperiqa(monkeypatch):
    monkeypatch.setattr(
        "selects.ml.onnx_rt.model_session",
        lambda name, prefer=None, cache=True: _FakeSess(),
    )


def test_score_images_shape_and_range():
    imgs = [Image.new("RGB", (32, 32), color=(i * 40, 10, 10)) for i in range(4)]
    s = score_images(imgs)
    assert s.shape == (4,)
    assert np.isfinite(s).all()
    assert (s >= 0).all() and (s <= 10).all()


def test_run_aesthetic_stage_writes_ap25(tmp_path, monkeypatch):
    from selects.config import get_folder_config
    from selects.db import init_db, session_scope
    from selects.db.models import AestheticScore, Photo
    from selects.ml.aesthetic import run_aesthetic_stage

    cfg = get_folder_config(tmp_path)
    Session = init_db(cfg.db_path)
    previews = cfg.state_dir / "previews"
    previews.mkdir(parents=True, exist_ok=True)
    img_path = previews / "a.jpg"
    Image.new("RGB", (64, 64), color=(20, 80, 20)).save(img_path)
    with session_scope(Session) as s:
        p = Photo(
            path=str(tmp_path / "a.jpg"),
            sha256="a" * 64,
            preview_path="previews/a.jpg",
        )
        s.add(p)
        s.flush()
        pid = p.id
    n = run_aesthetic_stage(cfg)
    assert n == 1
    with session_scope(Session) as s:
        row = s.get(AestheticScore, pid)
        assert row is not None
        assert row.ap25_score is not None
        assert np.isfinite(row.ap25_score)
        assert 0.0 <= row.ap25_score <= 10.0


def test_ensemble_prefers_ap_nima_then_ap_then_iqa():
    assert ensemble_score(6.0, 5.0, 0.2) == pytest.approx(0.6 * 6.0 + 0.4 * 5.0)
    assert ensemble_score(6.0, None, 0.2) == pytest.approx(6.0)
    assert ensemble_score(None, None, 0.2) == pytest.approx(2.0)
    assert ensemble_score(None, None, None) is None
