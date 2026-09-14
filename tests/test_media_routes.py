from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from selects.config import get_folder_config
from selects.db import init_db, session_scope
from selects.db.models import Video
from selects.server.media_routes import register_media_routes


def _client(tmp_path: Path) -> tuple[TestClient, str]:
    source = tmp_path / "clip.mp4"
    source.write_bytes(bytes(range(100)))
    sha = "a" * 64
    cfg = get_folder_config(tmp_path)
    Session = init_db(cfg.db_path)
    with session_scope(Session) as session:
        session.add(Video(
            path=str(source),
            sha256=sha,
            size_bytes=source.stat().st_size,
            mtime=source.stat().st_mtime,
            format="mp4",
            duration_sec=10.0,
            frames_json="[]",
            highlights_json="[]",
        ))
    app = FastAPI()
    register_media_routes(app, cfg)
    return TestClient(app), sha


def test_stream_supports_single_byte_range(tmp_path: Path):
    client, sha = _client(tmp_path)
    response = client.get(
        f"/api/videos/{sha}/stream",
        headers={"Range": "bytes=10-19"},
    )
    assert response.status_code == 206
    assert response.content == bytes(range(10, 20))
    assert response.headers["content-range"] == "bytes 10-19/100"
    assert response.headers["accept-ranges"] == "bytes"


def test_stream_rejects_invalid_range(tmp_path: Path):
    client, sha = _client(tmp_path)
    response = client.get(
        f"/api/videos/{sha}/stream",
        headers={"Range": "bytes=500-600"},
    )
    assert response.status_code == 416
    assert response.headers["content-range"] == "bytes */100"


def test_edit_rating_tags_and_collections_round_trip(tmp_path: Path):
    client, sha = _client(tmp_path)
    recipe = {
        "schema_version": 1,
        "segments": [{"start_sec": 1.0, "end_sec": 8.5}],
        "mute_audio": False,
        "gain_db": 0,
        "stabilize": {"enabled": False, "strength": "medium", "crop": 0.08},
        "export": {"quality": "balanced", "container": "mp4"},
        "source_sha256": sha,
    }

    saved = client.put(f"/api/videos/{sha}/edit", json={"recipe": recipe})
    assert saved.status_code == 200
    assert saved.json()["recipe"]["segments"][0]["start_sec"] == 1.0
    assert client.get(f"/api/videos/{sha}/edit").json()["recipe"]["source_sha256"] == sha

    assert client.put(f"/api/videos/{sha}/rating", json={"rating": 1}).json()["rating"] == 1
    tags = client.put(
        f"/api/videos/{sha}/tags",
        json={"tags": ["mountains", "road trip", "mountains"]},
    ).json()
    assert tags["tags"] == ["mountains", "road trip"]
    assert client.get(f"/api/videos/{sha}/tags").json()["tags"] == tags["tags"]

    collection = client.post(
        "/api/video-collections",
        json={"name": "Leh selects"},
    ).json()
    assert client.post(
        f"/api/video-collections/{collection['id']}/videos/{sha}"
    ).status_code == 200
    listed = client.get("/api/video-collections").json()["collections"]
    assert listed == [{
        "id": collection["id"],
        "name": "Leh selects",
        "description": None,
        "count": 1,
    }]


def test_timeline_uses_existing_analysis_when_deep_index_is_absent(tmp_path: Path):
    client, sha = _client(tmp_path)
    body = client.get(f"/api/videos/{sha}/timeline").json()
    assert body["sha256"] == sha
    assert body["duration_sec"] == 10.0
    assert body["frames"] == []
    assert body["keyframes"] == []
    assert body["audio"] is None


def test_edit_cannot_reference_another_source(tmp_path: Path):
    client, sha = _client(tmp_path)
    response = client.put(
        f"/api/videos/{sha}/edit",
        json={"recipe": {"source_sha256": "b" * 64}},
    )
    assert response.status_code == 409
