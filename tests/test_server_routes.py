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


async def test_doctor_blurry_keepers_uses_iqa_percentile(tmp_path):
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
        r = await client.get("/api/doctor/issues")
        assert r.status_code == 200
        body = r.json()
        keepers = body["blurry_keepers"]
        # Library 50th percentile of [0.2, 0.4, 0.6, 0.8] is 0.5.
        keeper_iqas = sorted(k["combined"] for k in keepers)
        assert len(keeper_iqas) == 2
        assert keeper_iqas[0] == pytest.approx(0.6)
        assert keeper_iqas[1] == pytest.approx(0.8)
        assert body["counts"]["blurry_keepers"] == 2


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
