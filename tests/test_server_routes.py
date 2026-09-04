import shutil
from pathlib import Path

import pytest
from httpx import AsyncClient, ASGITransport

from selects.config import get_folder_config
from selects.db import init_db
from selects.indexer.orchestrator import index_folder
from selects.pipeline import run_classical_stage
from selects.server.app import build_app

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def populated_folder(tmp_path):
    for f in FIXTURES_DIR.iterdir():
        if f.is_file() and f.suffix.lower() in {".jpg", ".jpeg", ".heic", ".heif", ".mp4"}:
            shutil.copy(f, tmp_path / f.name)
    return tmp_path


async def test_health_endpoint(tmp_path):
    cfg = get_folder_config(tmp_path)
    init_db(cfg.db_path)
    app = build_app(cfg, run_background=False)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/api/health")
        assert r.status_code == 200


async def test_openapi_version_matches_package_version(tmp_path):
    import selects

    cfg = get_folder_config(tmp_path)
    init_db(cfg.db_path)
    app = build_app(cfg, run_background=False)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/openapi.json")
        assert r.status_code == 200
        assert r.json()["info"]["version"] == selects.__version__


def test_no_hardcoded_version_literal_in_package():
    """No source file may pin a version string — they go stale (e.g. the
    Nominatim User-Agent in ``selects/ml/locations.py``). Use ``__version__``."""
    import selects

    import re

    pkg_root = Path(selects.__file__).parent
    stale = "0.1.13"
    ua_literal = re.compile(r"selects/\d+\.\d+\.\d+")
    stale_hits, ua_hits = [], []
    for py in pkg_root.rglob("*.py"):
        if py.name == "__init__.py" and py.parent == pkg_root:
            continue  # the one place the version is allowed to be a literal
        text = py.read_text(encoding="utf-8")
        rel = str(py.relative_to(pkg_root))
        if stale in text:
            stale_hits.append(rel)
        if ua_literal.search(text):
            ua_hits.append(rel)
    assert stale_hits == [], f"hard-coded version literal {stale!r} in: {stale_hits}"
    assert ua_hits == [], f"hard-coded selects/<version> string in: {ua_hits}"


async def test_list_photos_returns_indexed_files(populated_folder):
    cfg = get_folder_config(populated_folder)
    init_db(cfg.db_path)
    index_folder(cfg)
    run_classical_stage(cfg)

    app = build_app(cfg, run_background=False)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/api/photos")
        assert r.status_code == 200
        body = r.json()
        assert body["total"] >= 2
        assert all("sha256" in item for item in body["items"])
        assert all("auto_reject" in item for item in body["items"])


async def test_get_thumbnail_returns_image(populated_folder):
    cfg = get_folder_config(populated_folder)
    init_db(cfg.db_path)
    index_folder(cfg)

    app = build_app(cfg, run_background=False)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        listing = (await client.get("/api/photos")).json()
        sha = listing["items"][0]["sha256"]
        r = await client.get(f"/api/thumb/{sha}")
        assert r.status_code == 200
        assert r.headers["content-type"] == "image/jpeg"
        assert len(r.content) > 0


async def test_editor_result_falls_back_to_preview(populated_folder):
    cfg = get_folder_config(populated_folder)
    init_db(cfg.db_path)
    index_folder(cfg)
    app = build_app(cfg, run_background=False)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        listing = (await client.get("/api/photos")).json()
        sha = listing["items"][0]["sha256"]
        r = await client.get(f"/api/editor/result/{sha}")
        assert r.status_code == 200
        assert r.headers["content-type"] == "image/jpeg"
        assert "max-age=31536000" in r.headers.get("cache-control", "")


async def test_thumb_is_immutably_cached(populated_folder):
    cfg = get_folder_config(populated_folder)
    init_db(cfg.db_path)
    index_folder(cfg)
    app = build_app(cfg, run_background=False)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        listing = (await client.get("/api/photos")).json()
        sha = listing["items"][0]["sha256"]
        r = await client.get(f"/api/thumb/{sha}", headers={"Accept-Encoding": "gzip"})
        assert r.status_code == 200
        assert "max-age=31536000" in r.headers.get("cache-control", "")
        assert r.headers.get("content-encoding") != "gzip"


async def test_list_photos_sort_aesthetic_prefers_ap25(tmp_path):
    from datetime import datetime

    from selects.db import session_scope
    from selects.db.models import AestheticScore, Embedding, Photo

    cfg = get_folder_config(tmp_path)
    Session = init_db(cfg.db_path)
    with session_scope(Session) as s:
        low_iqa = Photo(path=str(tmp_path / "a.jpg"), sha256="a" * 64, taken_at=datetime(2024, 1, 1))
        high_ap = Photo(path=str(tmp_path / "b.jpg"), sha256="b" * 64, taken_at=datetime(2024, 1, 2))
        s.add_all([low_iqa, high_ap])
        s.flush()
        s.add(Embedding(photo_id=low_iqa.id, siglip=b"\x00" * 2304, aesthetic_iqa=0.95))
        s.add(Embedding(photo_id=high_ap.id, siglip=b"\x00" * 2304, aesthetic_iqa=0.2))
        s.add(AestheticScore(photo_id=low_iqa.id, ap25_score=2.0))
        s.add(AestheticScore(photo_id=high_ap.id, ap25_score=8.0))

    app = build_app(cfg, run_background=False)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/api/photos?sort=aesthetic&collapse=none")
        assert r.status_code == 200
        shas = [item["sha256"] for item in r.json()["items"]]
        assert shas[0] == "b" * 64


