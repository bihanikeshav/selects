"""Tests for travelcull.ml.model_assets and the /api/models/* routes.

Never hits the network: HF/insightface presence checks are monkeypatched and
the url downloader is exercised against a fake ``requests.get``.
"""
from __future__ import annotations

import hashlib
import threading

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from travelcull.ml import model_assets
from travelcull.server.models_routes import register_model_routes


# --------------------------------------------------------------------------- #
# Fake HTTP plumbing (no network)                                             #
# --------------------------------------------------------------------------- #

class _FakeResp:
    def __init__(self, data: bytes):
        self._data = data

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def raise_for_status(self):
        return None

    def iter_content(self, chunk_size=1 << 20):
        for i in range(0, len(self._data), chunk_size):
            yield self._data[i : i + chunk_size]


def _serve(monkeypatch, data: bytes):
    """Monkeypatch requests.get (inside model_assets) to serve *data*."""
    import requests

    def fake_get(url, stream=False, timeout=None):
        return _FakeResp(data)

    monkeypatch.setattr(requests, "get", fake_get)


# --------------------------------------------------------------------------- #
# Manifest                                                                     #
# --------------------------------------------------------------------------- #

def test_manifest_well_formed():
    ids = set()
    for a in model_assets.MANIFEST:
        for key in ("id", "name", "kind", "ref", "approx_size_mb", "required_for", "sha256"):
            assert key in a, f"asset {a.get('id')} missing {key}"
        assert a["kind"] in {"hf", "url", "insightface"}
        assert isinstance(a["approx_size_mb"], int) and a["approx_size_mb"] >= 0
        assert isinstance(a["required_for"], str) and a["required_for"]
        assert a["id"] not in ids, f"duplicate id {a['id']}"
        ids.add(a["id"])
        if a["kind"] == "url":
            assert "filename" in a and a["filename"]
        else:
            # sha256 is only meaningful for url assets
            assert a["sha256"] is None

    # The real model ids derived from the ml modules must be present.
    refs = {a["ref"] for a in model_assets.MANIFEST}
    assert "google/siglip-so400m-patch14-384" in refs
    assert "Qwen/Qwen3-VL-2B-Instruct" in refs
    assert "xinyu1205/recognize-anything-plus-model" in refs


# --------------------------------------------------------------------------- #
# Hardened downloader                                                          #
# --------------------------------------------------------------------------- #

def test_download_atomic_success(tmp_path, monkeypatch):
    data = b"weight-bytes" * 1000
    _serve(monkeypatch, data)
    target = tmp_path / "sub" / "model.pth"

    out = model_assets.download_file("http://example/model.pth", target)

    assert out == target
    assert target.read_bytes() == data
    # No leftover temp file, and the parent dir was created.
    assert not (tmp_path / "sub" / "model.pth.part").exists()


def test_download_sha256_match(tmp_path, monkeypatch):
    data = b"abc123" * 10
    _serve(monkeypatch, data)
    digest = hashlib.sha256(data).hexdigest()
    target = tmp_path / "model.pth"

    model_assets.download_file("http://example/model.pth", target, sha256=digest)
    assert target.read_bytes() == data


def test_download_sha256_mismatch(tmp_path, monkeypatch):
    data = b"real-bytes"
    _serve(monkeypatch, data)
    target = tmp_path / "model.pth"

    with pytest.raises(ValueError, match="sha256 mismatch"):
        model_assets.download_file(
            "http://example/model.pth", target, sha256="deadbeef" * 8
        )

    # Nothing written to the real path, no leftover temp file.
    assert not target.exists()
    assert not (tmp_path / "model.pth.part").exists()


def test_download_timeout_propagates(tmp_path, monkeypatch):
    import requests

    def fake_get(url, stream=False, timeout=None):
        raise requests.exceptions.Timeout("connect timed out")

    monkeypatch.setattr(requests, "get", fake_get)
    target = tmp_path / "model.pth"

    with pytest.raises(requests.exceptions.Timeout):
        model_assets.download_file("http://example/model.pth", target)

    assert not target.exists()
    assert not (tmp_path / "model.pth.part").exists()


# --------------------------------------------------------------------------- #
# Presence / status                                                           #
# --------------------------------------------------------------------------- #

def test_asset_present_url(tmp_path):
    asset = {
        "id": "fake",
        "name": "Fake",
        "kind": "url",
        "ref": "http://example/fake.pth",
        "filename": "fake.pth",
        "approx_size_mb": 1,
        "required_for": "testing",
        "sha256": None,
    }
    assert model_assets.asset_present(asset, base_models_dir=tmp_path) is False

    (tmp_path / "fake.pth").write_bytes(b"x" * 100)
    assert model_assets.asset_present(asset, base_models_dir=tmp_path) is True


