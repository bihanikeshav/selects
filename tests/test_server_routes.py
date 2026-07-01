import shutil
from pathlib import Path

import pytest
from httpx import AsyncClient, ASGITransport

from travelcull.config import get_folder_config
from travelcull.db import init_db
from travelcull.indexer.orchestrator import index_folder
from travelcull.pipeline import run_classical_stage
from travelcull.server.app import build_app

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