async def test_list_photos_sort_aesthetic_uses_iqa(tmp_path):
    from datetime import datetime

    from selects.db import session_scope
    from selects.db.models import Embedding, Photo

    cfg = get_folder_config(tmp_path)
    Session = init_db(cfg.db_path)
    with session_scope(Session) as s:
        low = Photo(path=str(tmp_path / "low.jpg"), sha256="a" * 64, taken_at=datetime(2024, 1, 1))
        high = Photo(path=str(tmp_path / "high.jpg"), sha256="b" * 64, taken_at=datetime(2024, 1, 2))
        missing = Photo(path=str(tmp_path / "none.jpg"), sha256="c" * 64, taken_at=datetime(2024, 1, 3))
        s.add_all([low, high, missing])
        s.flush()
        s.add(Embedding(photo_id=low.id, siglip=b"\x00" * 2304, aesthetic_iqa=0.2))
        s.add(Embedding(photo_id=high.id, siglip=b"\x00" * 2304, aesthetic_iqa=0.9))
        s.add(Embedding(photo_id=missing.id, siglip=b"\x00" * 2304, aesthetic_iqa=None))

    app = build_app(cfg, run_background=False)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/api/photos?sort=aesthetic&collapse=none")
        assert r.status_code == 200
        body = r.json()
        # Null IQA is sorted last, not dropped; total is counted after filters.
        assert body["total"] == 3
        iqas = [item["aesthetic_iqa"] for item in body["items"]]
        assert iqas[0] == pytest.approx(0.9)
        assert iqas[1] == pytest.approx(0.2)
        assert iqas[2] is None


async def test_list_photos_sort_random_seed_is_stable_and_pages_are_disjoint(tmp_path):
    """``sort=random&seed=N`` must be a *fixed* shuffle: the same order on every
    call, and consecutive OFFSET pages that never repeat a photo. Unseeded
    ``random`` re-rolls per statement, which is what makes paging lose photos."""
    from selects.db import session_scope
    from selects.db.models import Photo

    cfg = get_folder_config(tmp_path)
    Session = init_db(cfg.db_path)
    with session_scope(Session) as s:
        s.add_all([
            Photo(path=str(tmp_path / f"{i}.jpg"), sha256=f"{i:064x}")
            for i in range(30)
        ])

    app = build_app(cfg, run_background=False)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        first = await client.get("/api/photos?sort=random&seed=7&collapse=none&limit=30")
        second = await client.get("/api/photos?sort=random&seed=7&collapse=none&limit=30")
        assert first.status_code == 200
        order_a = [i["sha256"] for i in first.json()["items"]]
        order_b = [i["sha256"] for i in second.json()["items"]]
        assert len(order_a) == 30
        assert order_a == order_b

        # A different seed is a different order (the modular scatter only wraps
        # once the seed is large, which is the range the UI draws from).
        other = await client.get(
            "/api/photos?sort=random&seed=1103515245&collapse=none&limit=30"
        )
        assert [i["sha256"] for i in other.json()["items"]] != order_a

        page1 = await client.get(
            "/api/photos?sort=random&seed=7&collapse=none&limit=10&offset=0"
        )
        page2 = await client.get(
            "/api/photos?sort=random&seed=7&collapse=none&limit=10&offset=10"
        )
        ids1 = [i["sha256"] for i in page1.json()["items"]]
        ids2 = [i["sha256"] for i in page2.json()["items"]]
        assert len(ids1) == len(ids2) == 10
        assert set(ids1).isdisjoint(ids2)
        assert ids1 + ids2 == order_a[:20]

        # No seed: still valid, just not reproducible.
        unseeded = await client.get("/api/photos?sort=random&collapse=none&limit=30")
        assert unseeded.status_code == 200
        assert len(unseeded.json()["items"]) == 30


async def test_list_photos_sort_ties_are_broken_by_id(tmp_path):
    """Equal scores must not leave the order up to SQLite: ``Photo.id`` is the
    final tiebreaker, so a page boundary can never repeat or drop a photo."""
    from selects.db import session_scope
    from selects.db.models import AestheticScore, Embedding, Photo

    cfg = get_folder_config(tmp_path)
    Session = init_db(cfg.db_path)
    with session_scope(Session) as s:
        photos = [
            Photo(path=str(tmp_path / f"{i}.jpg"), sha256=f"{i:064x}")
            for i in range(6)
        ]
        s.add_all(photos)
        s.flush()
        for p in photos:
            s.add(Embedding(photo_id=p.id, siglip=b"\x00" * 2304, aesthetic_iqa=0.5))
            s.add(AestheticScore(photo_id=p.id, ap25_score=5.0))
        ordered_shas = [p.sha256 for p in sorted(photos, key=lambda p: p.id)]

    app = build_app(cfg, run_background=False)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/api/photos?sort=aesthetic&collapse=none&limit=6")
        assert r.status_code == 200
        assert [i["sha256"] for i in r.json()["items"]] == ordered_shas

        p1 = await client.get("/api/photos?sort=aesthetic&collapse=none&limit=3&offset=0")
        p2 = await client.get("/api/photos?sort=aesthetic&collapse=none&limit=3&offset=3")
        got = [i["sha256"] for i in p1.json()["items"]]
        got += [i["sha256"] for i in p2.json()["items"]]
        assert got == ordered_shas