def test_asset_present_url_sha256(tmp_path):
    data = b"hello world"
    (tmp_path / "fake.pth").write_bytes(data)
    good = {
        "id": "fake", "name": "Fake", "kind": "url", "ref": "http://x/fake.pth",
        "filename": "fake.pth", "approx_size_mb": 1, "required_for": "t",
        "sha256": hashlib.sha256(data).hexdigest(),
    }
    assert model_assets.asset_present(good, base_models_dir=tmp_path) is True

    bad = dict(good, sha256="00" * 32)
    assert model_assets.asset_present(bad, base_models_dir=tmp_path) is False


def test_status_shape(tmp_path, monkeypatch):
    # Force every asset "missing": no hf cache, no insightface, empty url dir.
    monkeypatch.setattr(model_assets, "_hf_repo_cached", lambda repo_id: False)
    monkeypatch.setattr(model_assets, "insightface_dir", lambda: tmp_path / "nope")

    st = model_assets.status(base_models_dir=tmp_path)
    assert set(st) == {"models", "total_missing_mb"}
    assert len(st["models"]) == len(model_assets.MANIFEST)
    for m in st["models"]:
        assert set(m) == {"id", "name", "present", "approx_size_mb", "required_for"}
        assert m["present"] is False
    expected = sum(int(a["approx_size_mb"]) for a in model_assets.MANIFEST)
    assert st["total_missing_mb"] == expected


def test_download_all_only_missing(monkeypatch):
    # First asset present, rest missing -> download_all touches only the rest.
    present_id = model_assets.MANIFEST[0]["id"]
    monkeypatch.setattr(
        model_assets, "asset_present",
        lambda a, base_models_dir=None: a["id"] == present_id,
    )
    downloaded: list[str] = []
    monkeypatch.setattr(
        model_assets, "_download_asset",
        lambda asset, base: downloaded.append(asset["id"]),
    )
    msgs: list[dict] = []

    total = model_assets.download_all(publish=msgs.append)

    assert total == len(model_assets.MANIFEST) - 1
    assert present_id not in downloaded
    assert len(downloaded) == total
    # Progress is published per asset in the models stage.
    assert all(m["stage"] == "models" for m in msgs)
    assert msgs[-1]["current"] == total and msgs[-1]["total"] == total


# --------------------------------------------------------------------------- #
# Routes                                                                       #
# --------------------------------------------------------------------------- #

def _app_with_routes(publish):
    app = FastAPI()
    register_model_routes(app, publish)
    return app


def test_route_status(monkeypatch):
    monkeypatch.setattr(
        model_assets, "status",
        lambda: {"models": [{"id": "siglip"}], "total_missing_mb": 42},
    )
    client = TestClient(_app_with_routes(lambda msg: None))

    r = client.get("/api/models/status")
    assert r.status_code == 200
    body = r.json()
    assert body["total_missing_mb"] == 42
    assert body["downloading"] is False
    assert body["models"] == [{"id": "siglip"}]


def test_route_download_and_conflict(monkeypatch):
    msgs: list[dict] = []
    gate = threading.Event()
    started = threading.Event()

    def fake_download_all(publish, only_missing=True, base_models_dir=None):
        started.set()
        gate.wait(timeout=5)
        return 3

    monkeypatch.setattr(model_assets, "download_all", fake_download_all)
    client = TestClient(_app_with_routes(msgs.append))

    # First request starts the worker and returns immediately.
    r1 = client.post("/api/models/download")
    assert r1.status_code == 200
    assert r1.json() == {"started": True}

    assert started.wait(timeout=5)
    # While in-flight, a second request is a 409.
    r2 = client.post("/api/models/download")
    assert r2.status_code == 409

    # Let the worker finish and confirm it publishes a final "done".
    gate.set()
    for _ in range(500):
        if msgs and msgs[-1].get("message") == "done":
            break
        threading.Event().wait(0.01)
    assert msgs[-1] == {"stage": "models", "current": 3, "total": 3, "message": "done"}

    # Guard cleared -> a fresh download is accepted again.
    gate2 = threading.Event()
    monkeypatch.setattr(
        model_assets, "download_all",
        lambda publish, only_missing=True, base_models_dir=None: 0,
    )
    r3 = client.post("/api/models/download")
    assert r3.status_code == 200
