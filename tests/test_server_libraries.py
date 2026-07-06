from pathlib import Path

import pytest
from httpx import AsyncClient, ASGITransport

from selects.server.app import build_app
from selects.server.library_manager import LibraryManager


@pytest.fixture
def registry_path(tmp_path):
    return tmp_path / "libraries.json"


def _make_app(registry_path):
    manager = LibraryManager(registry_path=registry_path)
    app = build_app(manager=manager, run_background=False)
    return app


def _client(app):
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_status_fresh_registry(registry_path):
    app = _make_app(registry_path)
    async with _client(app) as c:
        r = await c.get("/api/libraries/status")
        assert r.status_code == 200
        body = r.json()
        assert body["needs_onboarding"] is True
        assert body["active"] is None
        assert body["photo_count"] == 0
        assert body["indexing"] is False

        r2 = await c.get("/api/libraries")
        assert r2.json() == {"libraries": [], "active_id": None}


async def test_add_library(registry_path, tmp_path):
    lib_dir = tmp_path / "trip1"
    lib_dir.mkdir()
    app = _make_app(registry_path)
    async with _client(app) as c:
        r = await c.post("/api/libraries", json={"name": "Trip 1", "path": str(lib_dir)})
        assert r.status_code == 200
        lib = r.json()["library"]
        assert lib["name"] == "Trip 1"
        assert lib["active"] is True
        assert Path(lib["path"]) == lib_dir.resolve()
        assert "created_at" in lib

        listing = (await c.get("/api/libraries")).json()
        assert len(listing["libraries"]) == 1
        assert listing["active_id"] == lib["id"]


async def test_add_bad_path_400(registry_path, tmp_path):
    app = _make_app(registry_path)
    async with _client(app) as c:
        missing = tmp_path / "does-not-exist"
        r = await c.post("/api/libraries", json={"name": "X", "path": str(missing)})
        assert r.status_code == 400


async def test_add_duplicate_opens_existing(registry_path, tmp_path):
    lib_dir = tmp_path / "trip"
    lib_dir.mkdir()
    app = _make_app(registry_path)
    async with _client(app) as c:
        r1 = await c.post("/api/libraries", json={"name": "A", "path": str(lib_dir)})
        assert r1.status_code == 200
        id1 = r1.json()["library"]["id"]
        # Re-adding an already-registered path opens the existing library
        # instead of erroring, so the UI can proceed straight in.
        r2 = await c.post("/api/libraries", json={"name": "B", "path": str(lib_dir)})
        assert r2.status_code == 200
        body = r2.json()
        assert body.get("already_registered") is True
        assert body["library"]["id"] == id1


async def test_activate(registry_path, tmp_path):
    a = tmp_path / "a"
    a.mkdir()
    b = tmp_path / "b"
    b.mkdir()
    app = _make_app(registry_path)
    async with _client(app) as c:
        id_a = (await c.post("/api/libraries", json={"name": "A", "path": str(a)})).json()["library"]["id"]
        id_b = (await c.post("/api/libraries", json={"name": "B", "path": str(b)})).json()["library"]["id"]

        # A is active (first added); activate B and verify the switch.
        r = await c.post(f"/api/libraries/{id_b}/activate")
        assert r.status_code == 200
        assert r.json()["ok"] is True
        assert r.json()["library"]["id"] == id_b
        assert r.json()["library"]["active"] is True

        listing = (await c.get("/api/libraries")).json()
        assert listing["active_id"] == id_b

        # Unknown id -> 404
        assert (await c.post("/api/libraries/nope/activate")).status_code == 404


async def test_delete_active_400_then_delete(registry_path, tmp_path):
    a = tmp_path / "a"
    a.mkdir()
    b = tmp_path / "b"
    b.mkdir()
    app = _make_app(registry_path)
    async with _client(app) as c:
        id_a = (await c.post("/api/libraries", json={"name": "A", "path": str(a)})).json()["library"]["id"]
        id_b = (await c.post("/api/libraries", json={"name": "B", "path": str(b)})).json()["library"]["id"]

        # A is active and B exists -> deleting A must 400.
        assert (await c.delete(f"/api/libraries/{id_a}")).status_code == 400

        # Deleting the non-active B is fine.
        assert (await c.delete(f"/api/libraries/{id_b}")).status_code == 200

        # A is now the only (active) library -> deletion allowed, active -> null.
        assert (await c.delete(f"/api/libraries/{id_a}")).status_code == 200

        listing = (await c.get("/api/libraries")).json()
        assert listing["libraries"] == []
        assert listing["active_id"] is None
