"""Tests for selects.ml.tags — unit tests using CPU/synthetic data."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import torch

from selects.db import init_db, session_scope
from selects.db.models import Embedding, Photo, PhotoTag, PipelineState
from selects.ml.tags import run_tag_stage, DEFAULT_TAG_PROMPTS


DIM = 1152


def _norm_rand(n: int, d: int = DIM) -> np.ndarray:
    x = np.random.randn(n, d).astype(np.float32)
    norms = np.linalg.norm(x, axis=1, keepdims=True)
    return (x / norms).astype(np.float16)


@pytest.fixture()
def session_factory(tmp_path: Path):
    db_path = tmp_path / "test.db"
    return init_db(db_path)


@pytest.fixture()
def embedded_db(session_factory, tmp_path: Path):
    """Insert 1 photo with a synthetic embedding, embedding_done=True."""
    with session_scope(session_factory) as s:
        p = Photo(path="/photos/img000.jpg", sha256="a" * 64, preview_path="previews/img000.jpg")
        s.add(p)
        s.flush()
        ps = PipelineState(photo_id=p.id, embedding_done=True)
        s.add(ps)
        blob = _norm_rand(1).tobytes()
        emb = Embedding(photo_id=p.id, siglip=blob, aesthetic_iqa=0.7)
        s.add(emb)
        photo_id = p.id

    return session_factory, tmp_path, photo_id


def _fake_encode_text(prompts):
    """Return unit-normalized random [N, 1152] float32 on CPU (no GPU needed)."""
    n = len(prompts)
    x = torch.randn(n, DIM)
    return torch.nn.functional.normalize(x.float(), dim=-1)


class TestRunTagStage:
    def test_populates_photo_tags_for_single_photo(self, embedded_db, monkeypatch):
        session_factory, folder, photo_id = embedded_db

        from selects.config import get_folder_config
        cfg = get_folder_config(folder)

        import selects.ml.tags as tags_mod
        monkeypatch.setattr(tags_mod, "encode_text_prompts", _fake_encode_text)
        monkeypatch.setattr(tags_mod, "init_db", lambda _path: session_factory)

        # Use min_z=0.0 so single-photo datasets (where z-score is always 0) still get tags.
        n = run_tag_stage(cfg, min_score=0.0, min_z=0.0)
        assert n == 1

        with session_scope(session_factory) as s:
            tags = s.query(PhotoTag).filter(PhotoTag.photo_id == photo_id).all()

        # Should have at least 1 tag (top_k=3 by default, min_z=0.0)
        assert len(tags) >= 1
        for pt in tags:
            assert pt.tag in DEFAULT_TAG_PROMPTS
            assert isinstance(pt.score, float)

    def test_marks_vl_done(self, embedded_db, monkeypatch):
        session_factory, folder, photo_id = embedded_db

        from selects.config import get_folder_config
        cfg = get_folder_config(folder)

        import selects.ml.tags as tags_mod
        monkeypatch.setattr(tags_mod, "encode_text_prompts", _fake_encode_text)
        monkeypatch.setattr(tags_mod, "init_db", lambda _path: session_factory)

        run_tag_stage(cfg, min_score=0.0, min_z=0.0)

        with session_scope(session_factory) as s:
            ps = s.get(PipelineState, photo_id)

        assert ps.vl_done is True

    def test_idempotent_rerun_replaces_tags(self, embedded_db, monkeypatch):
        """Running tag stage twice should not duplicate rows."""
        session_factory, folder, photo_id = embedded_db

        from selects.config import get_folder_config
        cfg = get_folder_config(folder)

        import selects.ml.tags as tags_mod
        monkeypatch.setattr(tags_mod, "encode_text_prompts", _fake_encode_text)
        monkeypatch.setattr(tags_mod, "init_db", lambda _path: session_factory)

        run_tag_stage(cfg, min_score=0.0, min_z=0.0)
        with session_scope(session_factory) as s:
            count_after_first = s.query(PhotoTag).filter(PhotoTag.photo_id == photo_id).count()

        run_tag_stage(cfg, min_score=0.0, min_z=0.0)
        with session_scope(session_factory) as s:
            count_after_second = s.query(PhotoTag).filter(PhotoTag.photo_id == photo_id).count()

        assert count_after_first == count_after_second

    def test_returns_zero_when_no_embeddings(self, session_factory, tmp_path, monkeypatch):
        """Tag stage should skip photos without embeddings."""
        from selects.config import get_folder_config
        cfg = get_folder_config(tmp_path)

        import selects.ml.tags as tags_mod
        monkeypatch.setattr(tags_mod, "encode_text_prompts", _fake_encode_text)
        monkeypatch.setattr(tags_mod, "init_db", lambda _path: session_factory)

        n = run_tag_stage(cfg, min_score=0.0, min_z=0.0)
        assert n == 0
