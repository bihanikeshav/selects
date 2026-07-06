"""Tests for selects.ml.embed — unit tests using CPU/synthetic data."""
from __future__ import annotations

import struct
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
import torch
from PIL import Image

from selects.db import init_db, session_scope
from selects.db.models import Embedding, Photo, PipelineState
from selects.ml.embed import run_embedding_stage


DIM = 1152  # SigLIP-SO400M embedding dimension


def _make_fake_feats(n: int) -> torch.Tensor:
    """Return L2-normalized random [n, 1152] float32 tensor."""
    x = torch.randn(n, DIM)
    return torch.nn.functional.normalize(x, dim=-1)


def _make_fake_iqa(n: int) -> np.ndarray:
    return np.random.rand(n).astype(np.float32)


def _make_red_image(size: int = 64) -> Image.Image:
    return Image.new("RGB", (size, size), color=(200, 50, 50))


@pytest.fixture()
def session_factory(tmp_path: Path):
    db_path = tmp_path / "test.db"
    return init_db(db_path)


@pytest.fixture()
def populated_db(session_factory, tmp_path: Path):
    """Insert 3 photos with preview files and PipelineState(embedding_done=False)."""
    previews_dir = tmp_path / ".selects" / "previews"
    previews_dir.mkdir(parents=True, exist_ok=True)

    photo_ids = []
    with session_scope(session_factory) as s:
        for i in range(3):
            fname = f"preview_{i:03d}.jpg"
            img = _make_red_image()
            img.save(previews_dir / fname)

            p = Photo(
                path=f"/photos/img{i:03d}.jpg",
                sha256=f"{'a' * 60}{i:04d}",
                preview_path=f"previews/{fname}",
            )
            s.add(p)
            s.flush()
            ps = PipelineState(photo_id=p.id, embedding_done=False)
            s.add(ps)
            photo_ids.append(p.id)

    return session_factory, tmp_path, photo_ids


def _patch_encode(monkeypatch, n_photos: int):
    """Patch encode_image_batch to return synthetic embeddings without loading the model."""
    def fake_encode(images):
        n = len(images)
        feats = _make_fake_feats(n)
        iqa = _make_fake_iqa(n)
        return feats, iqa

    import selects.ml.embed as embed_mod
    monkeypatch.setattr(embed_mod, "encode_image_batch", fake_encode)


class TestRunEmbeddingStage:
    def test_returns_count_of_processed_photos(self, populated_db, tmp_path, monkeypatch):
        session_factory, folder, photo_ids = populated_db
        _patch_encode(monkeypatch, len(photo_ids))

        from selects.config import get_folder_config
        cfg = get_folder_config(folder)

        # Also patch init_db inside embed to return our session factory
        import selects.ml.embed as embed_mod
        monkeypatch.setattr(embed_mod, "init_db", lambda _path: session_factory)

        n = run_embedding_stage(cfg)
        assert n == 3

    def test_embeddings_have_correct_dimension(self, populated_db, tmp_path, monkeypatch):
        session_factory, folder, photo_ids = populated_db
        _patch_encode(monkeypatch, len(photo_ids))

        from selects.config import get_folder_config
        cfg = get_folder_config(folder)

        import selects.ml.embed as embed_mod
        monkeypatch.setattr(embed_mod, "init_db", lambda _path: session_factory)

        run_embedding_stage(cfg)

        with session_scope(session_factory) as s:
            embs = s.query(Embedding).all()

        assert len(embs) == 3
        for emb in embs:
            arr = np.frombuffer(emb.siglip, dtype=np.float16)
            assert arr.shape == (DIM,), f"expected ({DIM},) got {arr.shape}"

    def test_embedding_done_flag_set(self, populated_db, tmp_path, monkeypatch):
        session_factory, folder, photo_ids = populated_db
        _patch_encode(monkeypatch, len(photo_ids))

        from selects.config import get_folder_config
        cfg = get_folder_config(folder)

        import selects.ml.embed as embed_mod
        monkeypatch.setattr(embed_mod, "init_db", lambda _path: session_factory)

        run_embedding_stage(cfg)

        with session_scope(session_factory) as s:
            states = s.query(PipelineState).all()

        assert all(ps.embedding_done for ps in states)

    def test_iqa_score_in_valid_range(self, populated_db, tmp_path, monkeypatch):
        session_factory, folder, photo_ids = populated_db
        _patch_encode(monkeypatch, len(photo_ids))

        from selects.config import get_folder_config
        cfg = get_folder_config(folder)

        import selects.ml.embed as embed_mod
        monkeypatch.setattr(embed_mod, "init_db", lambda _path: session_factory)

        run_embedding_stage(cfg)

        with session_scope(session_factory) as s:
            embs = s.query(Embedding).all()

        for emb in embs:
            assert 0.0 <= emb.aesthetic_iqa <= 1.0

    def test_idempotent_rerun_returns_zero(self, populated_db, tmp_path, monkeypatch):
        """Second run should find nothing pending and return 0."""
        session_factory, folder, photo_ids = populated_db
        _patch_encode(monkeypatch, len(photo_ids))

        from selects.config import get_folder_config
        cfg = get_folder_config(folder)

        import selects.ml.embed as embed_mod
        monkeypatch.setattr(embed_mod, "init_db", lambda _path: session_factory)

        run_embedding_stage(cfg)
        n2 = run_embedding_stage(cfg)
        assert n2 == 0
