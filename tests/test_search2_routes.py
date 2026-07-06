"""Tests for the hybrid discovery search endpoint (/api/search2)."""
from __future__ import annotations

from datetime import datetime

import numpy as np
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from travelcull.config import get_folder_config
from travelcull.db import init_db, session_scope
from travelcull.db.models import (
    AestheticScore, Embedding, FaceEmbedding, Person, Photo, PhotoPerson, PhotoTag,
)
from travelcull.server.search2_routes import register_search2_routes


def _siglip_blob(vec: np.ndarray) -> bytes:
    v = vec.astype(np.float32)
    v = v / (np.linalg.norm(v) + 1e-9)
    return v.astype(np.float16).tobytes()


@pytest.fixture
def app_and_ids(tmp_path, monkeypatch):
    cfg = get_folder_config(tmp_path)
    Session = init_db(cfg.db_path)

    # Three orthogonal-ish directions in a small embedding space (padded to 1152
    # dims like real SigLIP so siglip_bytes_to_matrix's dtype path is exercised).
    dim = 1152
    rng = np.zeros((3, dim), dtype=np.float32)
    rng[0, 0] = 1.0   # "monastery" photo: aligned with query vector below
    rng[1, 1] = 1.0   # unrelated photo, weak similarity to query
    rng[2, 0] = 0.4
    rng[2, 1] = 0.9   # another unrelated-ish photo, but has an exact tag hit

    ids = {}
    with session_scope(Session) as s:
        photos = [
            Photo(path=str(tmp_path / "a.jpg"), sha256="a" * 64, taken_at=datetime(2024, 1, 1)),
            Photo(path=str(tmp_path / "b.jpg"), sha256="b" * 64, taken_at=datetime(2024, 6, 1)),
            Photo(path=str(tmp_path / "c.jpg"), sha256="c" * 64, taken_at=datetime(2024, 3, 1)),
        ]
        s.add_all(photos)
        s.flush()
        ids["a"], ids["b"], ids["c"] = photos[0].id, photos[1].id, photos[2].id

        s.add_all([
            Embedding(photo_id=photos[0].id, siglip=_siglip_blob(rng[0])),
            Embedding(photo_id=photos[1].id, siglip=_siglip_blob(rng[1])),
            Embedding(photo_id=photos[2].id, siglip=_siglip_blob(rng[2])),
        ])
        # Photo c has an exact tag hit for the query word "monastery" even though
        # its semantic similarity is weaker than photo a's.
        s.add(PhotoTag(photo_id=photos[2].id, tag="monastery", score=0.9, source="ram"))
        s.add(AestheticScore(photo_id=photos[0].id, nima_score=8.0))
        s.add(AestheticScore(photo_id=photos[1].id, nima_score=2.0))

        person = Person(label="Alice")
        s.add(person)
        face = FaceEmbedding(
            photo_id=photos[1].id, face_index=0, embedding=b"\x00" * 1024,
            bbox_x=0, bbox_y=0, bbox_w=10, bbox_h=10, confidence=0.9,
        )
        s.add(face)
        s.flush()
        s.add(PhotoPerson(photo_id=photos[1].id, person_id=person.id, face_embedding_id=face.id, confidence=0.9))
        ids["person"] = person.id

    # Query vector aligned with photo a's direction -> highest raw semantic score.
    query_vec = np.zeros(dim, dtype=np.float32)
    query_vec[0] = 1.0
    monkeypatch.setattr("travelcull.ml.search.embed_query", lambda q: query_vec)

    app = FastAPI()
    register_search2_routes(app, cfg)
    return app, ids


async def _get(app: FastAPI, url: str):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get(url)


async def test_no_filters_rejected(app_and_ids):
    app, _ = app_and_ids
    r = await _get(app, "/api/search2")
    assert r.status_code == 400


async def test_exact_tag_hit_outranks_weak_semantic_match(app_and_ids):
    app, ids = app_and_ids
    r = await _get(app, "/api/search2?q=monastery")
    assert r.status_code == 200
    body = r.json()
    result_ids = [item["photo_id"] for item in body["results"]]
    # photo "a" has the strongest raw semantic similarity to the query vector,
    # but photo "c" carries an exact tag match ("monastery") and must rank first.
    assert result_ids[0] == ids["c"]
    assert ids["a"] in result_ids


async def test_person_filter(app_and_ids):
    app, ids = app_and_ids
    r = await _get(app, f"/api/search2?person_id={ids['person']}")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    assert body["results"][0]["photo_id"] == ids["b"]


async def test_min_aesthetic_filter(app_and_ids):
    app, ids = app_and_ids
    r = await _get(app, "/api/search2?min_aesthetic=5")
    assert r.status_code == 200
    body = r.json()
    result_ids = {item["photo_id"] for item in body["results"]}
    assert result_ids == {ids["a"]}


async def test_date_range_filter(app_and_ids):
    app, ids = app_and_ids
    r = await _get(app, "/api/search2?date_from=2024-05-01&date_to=2024-12-31")
    assert r.status_code == 200
    body = r.json()
    result_ids = {item["photo_id"] for item in body["results"]}
    assert result_ids == {ids["b"]}


async def test_tags_filter(app_and_ids):
    app, ids = app_and_ids
    r = await _get(app, "/api/search2?tags=monastery")
    assert r.status_code == 200
    body = r.json()
    result_ids = {item["photo_id"] for item in body["results"]}
    assert result_ids == {ids["c"]}


async def test_invalid_date_returns_400(app_and_ids):
    app, _ = app_and_ids
    r = await _get(app, "/api/search2?date_from=not-a-date")
    assert r.status_code == 400