async def test_list_curated_sort_aesthetic_uses_iqa(tmp_path):
    from datetime import datetime

    from selects.db import session_scope
    from selects.db.models import Embedding, Photo, Swipe

    cfg = get_folder_config(tmp_path)
    Session = init_db(cfg.db_path)
    with session_scope(Session) as s:
        low = Photo(path=str(tmp_path / "low.jpg"), sha256="a" * 64, taken_at=datetime(2024, 1, 1))
        high = Photo(path=str(tmp_path / "high.jpg"), sha256="b" * 64, taken_at=datetime(2024, 1, 2))
        missing = Photo(path=str(tmp_path / "none.jpg"), sha256="c" * 64, taken_at=datetime(2024, 1, 3))
        rejected = Photo(
            path=str(tmp_path / "rej.jpg"), sha256="d" * 64, taken_at=datetime(2024, 1, 4)
        )
        s.add_all([low, high, missing, rejected])
        s.flush()
        s.add(Embedding(photo_id=low.id, siglip=b"\x00" * 2304, aesthetic_iqa=0.2))
        s.add(Embedding(photo_id=high.id, siglip=b"\x00" * 2304, aesthetic_iqa=0.9))
        s.add(Embedding(photo_id=missing.id, siglip=b"\x00" * 2304, aesthetic_iqa=None))
        s.add(Embedding(photo_id=rejected.id, siglip=b"\x00" * 2304, aesthetic_iqa=0.99))
        s.add(Swipe(photo_id=low.id, decision="keep"))
        s.add(Swipe(photo_id=high.id, decision="silver"))
        s.add(Swipe(photo_id=missing.id, decision="keep"))
        s.add(Swipe(photo_id=rejected.id, decision="reject"))

    app = build_app(cfg, run_background=False)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/api/curated?sort=aesthetic")
        assert r.status_code == 200
        body = r.json()
        # Liked photos missing IQA stay in the set (nulls last); rejects are dropped.
        assert body["total"] == 3
        photos = body["photos"]
        iqas = [item["iqa"] for item in photos]
        assert iqas[0] == pytest.approx(0.9)
        assert iqas[1] == pytest.approx(0.2)
        assert iqas[2] is None
        assert [item["combined"] for item in photos] == iqas
        assert all(item["ap25"] is None and item["nima"] is None for item in photos)


async def test_photos_blurry_keepers_bucket_uses_iqa_percentile(tmp_path):
    from selects.db import session_scope
    from selects.db.models import ClassicalScore, Embedding, Photo

    cfg = get_folder_config(tmp_path)
    Session = init_db(cfg.db_path)
    with session_scope(Session) as s:
        for i, iqa in enumerate([0.2, 0.4, 0.6, 0.8]):
            p = Photo(path=str(tmp_path / f"{i}.jpg"), sha256=f"{i:064x}")
            s.add(p)
            s.flush()
            s.add(ClassicalScore(photo_id=p.id, blur=200.0, luma_mean=0.5))
            s.add(Embedding(photo_id=p.id, siglip=b"\x00" * 2304, aesthetic_iqa=iqa))

    app = build_app(cfg, run_background=False)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/api/photos?quality=blurry_keepers&collapse=none")
        assert r.status_code == 200
        body = r.json()
        # Library 50th percentile of [0.2, 0.4, 0.6, 0.8] is 0.5.
        keeper_iqas = sorted(item["aesthetic_iqa"] for item in body["items"])
        assert keeper_iqas == [pytest.approx(0.6), pytest.approx(0.8)]
        assert body["total"] == 2


@pytest.mark.parametrize(
    "path",
    [
        "/api/doctor/issues",
        "/api/calibrate/next",
        "/api/search?q=hello",
        "/api/moments",
        "/api/photos/" + "a" * 64 + "/tags",
    ],
)
async def test_removed_endpoints_are_gone(tmp_path, path):
    from selects.db import session_scope
    from selects.db.models import Photo, PhotoTag

    cfg = get_folder_config(tmp_path)
    Session = init_db(cfg.db_path)
    # A real photo with tags: /api/photos/<sha>/tags would answer 200 if the
    # endpoint still existed, so the 404 below is about the route, not the row.
    with session_scope(Session) as s:
        photo = Photo(path=str(tmp_path / "a.jpg"), sha256="a" * 64)
        s.add(photo)
        s.flush()
        s.add(PhotoTag(photo_id=photo.id, tag="mountain", score=0.9, source="thematic"))

    app = build_app(cfg, run_background=False)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get(path)
        assert r.status_code == 404


