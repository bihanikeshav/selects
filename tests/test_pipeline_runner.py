"""Tests for the shared default pipeline (mocked ML — no model downloads)."""
from __future__ import annotations

from selects.config import get_folder_config
from selects.db import init_db
from selects.server.pipeline_runner import (
    DEFAULT_STAGE_ORDER,
    FAST_SKIP_STAGES,
    PipelineCancelled,
    run_pipeline_stages,
)


def test_default_stage_order():
    assert DEFAULT_STAGE_ORDER == (
        "index",
        "video",
        "classical",
        "embed",
        "tag",
        "ram_tag",
        "smart_tag",
        "face_embed",
        "persons",
        "moment",
        "story",
        "thematic",
        "date",
    )
    assert DEFAULT_STAGE_ORDER.index("thematic") > DEFAULT_STAGE_ORDER.index("story")
    assert DEFAULT_STAGE_ORDER.index("persons") > DEFAULT_STAGE_ORDER.index("face_embed")


def _run_with_mocked_stages(tmp_path, monkeypatch, speed_mode: str = "full"):
    cfg = get_folder_config(tmp_path, speed_mode=speed_mode)
    init_db(cfg.db_path)
    called: list[str] = []

    def fake_get(name: str):
        def fn(cfg, on_progress=None, paths=None, **_kwargs):
            called.append(name)
            if on_progress:
                on_progress(1, 1, name)
            return 1

        return fn

    monkeypatch.setattr("selects.server.pipeline_runner.get_stage_callable", fake_get)
    published: list[dict] = []
    run_pipeline_stages(cfg, published.append)
    return called, published


def test_default_stage_order_runs_all_mocked(tmp_path, monkeypatch):
    called, published = _run_with_mocked_stages(tmp_path, monkeypatch, speed_mode="full")
    assert called == list(DEFAULT_STAGE_ORDER)
    assert published[-1]["stage"] == "done"


def test_speed_mode_fast_skips_ram_face_smart(tmp_path, monkeypatch):
    called, _published = _run_with_mocked_stages(tmp_path, monkeypatch, speed_mode="fast")
    for skipped in ("ram_tag", "smart_tag", "face_embed", "persons"):
        assert skipped not in called
    assert FAST_SKIP_STAGES == {"ram_tag", "smart_tag", "face_embed", "persons"}
    assert called == [s for s in DEFAULT_STAGE_ORDER if s not in FAST_SKIP_STAGES]


def test_pipeline_cancelled_stops_later_stages(tmp_path, monkeypatch):
    cfg = get_folder_config(tmp_path)
    init_db(cfg.db_path)
    called: list[str] = []

    def fake_get(name: str):
        def fn(cfg, on_progress=None, paths=None, **_kwargs):
            called.append(name)
            if name == "classical":
                raise PipelineCancelled()
            return 1

        return fn

    monkeypatch.setattr("selects.server.pipeline_runner.get_stage_callable", fake_get)
    published: list[dict] = []
    run_pipeline_stages(cfg, published.append)
    assert "classical" in called
    assert "embed" not in called
    assert published[-1]["stage"] == "cancelled"
