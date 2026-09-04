"""LAN token auth (cookie + query param), websocket auth, shutdown cancel."""
from __future__ import annotations

import threading
import time

import pytest
from starlette.testclient import TestClient

from selects.config import get_folder_config
from selects.server.app import build_app
from selects.server.library_manager import LibraryManager

TOKEN = "s3cret-token"
LAN_CLIENT = ("10.0.0.5", 1234)


@pytest.fixture()
def lan_app(tmp_path, monkeypatch):
    monkeypatch.setenv("SELECTS_LAN_TOKEN", TOKEN)
    manager = LibraryManager(registry_path=tmp_path / "libraries.json")
    return build_app(manager=manager, run_background=False, bind_host="0.0.0.0")


def test_lan_request_without_token_is_401(lan_app):
    with TestClient(lan_app, client=LAN_CLIENT) as c:
        assert c.get("/api/libraries").status_code == 401


def test_health_is_always_reachable(lan_app):
    with TestClient(lan_app, client=LAN_CLIENT) as c:
        assert c.get("/api/health").status_code == 200


def test_query_token_sets_cookie_and_cookie_then_authenticates(lan_app):
    with TestClient(lan_app, client=LAN_CLIENT) as c:
        c.cookies.clear()
        r = c.get(f"/api/libraries?token={TOKEN}")
        assert r.status_code == 200
        set_cookie = r.headers["set-cookie"]
        assert f"selects_token={TOKEN}" in set_cookie
        assert "HttpOnly" in set_cookie
        assert "SameSite=Lax" in set_cookie
        assert "Path=/" in set_cookie

        # The client kept the cookie; a plain request now passes.
        assert c.cookies.get("selects_token") == TOKEN
        assert c.get("/api/libraries").status_code == 200


def test_health_with_query_token_sets_cookie(lan_app):
    with TestClient(lan_app, client=LAN_CLIENT) as c:
        c.cookies.clear()
        r = c.get(f"/api/health?token={TOKEN}")
        assert r.status_code == 200
        assert f"selects_token={TOKEN}" in r.headers["set-cookie"]


def test_loopback_client_needs_no_token(lan_app):
    with TestClient(lan_app, client=("127.0.0.1", 1234)) as c:
        assert c.get("/api/libraries").status_code == 200


def test_no_token_env_means_no_gate(tmp_path, monkeypatch):
    monkeypatch.delenv("SELECTS_LAN_TOKEN", raising=False)
    manager = LibraryManager(registry_path=tmp_path / "libraries.json")
    app = build_app(manager=manager, run_background=False, bind_host="0.0.0.0")
    with TestClient(app, client=LAN_CLIENT) as c:
        assert c.get("/api/libraries").status_code == 200
        with c.websocket_connect("/ws/progress"):
            pass


def test_ws_rejects_lan_client_without_token(lan_app):
    from starlette.websockets import WebSocketDisconnect

    with TestClient(lan_app, client=LAN_CLIENT) as c:
        with pytest.raises(WebSocketDisconnect) as exc:
            with c.websocket_connect("/ws/progress"):
                pass
        assert exc.value.code == 4401


def test_ws_accepts_with_query_token(lan_app):
    with TestClient(lan_app, client=LAN_CLIENT) as c:
        with c.websocket_connect(f"/ws/progress?token={TOKEN}"):
            pass


def test_ws_accepts_with_cookie(lan_app):
    with TestClient(lan_app, client=LAN_CLIENT) as c:
        c.cookies.set("selects_token", TOKEN)
        with c.websocket_connect("/ws/progress"):
            pass


def test_ws_accepts_loopback_without_token(lan_app):
    with TestClient(lan_app, client=("127.0.0.1", 1234)) as c:
        with c.websocket_connect("/ws/progress"):
            pass


def test_shutdown_cancels_running_index(tmp_path, monkeypatch):
    monkeypatch.delenv("SELECTS_LAN_TOKEN", raising=False)
    cfg = get_folder_config(tmp_path / "lib")
    (tmp_path / "lib").mkdir(exist_ok=True)

    started = threading.Event()
    finished = threading.Event()

    def fake_stages(cfg_, publish, should_cancel=None):
        started.set()
        deadline = time.monotonic() + 20.0
        while not (should_cancel and should_cancel()):
            if time.monotonic() > deadline:  # safety net so a failure can't hang
                return
            time.sleep(0.01)
        finished.set()

    def fake_warmup(cfg_, embeddings=False):
        return None

    monkeypatch.setattr("selects.server.app.run_pipeline_stages", fake_stages)
    monkeypatch.setattr("selects.ml.warmup.warmup_search", fake_warmup)

    manager = LibraryManager(bootstrap_cfg=cfg, registry_path=tmp_path / "libraries.json")
    app = build_app(manager=manager, run_background=True)
    with TestClient(app) as c:
        assert c.get("/api/health").status_code == 200
        assert started.wait(5.0), "background index never started"

    assert finished.wait(5.0), "index worker did not stop after shutdown"