async def _seed_moment_library(tmp_path):
    """3 photos; two of them a moment whose primary is the first.

    Verdicts: keep on the primary, reject on the non-primary member.
    """
    from datetime import datetime

    from selects.db import session_scope
    from selects.db.models import Moment, MomentMember, Photo, Swipe

    cfg = get_folder_config(tmp_path)
    Session = init_db(cfg.db_path)
    with session_scope(Session) as s:
        primary = Photo(path=str(tmp_path / "a.jpg"), sha256="a" * 64, taken_at=datetime(2024, 1, 1, 10))
        member = Photo(path=str(tmp_path / "b.jpg"), sha256="b" * 64, taken_at=datetime(2024, 1, 1, 10, 0, 1))
        loner = Photo(path=str(tmp_path / "c.jpg"), sha256="c" * 64, taken_at=datetime(2024, 1, 1, 12))
        s.add_all([primary, member, loner])
        s.flush()
        mom = Moment(
            started_at=datetime(2024, 1, 1, 10),
            ended_at=datetime(2024, 1, 1, 10, 0, 1),
            size=2,
            primary_photo_id=primary.id,
        )
        s.add(mom)
        s.flush()
        s.add(MomentMember(moment_id=mom.id, photo_id=primary.id, rank=0))
        s.add(MomentMember(moment_id=mom.id, photo_id=member.id, rank=1))
        s.add(Swipe(photo_id=primary.id, decision="keep"))
        s.add(Swipe(photo_id=member.id, decision="reject"))
    return cfg


async def test_swipe_summary_collapse_moments_counts_primaries_only(tmp_path):
    cfg = await _seed_moment_library(tmp_path)
    app = build_app(cfg, run_background=False)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        body = (await client.get("/api/swipes/summary?collapse=moments")).json()
        assert body == {
            "total_photos": 2,
            "kept": 1,
            "rejected": 0,
            "undecided": 1,
        }
        assert body["kept"] + body["rejected"] + body["undecided"] == body["total_photos"]

        # The collapsed set matches what /api/photos returns with that collapse.
        listing = (await client.get("/api/photos?collapse=moments")).json()
        assert listing["total"] == body["total_photos"]


async def test_swipe_summary_collapse_none_counts_every_photo(tmp_path):
    cfg = await _seed_moment_library(tmp_path)
    app = build_app(cfg, run_background=False)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        body = (await client.get("/api/swipes/summary?collapse=none")).json()
        assert body == {
            "total_photos": 3,
            "kept": 1,
            "rejected": 1,
            "undecided": 1,
        }
        assert body["kept"] + body["rejected"] + body["undecided"] == body["total_photos"]


async def test_swipe_summary_quality_filter_matches_photos_total(tmp_path):
    """The tally must add up to the list total under the SAME query params."""
    from selects.db import session_scope
    from selects.db.models import ClassicalScore, Photo, Swipe

    cfg = get_folder_config(tmp_path)
    Session = init_db(cfg.db_path)
    with session_scope(Session) as s:
        # blur < 150 is the "out_of_focus" bucket: 2 of these 4 qualify.
        for i, blur in enumerate([50.0, 120.0, 300.0, 900.0]):
            p = Photo(path=str(tmp_path / f"{i}.jpg"), sha256=f"{i:064x}")
            s.add(p)
            s.flush()
            s.add(ClassicalScore(photo_id=p.id, blur=blur, luma_mean=0.5))
            if i == 0:
                s.add(Swipe(photo_id=p.id, decision="keep"))
            if i == 2:
                s.add(Swipe(photo_id=p.id, decision="reject"))

    app = build_app(cfg, run_background=False)
    transport = ASGITransport(app=app)
    params = "collapse=none&quality=out_of_focus"
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        body = (await client.get(f"/api/swipes/summary?{params}")).json()
        listing = (await client.get(f"/api/photos?{params}")).json()
        assert body["total_photos"] == listing["total"] == 2
        # Only the in-bucket keep counts; the out-of-bucket reject does not.
        assert body == {"total_photos": 2, "kept": 1, "rejected": 0, "undecided": 1}
        assert body["kept"] + body["rejected"] + body["undecided"] == listing["total"]


@pytest.mark.parametrize("path", ["/api/photos", "/api/swipes/summary"])
async def test_collapse_rejects_unknown_value(tmp_path, path):
    cfg = get_folder_config(tmp_path)
    init_db(cfg.db_path)
    app = build_app(cfg, run_background=False)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        assert (await client.get(f"{path}?collapse=bogus")).status_code == 422


async def test_swipe_summary_counts_silver_as_kept_and_skip_as_undecided(tmp_path):
    from selects.db import session_scope
    from selects.db.models import Photo, Swipe

    cfg = get_folder_config(tmp_path)
    Session = init_db(cfg.db_path)
    with session_scope(Session) as s:
        silver = Photo(path=str(tmp_path / "s.jpg"), sha256="a" * 64)
        skipped = Photo(path=str(tmp_path / "k.jpg"), sha256="b" * 64)
        s.add_all([silver, skipped])
        s.flush()
        s.add(Swipe(photo_id=silver.id, decision="silver"))
        s.add(Swipe(photo_id=skipped.id, decision="skip"))

    app = build_app(cfg, run_background=False)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        body = (await client.get("/api/swipes/summary")).json()
        assert body == {"total_photos": 2, "kept": 1, "rejected": 0, "undecided": 1}


