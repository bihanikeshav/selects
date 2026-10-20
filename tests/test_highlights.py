"""Selection quality, user decisions and the real Best Of API contract."""
from datetime import datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient

from selects.config import FolderConfig
from selects.db import init_db, session_scope
from selects.db.models import ClassicalScore, Embedding, Moment, MomentMember, Photo, PhotoCategory, Swipe
from selects.ml.curation import curate
from selects.ml.highlights import select_highlights
from selects.server.app import build_app


def seed(tmp_path, scores=(0.92, 0.91, 0.89)):
    cfg = FolderConfig(folder=tmp_path)
    Session = init_db(cfg.db_path)
    ids = []
    with session_scope(Session) as s:
        for i, score in enumerate(scores):
            source = tmp_path / f"photo-{i}.jpg"
            source.write_bytes(f"original {i}".encode())
            p = Photo(path=str(source), sha256=f"{i + 1:064x}",
                      taken_at=datetime(2026, 1, 1) + timedelta(days=i // 2))
            s.add(p)
            s.flush()
            ids.append(p.id)
            if score is not None:
                s.add(Embedding(photo_id=p.id, siglip=b"\x00" * 2304, aesthetic_iqa=score))
            s.add(ClassicalScore(photo_id=p.id, auto_reject=False))
            s.add(PhotoCategory(photo_id=p.id, primary_category="landscape" if i < 2 else "portrait"))
    return cfg, Session, ids


def test_diversity_adds_coverage_without_overriding_large_quality_gap(tmp_path):
    _, Session, ids = seed(tmp_path)
    with session_scope(Session) as s:
        candidates = curate(s, ids, pct_floor=0)
        plain, _, available = select_highlights(s, candidates, 2, diverse=False)
        balanced, reasons, _ = select_highlights(s, candidates, 2)
        assert [c.photo_id for c in plain] == ids[:2]
        assert [c.photo_id for c in balanced] == [ids[0], ids[2]]
        assert available == 3
        assert any("day" in r for r in reasons[ids[2]])
        candidates[2].combined = 0.1
        balanced, _, _ = select_highlights(s, candidates, 2)
        assert [c.photo_id for c in balanced] == ids[:2]


def test_exact_duplicates_and_missing_scores_are_handled_without_fabricated_quality(tmp_path):
    _, Session, ids = seed(tmp_path, (None, None, None))
    with session_scope(Session) as s:
        s.get(Photo, ids[1]).sha256 = s.get(Photo, ids[0]).sha256
        s.flush()
        candidates = curate(s, ids, pct_floor=0)
        out, reasons, available = select_highlights(s, candidates, 40, diverse=False)
        assert [c.photo_id for c in out] == [ids[0], ids[2]]
        assert available == 2
        assert all("Quality score unavailable" in reasons[c.photo_id][0] for c in out)


@pytest.mark.parametrize("budget", [None, 12, 40, 80])
async def test_budget_filters_before_burst_collapse_and_preserves_originals_and_keeps(tmp_path, budget):
    cfg, Session, ids = seed(tmp_path, (0.99, 0.9, 0.8, 0.7))
    before = {p: p.read_bytes() for p in tmp_path.glob("*.jpg")}
    with session_scope(Session) as s:
        s.add(Swipe(photo_id=ids[0], decision="reject"))
        s.get(ClassicalScore, ids[2]).auto_reject = True
        s.add(Swipe(photo_id=ids[3], decision="keep"))
        s.get(ClassicalScore, ids[3]).auto_reject = True
        m = Moment(primary_photo_id=ids[0], size=2,
                   started_at=datetime(2026, 1, 1), ended_at=datetime(2026, 1, 1))
        s.add(m)
        s.flush()
        s.add_all([MomentMember(moment_id=m.id, photo_id=pid, rank=i) for i, pid in enumerate(ids[:2])])
    app = build_app(cfg, run_background=False)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        params = {"facet": "library", "value": "all", "scope_pct": 0, "library_pct": 0}
        if budget is not None:
            params["budget"] = budget
        response = await client.get("/api/curate", params=params)
        assert response.status_code == 200
        body = response.json()
        assert body["available"] == body["total"] == 2
        assert {p["photo_id"] for p in body["photos"]} == {ids[1], ids[3]}
        if budget is not None:
            assert all(p["selection_reasons"] for p in body["photos"])
    with session_scope(Session) as s:
        assert {r.photo_id: r.decision for r in s.query(Swipe)} == {ids[0]: "reject", ids[3]: "keep"}
        assert s.get(Moment, m.id).primary_photo_id == ids[0]
    assert all(p.read_bytes() == original for p, original in before.items())


@pytest.mark.parametrize("query", ["budget=0", "budget=1001", "budget=-1", "scope_pct=101", "library_pct=-1"])
async def test_invalid_selection_parameters_are_rejected(tmp_path, query):
    cfg, _, _ = seed(tmp_path)
    app = build_app(cfg, run_background=False)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.get(f"/api/curate?facet=library&value=all&{query}")).status_code == 422


async def test_budget_counts_full_pool_before_response_limit(tmp_path):
    cfg, _, ids = seed(tmp_path)
    app = build_app(cfg, run_background=False)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        result = (await client.get("/api/curate?facet=library&value=all&scope_pct=0&library_pct=0&budget=2&limit=1")).json()
        assert result["total"] == 1
        assert result["available"] == len(ids)


def test_preview_repair_keeps_existing_analysis(tmp_path):
    from PIL import Image
    from selects.db.models import PipelineState
    from selects.indexer.orchestrator import index_folder

    source = tmp_path / "photo.jpg"
    Image.new("RGB", (40, 30)).save(source)
    cfg = FolderConfig(folder=tmp_path)
    index_folder(cfg)
    Session = init_db(cfg.db_path)
    with session_scope(Session) as s:
        p = s.query(Photo).one()
        state = s.get(PipelineState, p.id)
        state.classical_done = state.embedding_done = True
        preview = cfg.state_dir / p.preview_path
    preview.unlink()
    index_folder(cfg)
    with session_scope(Session) as s:
        state = s.query(PipelineState).one()
        assert state.classical_done and state.embedding_done
    assert preview.is_file()
