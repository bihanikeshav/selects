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