async def test_delete_swipe_clears_the_verdict(tmp_path):
    from selects.db import session_scope
    from selects.db.models import Photo, Swipe

    cfg = get_folder_config(tmp_path)
    Session = init_db(cfg.db_path)
    sha = "a" * 64
    with session_scope(Session) as s:
        s.add(Photo(path=str(tmp_path / "a.jpg"), sha256=sha))

    app = build_app(cfg, run_background=False)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        assert (await client.post(f"/api/swipes/{sha}", json={"decision": "keep"})).status_code == 200

        first = await client.delete(f"/api/swipes/{sha}")
        assert first.status_code == 200
        assert first.json() == {"ok": True, "deleted": True}

        second = await client.delete(f"/api/swipes/{sha}")
        assert second.status_code == 200
        assert second.json() == {"ok": True, "deleted": False}

        unknown = await client.delete("/api/swipes/" + "f" * 64)
        assert unknown.status_code == 404
        assert (await client.delete("/api/swipes/zz")).status_code == 400

    with session_scope(Session) as s:
        photo = s.query(Photo).filter(Photo.sha256 == sha).one()
        assert s.get(Swipe, photo.id) is None


async def test_thumb_rejects_non_hex_sha(tmp_path):
    cfg = get_folder_config(tmp_path)
    init_db(cfg.db_path)
    app = build_app(cfg, run_background=False)

    async def _status(path: str) -> int:
        status: dict[str, int] = {}

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            if message["type"] == "http.response.start":
                status["code"] = message["status"]

        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": path,
            "raw_path": path.encode("ascii"),
            "query_string": b"",
            "headers": [(b"host", b"test")],
            "client": ("127.0.0.1", 123),
            "server": ("test", 80),
        }
        await app(scope, receive, send)
        return status["code"]

    # Literal traversal (httpx would collapse /api/thumb/../windows → /api/windows).
    assert await _status("/api/thumb/../windows") == 400
    assert await _status("/api/thumb/zz") == 400

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/api/thumb/zz")
        assert r.status_code == 400
        r = await client.get("/api/thumb/%2e%2e%2fwindows")
        assert r.status_code == 400


async def test_cluster_photos_empty_source_filters_null_tags(tmp_path):
    """Scenes (?source=) must match list_clusters: NULL-source tags only."""
    from selects.db import session_scope
    from selects.db.models import Photo, PhotoTag

    cfg = get_folder_config(tmp_path)
    Session = init_db(cfg.db_path)
    with session_scope(Session) as s:
        null_tagged = Photo(path=str(tmp_path / "null.jpg"), sha256="a" * 64)
        ram_tagged = Photo(path=str(tmp_path / "ram.jpg"), sha256="b" * 64)
        ram_other = Photo(path=str(tmp_path / "ram2.jpg"), sha256="c" * 64)
        s.add_all([null_tagged, ram_tagged, ram_other])
        s.flush()
        s.add(PhotoTag(photo_id=null_tagged.id, tag="mountain", score=0.9, source=None))
        s.add(PhotoTag(photo_id=ram_tagged.id, tag="mountain", score=0.9, source="ram"))
        s.add(PhotoTag(photo_id=ram_other.id, tag="tree", score=0.8, source="ram"))
        null_sha = null_tagged.sha256
        ram_sha = ram_tagged.sha256
        ram_other_sha = ram_other.sha256

    app = build_app(cfg, run_background=False)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/api/clusters/mountain/photos?source=")
        assert r.status_code == 200
        mountain_shas = {item["sha256"] for item in r.json()["items"]}
        assert null_sha in mountain_shas
        assert ram_sha not in mountain_shas

        r = await client.get("/api/clusters/uncategorized/photos?source=")
        assert r.status_code == 200
        uncat_shas = {item["sha256"] for item in r.json()["items"]}
        assert ram_sha in uncat_shas
        assert ram_other_sha in uncat_shas
        assert null_sha not in uncat_shas


async def test_record_swipe_requires_hex_sha_and_known_decision(tmp_path):
    from selects.db import session_scope
    from selects.db.models import Photo, Swipe

    cfg = get_folder_config(tmp_path)
    Session = init_db(cfg.db_path)
    sha = "a" * 64
    with session_scope(Session) as s:
        s.add(Photo(path=str(tmp_path / "a.jpg"), sha256=sha))

    app = build_app(cfg, run_background=False)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        bad_sha = await client.post("/api/swipes/zz", json={"decision": "keep"})
        assert bad_sha.status_code == 400
        short = await client.post("/api/swipes/" + "a" * 63, json={"decision": "keep"})
        assert short.status_code == 400
        bad_decision = await client.post(f"/api/swipes/{sha}", json={"decision": "maybe"})
        assert bad_decision.status_code == 400
        ok = await client.post(f"/api/swipes/{sha}", json={"decision": "keep"})
        assert ok.status_code == 200
        assert ok.json()["decision"] == "keep"

    with session_scope(Session) as s:
        photo = s.query(Photo).filter(Photo.sha256 == sha).one()
        swipe = s.get(Swipe, photo.id)
        assert swipe is not None
        assert swipe.decision == "keep"


