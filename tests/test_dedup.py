"""Tests for selects.dedup: cross-library duplicate grouping."""
from __future__ import annotations

import numpy as np

from selects.config import get_folder_config
from selects.db import init_db, session_scope
from selects.db.models import Embedding, Photo
from selects.dedup import scan_all_libraries


def _make_library(tmp_path, name, photos):
    """Create a library folder with its own DB populated from *photos*.

    Each entry in *photos* is a dict with keys: path, sha256, size_bytes, and
    optionally siglip (np.ndarray) / aesthetic_iqa.
    """
    folder = tmp_path / name
    folder.mkdir()
    cfg = get_folder_config(folder)
    Session = init_db(cfg.db_path)
    with session_scope(Session) as s:
        for p in photos:
            photo = Photo(path=p["path"], sha256=p.get("sha256"), size_bytes=p.get("size_bytes"))
            s.add(photo)
            s.flush()
            if "siglip" in p or "aesthetic_iqa" in p:
                siglip = p.get("siglip")
                blob = (
                    siglip.astype(np.float16).tobytes()
                    if siglip is not None
                    else np.zeros(8, dtype=np.float16).tobytes()
                )
                s.add(Embedding(photo_id=photo.id, siglip=blob, aesthetic_iqa=p.get("aesthetic_iqa")))
    return {"id": name, "name": name, "path": str(folder)}


def test_exact_duplicate_across_libraries(tmp_path):
    lib_a = _make_library(
        tmp_path, "a", [{"path": "/a/1.jpg", "sha256": "same", "size_bytes": 100, "aesthetic_iqa": 0.5}]
    )
    lib_b = _make_library(
        tmp_path, "b", [{"path": "/b/1.jpg", "sha256": "same", "size_bytes": 200, "aesthetic_iqa": 0.9}]
    )

    report = scan_all_libraries([lib_a, lib_b])

    assert report["libraries_scanned"] == 2
    assert report["photos_scanned"] == 2
    assert report["exact_group_count"] == 1
    assert report["near_group_count"] == 0

    (group,) = report["groups"]
    assert group["kind"] == "exact"
    assert group["key"] == "same"
    assert len(group["members"]) == 2

    keeper = group["members"][group["keeper_index"]]
    assert keeper["library_id"] == "b"  # higher aesthetic_iqa wins
    assert group["reclaimable_bytes"] == 100  # size of the non-kept copy


def test_keeper_falls_back_to_largest_file_without_aesthetic_score(tmp_path):
    lib_a = _make_library(tmp_path, "a", [{"path": "/a/1.jpg", "sha256": "s", "size_bytes": 100}])
    lib_b = _make_library(tmp_path, "b", [{"path": "/b/1.jpg", "sha256": "s", "size_bytes": 300}])

    report = scan_all_libraries([lib_a, lib_b])

    (group,) = report["groups"]
    keeper = group["members"][group["keeper_index"]]
    assert keeper["library_id"] == "b"
    assert group["reclaimable_bytes"] == 100


def test_near_duplicate_within_single_library(tmp_path):
    v1 = np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32)
    v2 = np.array([0.999, 0.001, 0.0, 0.0], dtype=np.float32)  # near-identical to v1
    v3 = np.array([0.0, 1.0, 0.0, 0.0], dtype=np.float32)  # orthogonal -> not a dup

    lib = _make_library(
        tmp_path,
        "lib",
        [
            {"path": "/lib/1.jpg", "sha256": "s1", "size_bytes": 100, "siglip": v1, "aesthetic_iqa": 0.4},
            {"path": "/lib/2.jpg", "sha256": "s2", "size_bytes": 150, "siglip": v2, "aesthetic_iqa": 0.6},
            {"path": "/lib/3.jpg", "sha256": "s3", "size_bytes": 120, "siglip": v3, "aesthetic_iqa": 0.9},
        ],
    )

    report = scan_all_libraries([lib])

    assert report["exact_group_count"] == 0
    assert report["near_group_count"] == 1
    (group,) = [g for g in report["groups"] if g["kind"] == "near"]
    shas = {m["sha256"] for m in group["members"]}
    assert shas == {"s1", "s2"}
    keeper = group["members"][group["keeper_index"]]
    assert keeper["sha256"] == "s2"  # higher aesthetic_iqa among the near-dup pair


def test_no_duplicates(tmp_path):
    lib = _make_library(
        tmp_path,
        "lib",
        [
            {"path": "/lib/1.jpg", "sha256": "s1", "size_bytes": 100},
            {"path": "/lib/2.jpg", "sha256": "s2", "size_bytes": 150},
        ],
    )

    report = scan_all_libraries([lib])

    assert report["groups"] == []
    assert report["total_reclaimable_bytes"] == 0


def test_missing_library_is_skipped_not_fatal(tmp_path):
    missing = {"id": "x", "name": "x", "path": str(tmp_path / "does-not-exist")}
    lib = _make_library(tmp_path, "lib", [{"path": "/lib/1.jpg", "sha256": "s1", "size_bytes": 100}])

    report = scan_all_libraries([missing, lib])

    assert report["libraries_scanned"] == 2
    assert report["photos_scanned"] == 1
    assert report["groups"] == []


def test_thumb_url_only_for_active_library(tmp_path):
    lib_a = _make_library(tmp_path, "a", [{"path": "/a/1.jpg", "sha256": "same", "size_bytes": 100}])
    lib_b = _make_library(tmp_path, "b", [{"path": "/b/1.jpg", "sha256": "same", "size_bytes": 100}])

    report = scan_all_libraries([lib_a, lib_b], active_library_id="a")

    (group,) = report["groups"]
    by_lib = {m["library_id"]: m for m in group["members"]}
    assert by_lib["a"]["thumb_url"] == "/api/thumb/same"
    assert by_lib["b"]["thumb_url"] is None


# ===== route-layer test (not wired into app.py; built ad hoc here) =========

import time

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from selects.server.dedup_routes import register_dedup_routes
from selects.server.library_manager import LibraryManager


async def test_dedup_report_route_polls_to_completion(tmp_path):
    a = tmp_path / "lib_a"
    a.mkdir()
    b = tmp_path / "lib_b"
    b.mkdir()

    manager = LibraryManager(registry_path=tmp_path / "libraries.json")
    manager.add_library("A", str(a))
    manager.add_library("B", str(b))

    app = FastAPI()
    register_dedup_routes(app, manager)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        deadline = time.time() + 10
        body = None
        while time.time() < deadline:
            r = await c.get("/api/dedup/report")
            assert r.status_code == 200
            body = r.json()
            if not body["running"] and body["result"] is not None:
                break
        assert body is not None
        assert body["error"] is None
        assert body["result"]["libraries_scanned"] == 2
