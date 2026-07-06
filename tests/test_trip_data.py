"""Tests for selects.ml.trip_data — per-library data loaders + geo helper."""
from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from selects.config import get_folder_config
from selects.ml import trip_data
from selects.ml.trip_data import (
    DEFAULT_KEYWORDS,
    DEFAULT_TAG_PROMPTS,
    km_per_deg_lon,
    load_keywords,
    load_landmarks,
    load_tag_prompts,
)


@pytest.fixture(autouse=True)
def _clear_cache():
    trip_data.clear_cache()
    yield
    trip_data.clear_cache()


def _cfg(folder: Path):
    return get_folder_config(folder)


def _write_state(folder: Path, name: str, content: str) -> None:
    state = folder / ".selects"
    state.mkdir(parents=True, exist_ok=True)
    (state / name).write_text(content, encoding="utf-8")


# ── km_per_deg_lon ───────────────────────────────────────────────────────────

def test_km_per_deg_lon_equator():
    assert km_per_deg_lon(0.0) == pytest.approx(111.32, abs=1e-6)


def test_km_per_deg_lon_shrinks_with_latitude():
    assert km_per_deg_lon(60.0) == pytest.approx(111.32 * math.cos(math.radians(60.0)))
    # ~55.66 km at 60°, roughly half the equatorial value
    assert km_per_deg_lon(60.0) < km_per_deg_lon(0.0)


def test_km_per_deg_lon_matches_legacy_ladakh_constant():
    # The old hardcoded 92.0 was 111.32*cos(34°); confirm the helper reproduces it.
    assert km_per_deg_lon(34.0) == pytest.approx(92.0, abs=0.5)


# ── landmarks ────────────────────────────────────────────────────────────────

def test_landmarks_default_empty(tmp_path):
    assert load_landmarks(_cfg(tmp_path)) == []


def test_landmarks_override(tmp_path):
    data = [
        {"name": "Test Peak", "lat": 12.5, "lon": 34.5, "radius_m": 2000},
        {"name": "No Radius", "lat": 1.0, "lon": 2.0},
    ]
    _write_state(tmp_path, "landmarks.json", json.dumps(data))
    result = load_landmarks(_cfg(tmp_path))
    assert len(result) == 2
    assert result[0]["name"] == "Test Peak"
    assert result[0]["radius_m"] == 2000.0
    assert "radius_m" not in result[1]


def test_landmarks_malformed_falls_back(tmp_path):
    _write_state(tmp_path, "landmarks.json", "{ not valid json ]")
    assert load_landmarks(_cfg(tmp_path)) == []


def test_landmarks_skips_bad_entries(tmp_path):
    data = [
        {"name": "Good", "lat": 1.0, "lon": 2.0},
        {"name": "Missing lon", "lat": 1.0},
    ]
    _write_state(tmp_path, "landmarks.json", json.dumps(data))
    result = load_landmarks(_cfg(tmp_path))
    assert [r["name"] for r in result] == ["Good"]


# ── keywords ─────────────────────────────────────────────────────────────────

def test_keywords_default(tmp_path):
    assert load_keywords(_cfg(tmp_path)) == DEFAULT_KEYWORDS


def test_keywords_override(tmp_path):
    data = {"Beaches": ["beach", "sand"]}
    _write_state(tmp_path, "keywords.json", json.dumps(data))
    result = load_keywords(_cfg(tmp_path))
    assert result == {"Beaches": ["beach", "sand"]}


def test_keywords_malformed_falls_back(tmp_path):
    _write_state(tmp_path, "keywords.json", "not json at all {{{")
    assert load_keywords(_cfg(tmp_path)) == DEFAULT_KEYWORDS


# ── tag prompts ──────────────────────────────────────────────────────────────

def test_tag_prompts_default(tmp_path):
    assert load_tag_prompts(_cfg(tmp_path)) == DEFAULT_TAG_PROMPTS


def test_tag_prompts_override(tmp_path):
    data = {"cat": ["a photo of a cat"]}
    _write_state(tmp_path, "tag_prompts.json", json.dumps(data))
    result = load_tag_prompts(_cfg(tmp_path))
    assert result == {"cat": ["a photo of a cat"]}


def test_tag_prompts_malformed_falls_back(tmp_path):
    _write_state(tmp_path, "tag_prompts.json", "]]bad[[")
    assert load_tag_prompts(_cfg(tmp_path)) == DEFAULT_TAG_PROMPTS


# ── caching ──────────────────────────────────────────────────────────────────

def test_loaders_cache_per_state_dir(tmp_path):
    # First read: no file -> default
    assert load_keywords(_cfg(tmp_path)) == DEFAULT_KEYWORDS
    # Writing the file after the value is cached should NOT change the result
    _write_state(tmp_path, "keywords.json", json.dumps({"X": ["y"]}))
    assert load_keywords(_cfg(tmp_path)) == DEFAULT_KEYWORDS
    # After clearing the cache the new file is picked up
    trip_data.clear_cache()
    assert load_keywords(_cfg(tmp_path)) == {"X": ["y"]}


def test_defaults_are_generic():
    # No Ladakh-specific terms leak into the generic defaults.
    banned = {"yak", "prayer_flags", "barren_terrain", "monastery", "shrine"}
    assert banned.isdisjoint(DEFAULT_TAG_PROMPTS.keys())