async def test_edit_open_rejects_non_allowlisted_editor(tmp_path, monkeypatch):
    from unittest.mock import patch

    from selects.db import session_scope
    from selects.db.models import Photo

    cfg = get_folder_config(tmp_path)
    Session = init_db(cfg.db_path)
    sha = "b" * 64
    with session_scope(Session) as s:
        s.add(Photo(path=str(tmp_path / "a.jpg"), sha256=sha))

    app = build_app(cfg, run_background=False)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        with patch("shutil.which") as which:
            r = await client.post(
                "/api/edit/open",
                json={"sha256s": [sha], "editor": "notepad"},
            )
            which.assert_not_called()
        assert r.status_code == 400
        assert "darktable" in r.json()["detail"]

        r2 = await client.post(
            "/api/edit/open",
            json={"sha256s": [sha], "editor": "cmd.exe"},
        )
        assert r2.status_code == 400


def test_is_loopback_host():
    from selects.server.fs_routes import is_loopback_host

    assert is_loopback_host("127.0.0.1")
    assert is_loopback_host("127.0.0.2")
    assert is_loopback_host("::1")
    assert is_loopback_host("[::1]")
    assert is_loopback_host("localhost")
    assert is_loopback_host("testclient")
    assert not is_loopback_host("0.0.0.0")
    assert not is_loopback_host("::")
    assert not is_loopback_host("192.168.1.5")
    assert not is_loopback_host(None)


async def test_fs_list_ok_on_localhost(tmp_path):
    cfg = get_folder_config(tmp_path)
    init_db(cfg.db_path)
    app = build_app(cfg, run_background=False, bind_host="127.0.0.1")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/api/fs/list")
        assert r.status_code == 200
        body = r.json()
        assert "dirs" in body


async def test_fs_list_forbidden_when_bound_to_lan(tmp_path):
    cfg = get_folder_config(tmp_path)
    init_db(cfg.db_path)
    app = build_app(cfg, run_background=False, bind_host="0.0.0.0")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/api/fs/list")
        assert r.status_code == 403


async def test_lan_token_required_for_non_loopback_client(tmp_path, monkeypatch):
    monkeypatch.setenv("SELECTS_LAN_TOKEN", "secret-token")
    cfg = get_folder_config(tmp_path)
    init_db(cfg.db_path)
    app = build_app(cfg, run_background=False, bind_host="0.0.0.0")
    transport = ASGITransport(app=app, client=("192.168.1.50", 50000))
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/api/health")
        assert r.status_code == 200
        r = await client.get("/api/photos")
        assert r.status_code == 401
        r = await client.get("/api/photos", headers={"Authorization": "Bearer secret-token"})
        assert r.status_code == 200


async def test_fs_list_forbidden_for_lan_client(tmp_path):
    cfg = get_folder_config(tmp_path)
    init_db(cfg.db_path)
    app = build_app(cfg, run_background=False, bind_host="127.0.0.1")
    transport = ASGITransport(app=app, client=("192.168.1.50", 50000))
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/api/fs/list")
        assert r.status_code == 403


async def test_calibrate_extremes_uses_iqa_without_nima_ap25(tmp_path):
    from datetime import datetime

    from selects.db import session_scope
    from selects.db.models import Embedding, Photo, PhotoRating

    cfg = get_folder_config(tmp_path)
    Session = init_db(cfg.db_path)
    with session_scope(Session) as s:
        low = Photo(path=str(tmp_path / "low.jpg"), sha256="a" * 64, taken_at=datetime(2024, 1, 1))
        high = Photo(path=str(tmp_path / "high.jpg"), sha256="b" * 64, taken_at=datetime(2024, 1, 2))
        s.add_all([low, high])
        s.flush()
        s.add(Embedding(photo_id=low.id, siglip=b"\x00" * 2304, aesthetic_iqa=0.1))
        s.add(Embedding(photo_id=high.id, siglip=b"\x00" * 2304, aesthetic_iqa=0.9))
        low_id, high_id = low.id, high.id

    app = build_app(cfg, run_background=False)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/api/calibrate/extremes?bucket=worst&n=10")
        assert r.status_code == 200
        body = r.json()
        assert body["total_indexed"] == 2
        ids = [p["photo_id"] for p in body["photos"]]
        assert ids[0] == low_id
        assert high_id in ids
        assert body["photos"][0]["scores"]["iqa"] == pytest.approx(0.1)
        assert body["photos"][0]["scores"]["nima"] is None
        assert body["photos"][0]["scores"]["ap25"] is None

        dash = await client.get("/api/calibrate/dashboard")
        assert dash.status_code == 200
        assert len(dash.json()["photos"]) == 2

        await client.post("/api/calibrate/rate", json={"photo_id": high_id, "rating": 1})
        agr = await client.get("/api/calibrate/agreement")
        assert agr.status_code == 200
        agr_body = agr.json()
        assert agr_body["n_upvotes"] == 1
        assert agr_body["models"]["iqa"]["n_scored_upvotes"] == 1
        assert agr_body["models"]["iqa"]["median_upvote_percentile"] is not None

    with session_scope(Session) as s:
        assert s.get(PhotoRating, high_id) is not None


