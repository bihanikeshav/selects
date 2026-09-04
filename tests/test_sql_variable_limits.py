"""Every library-sized ``IN (...)`` must survive SQLite's bound-variable cap.

Each test seeds 1,200 rows and pins the classic 999-variable ceiling (the
bundled SQLite allows 32,766, which hides the bug locally) via the
``sqlite_999_variables`` context manager in ``tests/conftest.py``. Without the
chunking/subquery fixes each of these raises
``OperationalError: too many SQL variables``.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pytest
from httpx import ASGITransport, AsyncClient

from selects.config import get_folder_config
from selects.db import init_db, session_scope
from selects.db.models import (
    AestheticScore,
    ClassicalScore,
    Embedding,
    FaceEmbedding,
    Moment,
    MomentMember,
    Person,
    Photo,
    PhotoPerson,
    PhotoTag,
    PipelineState,
    Story,
    StoryItem,
    Swipe,
    Visit,
)
from selects.server.app import build_app
from tests.conftest import engine_for, sqlite_999_variables

N = 1200
DIM = 1152
_BASE = datetime(2024, 5, 1, 9, 0, 0)


def _siglip_blob() -> bytes:
    v = np.full(DIM, 0.03, dtype=np.float16)
    return v.tobytes()


def _seed_photos(tmp_path: Path, n: int = N):
    """Create *n* photos with embeddings + aesthetic scores. Returns (cfg, Session, ids)."""
    cfg = get_folder_config(tmp_path)
    Session = init_db(cfg.db_path)
    blob = _siglip_blob()
    with session_scope(Session) as s:
        photos = [
            Photo(
                path=str(tmp_path / f"{i:05d}.jpg"),
                sha256=f"{i:064x}",
                taken_at=_BASE + timedelta(seconds=i),
            )
            for i in range(n)
        ]
        s.add_all(photos)
        s.flush()
        ids = [p.id for p in photos]
        s.add_all([
            Embedding(photo_id=pid, siglip=blob, aesthetic_iqa=0.4 + (i % 100) / 250.0)
            for i, pid in enumerate(ids)
        ])
        s.add_all([
            AestheticScore(photo_id=pid, ap25_score=5.0 + (i % 40) / 10.0, nima_score=5.0)
            for i, pid in enumerate(ids)
        ])
    return cfg, Session, ids


def _endpoint(cfg, path: str):
    """Return the route handler registered at *path*.

    Used where the request would carry 1,200 sha256s: httpx refuses URLs over
    64 KB, but the handler is still what has to survive the variable cap.
    """
    app = build_app(cfg, run_background=False)
    for route in app.routes:
        if getattr(route, "path", None) == path:
            return route.endpoint
    raise AssertionError(f"no route registered at {path}")


async def _get(cfg, url: str):
    app = build_app(cfg, run_background=False)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get(url)


# ── selects/ml/curation.py ────────────────────────────────────────────────────

def test_curation_curates_more_photos_than_sqlite_allows_variables(tmp_path):
    from selects.ml.curation import curate

    cfg, Session, ids = _seed_photos(tmp_path)
    with session_scope(Session) as s:
        # One burst so _dedup_and_rank's MomentMember/Moment INs are exercised too.
        for start in range(0, N, 4):
            members = ids[start:start + 4]
            m = Moment(
                primary_photo_id=members[0],
                started_at=_BASE,
                ended_at=_BASE + timedelta(seconds=4),
                size=len(members),
            )
            s.add(m)
            s.flush()
            s.add_all([
                MomentMember(moment_id=m.id, photo_id=pid, rank=r)
                for r, pid in enumerate(members)
            ])

    with sqlite_999_variables(engine_for(cfg.db_path)):
        with session_scope(Session) as s:
            out = curate(s, ids, pct_floor=0.0)
    # Every burst collapses to one survivor; nothing was lost to chunking.
    assert len(out) == N // 4
    assert len({c.photo_id for c in out}) == len(out)


# ── selects/ml/faces.py ───────────────────────────────────────────────────────

def test_face_embedding_stage_loads_previews_for_more_photos_than_variables(tmp_path):
    from selects.ml.faces import run_face_embedding_stage

    cfg, Session, ids = _seed_photos(tmp_path)
    with session_scope(Session) as s:
        s.add_all([ClassicalScore(photo_id=pid, faces_count=1) for pid in ids])

    with sqlite_999_variables(engine_for(cfg.db_path)):
        # No preview_path on any row, so every photo is skipped after the
        # id -> preview lookup — which is the query under test.
        assert run_face_embedding_stage(cfg) == 0


# ── selects/ml/face_attributes.py ─────────────────────────────────────────────

def test_stack_face_penalties_handles_more_ids_than_variables(tmp_path):
    from selects.ml.face_attributes import stack_face_penalties

    cfg, Session, ids = _seed_photos(tmp_path)
    with session_scope(Session) as s:
        s.add_all([
            FaceEmbedding(
                photo_id=pid, face_index=0, embedding=b"\x00" * 8,
                bbox_x=0, bbox_y=0, bbox_w=50, bbox_h=50, confidence=0.9,
                eyes_open=0.9, yaw=0.0, pitch=0.0, face_area_ratio=0.1,
            )
            for pid in ids
        ])

    with sqlite_999_variables(engine_for(cfg.db_path)):
        with session_scope(Session) as s:
            out = stack_face_penalties(s, ids)
    assert len(out) == N


# ── selects/ml/ram_tags.py ────────────────────────────────────────────────────

def test_ram_tagging_stage_selects_more_todo_photos_than_variables(tmp_path, monkeypatch):
    import selects.ml.ram_tags as ram_tags

    cfg, Session, ids = _seed_photos(tmp_path)

    class _ModelWanted(Exception):
        pass

    def _boom(_model):
        raise _ModelWanted

    # The todo query runs before the model loads; stop there so the test stays
    # offline and CPU-only.
    monkeypatch.setattr(ram_tags, "_load_ram", _boom)

    with sqlite_999_variables(engine_for(cfg.db_path)):
        with pytest.raises(_ModelWanted):
            ram_tags.run_ram_tagging_stage(cfg)


# ── selects/ml/tags.py ────────────────────────────────────────────────────────

def test_tag_stage_deletes_old_rows_for_more_photos_than_variables(tmp_path, monkeypatch):
    import selects.ml.tags as tags_mod

    cfg, Session, ids = _seed_photos(tmp_path)
    with session_scope(Session) as s:
        s.add_all([PipelineState(photo_id=pid, embedding_done=True) for pid in ids])
        # Stale source-NULL rows: the wipe before the rewrite is the chunked IN.
        s.add_all([PhotoTag(photo_id=pid, tag="stale", score=1.0) for pid in ids])

    def _fake_encode(prompts):
        rng = np.random.default_rng(0)
        x = rng.standard_normal((len(prompts), DIM)).astype(np.float32)
        return x / np.linalg.norm(x, axis=-1, keepdims=True)

    monkeypatch.setattr(tags_mod, "encode_text_prompts", _fake_encode)

    with sqlite_999_variables(engine_for(cfg.db_path)):
        assert tags_mod.run_tag_stage(cfg) == N

    with session_scope(Session) as s:
        assert s.query(PhotoTag).filter(PhotoTag.tag == "stale").count() == 0


# ── selects/ml/nl_story.py ────────────────────────────────────────────────────

def test_compose_story_filters_by_more_visit_names_than_variables(tmp_path):
    from selects.ml.nl_story import compose_story

    cfg, Session, ids = _seed_photos(tmp_path)
    names = [f"place{i:05d}" for i in range(N)]
    with session_scope(Session) as s:
        story = Story(day="2024-05-01", title="t", photo_count=0, created_at=_BASE)
        s.add(story)
        s.flush()
        s.add_all([
            Visit(
                story_id=story.id, rank=i, name=name, lat=0.0, lon=0.0,
                arrived_at=_BASE, departed_at=_BASE + timedelta(days=1), photo_count=1,
            )
            for i, name in enumerate(names)
        ])

    # Every visit name appears in the query, so all 1,200 land in location_names.
    query = " ".join(names)
    with sqlite_999_variables(engine_for(cfg.db_path)):
        story_out = compose_story(cfg, query)
    assert len(story_out.parsed.location_names) == N
    assert story_out.items


# ── selects/server/clusters_routes.py ─────────────────────────────────────────

async def test_clusters_routes_handle_more_photos_than_variables(tmp_path):
    cfg, Session, ids = _seed_photos(tmp_path)
    with session_scope(Session) as s:
        s.add_all([
            PhotoTag(photo_id=pid, tag="temple", score=1.0, source="thematic")
            for pid in ids
        ])
        # One untagged photo, so the NOT-IN subquery is checked against a
        # non-empty answer and not just "returns nothing".
        loner = Photo(
            path=str(tmp_path / "untagged.jpg"),
            sha256=f"{N:064x}",
            taken_at=_BASE,
        )
        s.add(loner)
        s.flush()
        loner_sha = loner.sha256

    with sqlite_999_variables(engine_for(cfg.db_path)):
        r = await _get(cfg, "/api/clusters?min_count=1")
        assert r.status_code == 200, r.text
        assert any(c["tag"] == "temple" for c in r.json()["clusters"])

        r = await _get(cfg, "/api/clusters/temple/photos?limit=50")
        assert r.status_code == 200, r.text
        assert len(r.json()["items"]) == 50

        r = await _get(cfg, "/api/clusters/uncategorized/photos?limit=50")
        assert r.status_code == 200, r.text
        items = r.json()["items"]
        assert [it["sha256"] for it in items] == [loner_sha]


# ── selects/server/editor_routes.py ───────────────────────────────────────────

def test_edits_status_handles_more_shas_than_variables(tmp_path):
    cfg, Session, ids = _seed_photos(tmp_path)
    shas = ",".join(f"{i:064x}" for i in range(N))

    with sqlite_999_variables(engine_for(cfg.db_path)):
        out = _endpoint(cfg, "/api/edits/status")(shas=shas)
    assert len(out) == N
    assert all(v["edited"] is False for v in out.values())


# ── selects/server/export_routes.py ───────────────────────────────────────────

async def test_xmp_preview_resolves_a_story_bigger_than_variables(tmp_path):
    cfg, Session, ids = _seed_photos(tmp_path)
    with session_scope(Session) as s:
        story = Story(day="2024-05-01", title="t", photo_count=N, created_at=_BASE)
        s.add(story)
        s.flush()
        story_id = story.id
        s.add_all([
            StoryItem(story_id=story_id, rank=i, photo_id=pid) for i, pid in enumerate(ids)
        ])
        s.add_all([
            Swipe(photo_id=pid, decision="keep", swiped_at=_BASE) for pid in ids
        ])

    with sqlite_999_variables(engine_for(cfg.db_path)):
        r = await _get(cfg, f"/api/export/xmp/preview?source=story:{story_id}")
    assert r.status_code == 200, r.text
    assert r.json()["total"] == N


# ── selects/server/persons_routes.py ──────────────────────────────────────────

async def test_persons_routes_handle_more_persons_and_photos_than_variables(tmp_path):
    cfg, Session, ids = _seed_photos(tmp_path)
    with session_scope(Session) as s:
        # 1,200 persons (the /api/persons cover query) …
        persons = [Person(photo_count=1, created_at=_BASE) for _ in range(N)]
        s.add_all(persons)
        s.flush()
        person_ids = [p.id for p in persons]
        faces = [
            FaceEmbedding(
                photo_id=pid, face_index=0, embedding=b"\x00" * 8,
                bbox_x=0, bbox_y=0, bbox_w=80, bbox_h=80, confidence=0.9,
            )
            for pid in ids
        ]
        s.add_all(faces)
        s.flush()
        s.add_all([
            PhotoPerson(
                photo_id=pid, person_id=person_id,
                face_embedding_id=face.id, confidence=0.9,
            )
            for pid, person_id, face in zip(ids, person_ids, faces)
        ])
        # … and one person that owns every photo (the /photos ORDER BY + LIMIT).
        big = Person(photo_count=N, created_at=_BASE)
        s.add(big)
        s.flush()
        big_id = big.id
        s.add_all([
            PhotoPerson(
                photo_id=pid, person_id=big_id,
                face_embedding_id=face.id, confidence=0.8,
            )
            for pid, face in zip(ids, faces)
        ])

    with sqlite_999_variables(engine_for(cfg.db_path)):
        r = await _get(cfg, "/api/persons?min_photo_count=1")
        assert r.status_code == 200, r.text
        assert len(r.json()["persons"]) == N + 1

        r = await _get(cfg, f"/api/persons/{big_id}/photos?limit=50")
        assert r.status_code == 200, r.text
        assert len(r.json()["items"]) == 50


# ── selects/server/photos_routes.py ───────────────────────────────────────────

def test_likes_status_handles_more_shas_than_variables(tmp_path):
    cfg, Session, ids = _seed_photos(tmp_path)
    with session_scope(Session) as s:
        s.add_all([Swipe(photo_id=pid, decision="keep", swiped_at=_BASE) for pid in ids])
    shas = ",".join(f"{i:064x}" for i in range(N))

    with sqlite_999_variables(engine_for(cfg.db_path)):
        out = _endpoint(cfg, "/api/likes/status")(shas=shas)
    assert len(out) == N
    assert all(out.values())


# ── selects/server/search2_routes.py ──────────────────────────────────────────

async def test_search2_handles_a_candidate_set_bigger_than_variables(tmp_path, monkeypatch):
    import selects.ml.search as search_mod

    cfg, Session, ids = _seed_photos(tmp_path)
    with session_scope(Session) as s:
        s.add_all([PhotoTag(photo_id=pid, tag="temple", score=1.0) for pid in ids])

    with sqlite_999_variables(engine_for(cfg.db_path)):
        # No free-text query: candidate ids come from the tag filter and both
        # the sha lookup and the taken_at ordering lookup take the whole set.
        r = await _get(cfg, "/api/search2?tags=temple&limit=50")
        assert r.status_code == 200, r.text
        assert r.json()["total"] == 50

        # With a free-text query the tag-bonus query is restricted by the same
        # candidate set. The embedding matrix is stubbed to keep this CPU-only.
        monkeypatch.setattr(
            search_mod, "library_embedding_matrix",
            lambda _cfg: (np.zeros((0, DIM), dtype=np.float32), [], []),
        )
        monkeypatch.setattr(
            search_mod, "embed_query", lambda _q: np.zeros(DIM, dtype=np.float32)
        )
        r = await _get(cfg, "/api/search2?q=temple&tags=temple&limit=50")
        assert r.status_code == 200, r.text


# ── selects/server/stories_routes.py ──────────────────────────────────────────

async def test_stories_route_handles_a_day_bigger_than_variables(tmp_path):
    cfg, Session, ids = _seed_photos(tmp_path)
    with session_scope(Session) as s:
        story = Story(day="2024-05-01", title="t", photo_count=N, created_at=_BASE)
        s.add(story)
        s.flush()
        story_id = story.id
        s.add_all([
            StoryItem(story_id=story_id, rank=i, photo_id=pid) for i, pid in enumerate(ids)
        ])
        s.add_all([PhotoTag(photo_id=pid, tag="temple", score=1.0) for pid in ids])
        s.add_all([Swipe(photo_id=pid, decision="keep", swiped_at=_BASE) for pid in ids])
        s.add_all([
            Visit(
                story_id=story_id, rank=0, name="place", lat=0.0, lon=0.0,
                arrived_at=_BASE, departed_at=_BASE + timedelta(days=1),
                photo_count=N, cover_photo_id=ids[0],
            )
        ])

    with sqlite_999_variables(engine_for(cfg.db_path)):
        # curated=1 walks the whole day through curate(); the legacy tag filter
        # and liked_only each take the full photo-id list.
        r = await _get(cfg, "/api/stories?curated=true")
        assert r.status_code == 200, r.text
        assert r.json()["stories"]

        r = await _get(cfg, "/api/stories?curated=false&liked_only=true&include_tags=temple")
        assert r.status_code == 200, r.text
        assert r.json()["stories"][0]["photo_count"] > 0
