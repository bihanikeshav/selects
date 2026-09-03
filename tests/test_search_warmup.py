"""Search-engine warmup, query cache, and static-asset caching."""
from __future__ import annotations

import numpy as np
import pytest
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient

from selects.config import get_folder_config
from selects.db import init_db, session_scope
from selects.db.models import Embedding, Photo
from selects.ml import search as search_mod
from selects.ml import warmup as warmup_mod
from selects.server.app import build_app


@pytest.fixture(autouse=True)
def _reset_warmup_and_caches():
    warmup_mod.reset_for_tests()
    search_mod._QUERY_CACHE.clear()
    search_mod._LIB_CACHE.clear()
    yield
    warmup_mod.reset_for_tests()
    search_mod._QUERY_CACHE.clear()
    search_mod._LIB_CACHE.clear()


def test_search_is_not_ready_before_warmup():
    assert warmup_mod.is_search_ready() is False


def test_warmup_search_loads_text_model_once(monkeypatch):
    calls: list[list[str]] = []

    def fake_encode(prompts):
        calls.append(list(prompts))
        return np.zeros((len(prompts), 1152), dtype=np.float32)

    monkeypatch.setattr("selects.ml.embed.encode_text_prompts", fake_encode)

    warmup_mod.warmup_search()
    warmup_mod.warmup_search()

    assert warmup_mod.is_search_ready() is True
    assert len(calls) == 1


def test_warmup_search_preloads_library_matrix(tmp_path, monkeypatch):
    cfg = get_folder_config(tmp_path)
    init_db(cfg.db_path)
    matrix_calls: list[object] = []

    monkeypatch.setattr(
        "selects.ml.embed.encode_text_prompts",
        lambda prompts: np.zeros((len(prompts), 1152), dtype=np.float32),
    )
    monkeypatch.setattr(
        "selects.ml.search.library_embedding_matrix",
        lambda c: matrix_calls.append(c) or (np.zeros((0, 1152), dtype=np.float32), [], []),
    )

    warmup_mod.warmup_search(cfg)
    assert matrix_calls == [cfg]

    warmup_mod.warmup_search(cfg, embeddings=False)
    assert len(matrix_calls) == 1


def test_warmup_search_can_skip_embeddings(monkeypatch):
    called = []
    monkeypatch.setattr(
        "selects.ml.embed.encode_text_prompts",
        lambda prompts: np.zeros((len(prompts), 1152), dtype=np.float32),
    )
    monkeypatch.setattr(
        "selects.ml.search.library_embedding_matrix",
        lambda cfg: called.append(cfg),
    )
    warmup_mod.warmup_search(embeddings=False)
    assert called == []
    assert warmup_mod.is_search_ready() is True


def test_embed_query_caches_identical_text(monkeypatch):
    calls: list[str] = []

    def fake_encode(prompts):
        calls.append(prompts[0])
        vec = np.zeros((1, 1152), dtype=np.float32)
        vec[0, 0] = 1.0
        return vec

    monkeypatch.setattr("selects.ml.embed.encode_text_prompts", fake_encode)
    a = search_mod.embed_query("Monastery")
    b = search_mod.embed_query(" monastery ")
    assert a is b
    assert calls == ["Monastery"]


def test_library_matrix_reuses_cached_array(tmp_path):
    cfg = get_folder_config(tmp_path)
    Session = init_db(cfg.db_path)
    vec = np.zeros(1152, dtype=np.float32)
    vec[0] = 1.0
    with session_scope(Session) as s:
        photo = Photo(path=str(tmp_path / "a.jpg"), sha256="a" * 64)
        s.add(photo)
        s.flush()
        s.add(Embedding(photo_id=photo.id, siglip=vec.astype(np.float16).tobytes()))

    mat1, ids1, shas1 = search_mod.library_embedding_matrix(cfg)
    mat2, ids2, shas2 = search_mod.library_embedding_matrix(cfg)
    assert mat1 is mat2
    assert ids1 == ids2
    assert shas1 == shas2
    assert mat1.shape == (1, 1152)


async def test_search_ready_endpoint_starts_false(tmp_path):
    cfg = get_folder_config(tmp_path)
    init_db(cfg.db_path)
    app = build_app(cfg, run_background=False)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/api/search/ready")
        assert r.status_code == 200
        body = r.json()
        assert body["ready"] is False


async def test_search_warmup_endpoint_marks_ready(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "selects.ml.embed.encode_text_prompts",
        lambda prompts: np.zeros((len(prompts), 1152), dtype=np.float32),
    )
    cfg = get_folder_config(tmp_path)
    init_db(cfg.db_path)
    app = build_app(cfg, run_background=False)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post("/api/search/warmup")
        assert r.status_code == 200
        assert r.json()["started"] is True
        import time

        for _ in range(50):
            if warmup_mod.is_search_ready():
                break
            time.sleep(0.02)
        assert warmup_mod.is_search_ready() is True
        r2 = await client.get("/api/search/ready")
        assert r2.json()["ready"] is True


def test_gzip_middleware_installed(tmp_path):
    cfg = get_folder_config(tmp_path)
    init_db(cfg.db_path)
    app = build_app(cfg, run_background=False)
    names = [m.cls.__name__ for m in app.user_middleware]
    assert "SkipImageGZipMiddleware" in names


def test_hashed_assets_are_immutably_cached(tmp_path):
    from selects.server.app import _find_static_dir

    cfg = get_folder_config(tmp_path)
    init_db(cfg.db_path)
    app = build_app(cfg, run_background=False)
    static = _find_static_dir()
    js = next((static / "assets").glob("*.js"), None) if static is not None else None
    if js is None:
        pytest.skip("no built frontend assets")
    with TestClient(app) as client:
        r = client.get(f"/assets/{js.name}")
        assert r.status_code == 200
        cc = r.headers.get("cache-control", "")
        assert "max-age=31536000" in cc
        assert "immutable" in cc