async def test_story_title_and_breadcrumb_drop_geocoder_suffix(tmp_path):
    """`Leh (2)` is a disambiguation artefact: the DB keeps it, the UI must not."""
    from datetime import datetime

    from selects.db import session_scope
    from selects.db.models import Photo, Story, StoryItem, Visit

    cfg = get_folder_config(tmp_path)
    Session = init_db(cfg.db_path)
    with session_scope(Session) as s:
        photo = Photo(path=str(tmp_path / "a.jpg"), sha256="a" * 64, taken_at=datetime(2024, 5, 1, 9))
        s.add(photo)
        s.flush()
        story = Story(
            day="2024-05-01",
            title="2024-05-01 · Exploring Leh (2) · 1 photos",
            photo_count=1,
        )
        s.add(story)
        s.flush()
        s.add(StoryItem(story_id=story.id, photo_id=photo.id, rank=0))
        s.add(Visit(
            story_id=story.id, rank=0, name="Leh (2)", lat=34.1, lon=77.5,
            elevation_m=3500,
            arrived_at=datetime(2024, 5, 1, 8), departed_at=datetime(2024, 5, 1, 18),
            photo_count=1,
        ))
        story_id = story.id

    app = build_app(cfg, run_background=False)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        body = (await client.get(f"/api/stories/{story_id}")).json()
        assert body["title"] == "2024-05-01 · Exploring Leh · 1 photos"
        assert body["itinerary_breadcrumb"] == "Leh (3,500m)"
        # The DB value survives so /best/place/<name> keeps resolving.
        assert body["visits"][0]["name"] == "Leh (2)"


# --------------------------------------------------------------------------- #
# quality is a closed enum, totals are DISTINCT, persons carry aesthetic_iqa
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "bucket", ["underexposed", "overexposed", "out_of_focus", "blurry_keepers"]
)
@pytest.mark.parametrize("path", ["/api/photos", "/api/swipes/summary"])
async def test_quality_accepts_only_the_four_buckets(tmp_path, path, bucket):
    cfg = get_folder_config(tmp_path)
    init_db(cfg.db_path)
    app = build_app(cfg, run_background=False)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        assert (await client.get(f"{path}?quality={bucket}")).status_code == 200
        # Anything else is a 422 rather than a silently-ignored filter.
        assert (await client.get(f"{path}?quality=nonsense")).status_code == 422
        assert (await client.get(f"{path}?quality=")).status_code == 422
        # Omitted entirely still means "no filter".
        assert (await client.get(path)).status_code == 200


async def test_photos_total_counts_a_photo_in_two_moments_once(tmp_path):
    """A photo that is the primary of two moments must not be counted twice.

    The collapse predicate outer-joins moment_members, so such a photo yields
    two rows; a plain COUNT(*) made /api/photos disagree with the summary tally
    the Review page shows next to it.
    """
    from datetime import datetime

    from selects.db import session_scope
    from selects.db.models import Moment, MomentMember, Photo

    cfg = get_folder_config(tmp_path)
    Session = init_db(cfg.db_path)
    with session_scope(Session) as s:
        shared = Photo(path=str(tmp_path / "a.jpg"), sha256="a" * 64,
                       taken_at=datetime(2024, 1, 1, 10))
        other = Photo(path=str(tmp_path / "b.jpg"), sha256="b" * 64,
                      taken_at=datetime(2024, 1, 1, 11))
        s.add_all([shared, other])
        s.flush()
        for i in range(2):
            mom = Moment(
                started_at=datetime(2024, 1, 1, 10),
                ended_at=datetime(2024, 1, 1, 10, 0, 1),
                size=1,
                primary_photo_id=shared.id,
            )
            s.add(mom)
            s.flush()
            s.add(MomentMember(moment_id=mom.id, photo_id=shared.id, rank=0))

    app = build_app(cfg, run_background=False)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        listing = (await client.get("/api/photos?collapse=moments")).json()
        summary = (await client.get("/api/swipes/summary?collapse=moments")).json()
        assert listing["total"] == 2
        assert summary["total_photos"] == listing["total"]
        assert [item["sha256"] for item in listing["items"]] == ["a" * 64, "b" * 64]

        # A page that contains the duplicated photo is still a *full* page:
        # deduping after offset/limit used to return one item for limit=1
        # against a promised total of 2, so the grid stalled short.
        first = (await client.get("/api/photos?collapse=moments&limit=1")).json()
        assert first["total"] == 2
        assert len(first["items"]) == 1
        assert first["items"][0]["sha256"] == "a" * 64

        second = (await client.get(
            "/api/photos?collapse=moments&limit=1&offset=1"
        )).json()
        assert len(second["items"]) == 1
        assert second["items"][0]["sha256"] == "b" * 64


async def test_person_photos_carry_aesthetic_iqa(tmp_path):
    import numpy as np

    from selects.db import session_scope
    from selects.db.models import (
        Embedding, FaceEmbedding, Person, Photo, PhotoPerson,
    )

    cfg = get_folder_config(tmp_path)
    Session = init_db(cfg.db_path)
    with session_scope(Session) as s:
        photo = Photo(path=str(tmp_path / "a.jpg"), sha256="a" * 64)
        person = Person(label="Ada", photo_count=1)
        s.add_all([photo, person])
        s.flush()
        s.add(Embedding(
            photo_id=photo.id,
            siglip=np.zeros(768, dtype=np.float16).tobytes(),
            aesthetic_iqa=0.75,
        ))
        fe = FaceEmbedding(
            photo_id=photo.id, face_index=0,
            embedding=np.zeros(512, dtype=np.float16).tobytes(),
            bbox_x=0, bbox_y=0, bbox_w=80, bbox_h=80, confidence=0.95,
        )
        s.add(fe)
        s.flush()
        s.add(PhotoPerson(
            photo_id=photo.id, person_id=person.id,
            face_embedding_id=fe.id, confidence=0.95,
        ))
        person_id = person.id

    app = build_app(cfg, run_background=False)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        body = (await client.get(f"/api/persons/{person_id}/photos")).json()
        assert body["total"] == 1
        assert body["items"][0]["aesthetic_iqa"] == pytest.approx(0.75)


async def test_likes_status_post_matches_get_and_handles_edge_cases(tmp_path):
    from selects.db import session_scope
    from selects.db.models import Photo, Swipe

    cfg = get_folder_config(tmp_path)
    Session = init_db(cfg.db_path)
    with session_scope(Session) as s:
        kept = Photo(path=str(tmp_path / "kept.jpg"), sha256="a" * 64)
        silver = Photo(path=str(tmp_path / "silver.jpg"), sha256="b" * 64)
        rejected = Photo(path=str(tmp_path / "rej.jpg"), sha256="c" * 64)
        s.add_all([kept, silver, rejected])
        s.flush()
        s.add(Swipe(photo_id=kept.id, decision="keep"))
        s.add(Swipe(photo_id=silver.id, decision="silver"))
        s.add(Swipe(photo_id=rejected.id, decision="reject"))

    shas = ["a" * 64, "b" * 64, "c" * 64]
    app = build_app(cfg, run_background=False)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        get_r = await client.get(f"/api/likes/status?shas={','.join(shas)}")
        assert get_r.status_code == 200

        post_r = await client.post("/api/likes/status", json={"shas": shas})
        assert post_r.status_code == 200
        assert post_r.json() == get_r.json()
        assert post_r.json() == {"a" * 64: True, "b" * 64: True, "c" * 64: False}

        empty_r = await client.post("/api/likes/status", json={"shas": []})
        assert empty_r.status_code == 200
        assert empty_r.json() == {}

        no_body_r = await client.post("/api/likes/status", json={})
        assert no_body_r.status_code == 200
        assert no_body_r.json() == {}

        bad_r = await client.post("/api/likes/status", json={"shas": "not-a-list"})
        assert bad_r.status_code == 422

        bad_entry_r = await client.post("/api/likes/status", json={"shas": [1, 2, 3]})
        assert bad_entry_r.status_code == 422


async def test_status_request_rejects_malformed_and_oversized_sha_lists(tmp_path):
    """``StatusRequest.shas`` is validated at the edge: every entry must be a
    64-char hex digest, and the list is capped far above any real library."""
    cfg = get_folder_config(tmp_path)
    init_db(cfg.db_path)
    app = build_app(cfg, run_background=False)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        for path in ("/api/likes/status", "/api/edits/status"):
            short = await client.post(path, json={"shas": ["a" * 63]})
            assert short.status_code == 422, path

            non_hex = await client.post(path, json={"shas": ["z" * 64]})
            assert non_hex.status_code == 422, path

            traversal = await client.post(path, json={"shas": ["../" + "a" * 61]})
            assert traversal.status_code == 422, path

            # Upper case is a valid digest.
            upper = await client.post(path, json={"shas": ["A" * 64]})
            assert upper.status_code == 200, path

        # The length cap is on the shared model; check it once (the body is
        # ~6 MB, so building it twice buys nothing).
        too_many = await client.post(
            "/api/likes/status", json={"shas": [f"{i:064x}" for i in range(100_001)]}
        )
        assert too_many.status_code == 422


async def test_edits_status_post_matches_get_and_handles_edge_cases(tmp_path):
    from selects.db import session_scope
    from selects.db.models import Photo

    cfg = get_folder_config(tmp_path)
    Session = init_db(cfg.db_path)
    with session_scope(Session) as s:
        s.add(Photo(path=str(tmp_path / "a.jpg"), sha256="a" * 64))
        s.add(Photo(path=str(tmp_path / "b.jpg"), sha256="b" * 64))

    shas = ["a" * 64, "b" * 64]
    app = build_app(cfg, run_background=False)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        get_r = await client.get(f"/api/edits/status?shas={','.join(shas)}")
        assert get_r.status_code == 200

        post_r = await client.post("/api/edits/status", json={"shas": shas})
        assert post_r.status_code == 200
        assert post_r.json() == get_r.json()
        assert set(post_r.json().keys()) == set(shas)
        assert all(v["edited"] is False for v in post_r.json().values())

        empty_r = await client.post("/api/edits/status", json={"shas": []})
        assert empty_r.status_code == 200
        assert empty_r.json() == {}

        bad_r = await client.post("/api/edits/status", json={"shas": "not-a-list"})
        assert bad_r.status_code == 422

        bad_entry_r = await client.post("/api/edits/status", json={"shas": [1, 2, 3]})
        assert bad_entry_r.status_code == 422
