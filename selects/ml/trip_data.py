"""Per-library trip data: landmarks, keyword taxonomy, and tag prompts.

All of this data used to be hardcoded (with Ladakh-specific values) inside the
individual ML modules. It now lives in optional JSON files under a library's
state directory (``<photo folder>/.selects/``) with travel-generic defaults
baked in as fallbacks:

    landmarks.json     list of {"name", "lat", "lon", "radius_m"(optional)}
    keywords.json      {label: [keyword, ...]} theme buckets for pattern stories
    tag_prompts.json   {tag: [prompt, ...]} zero-shot SigLIP taxonomy

Missing or malformed files fall back to the defaults below (a warning is logged
for malformed JSON). See ``examples/ladakh/`` for a worked example you can copy
into your own ``.selects/`` directory to customize.
"""
from __future__ import annotations

import json
import logging
import math
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Geometry helper                                                             #
# --------------------------------------------------------------------------- #

def km_per_deg_lon(lat: float) -> float:
    """Kilometres per degree of longitude at the given latitude.

    Longitude lines converge toward the poles, so this shrinks with |lat|.
    111.32 km/deg is the equatorial value; multiply by cos(lat). Previously the
    codebase hardcoded ``92.0`` (== 111.32·cos(34°)), which is only correct at
    Ladakh's latitude and wrong everywhere else.
    """
    return 111.32 * math.cos(math.radians(lat))


# Latitude is ~constant regardless of where you are.
KM_PER_DEG_LAT = 111.0


# --------------------------------------------------------------------------- #
# Generic defaults                                                            #
# --------------------------------------------------------------------------- #

# Travel-generic zero-shot tag taxonomy. Replaces the old Ladakh-leaning set
# (which had prayer_flags / yak / barren_terrain / monastery / shrine). Copy
# examples/ladakh/tag_prompts.json into your .selects/ to restore those.
DEFAULT_TAG_PROMPTS: dict[str, list[str]] = {
    "landscape":    ["a scenic landscape photograph", "a wide view of nature", "a panoramic vista"],
    "mountain":     ["snow-capped mountains", "a high mountain peak", "a rugged mountain range"],
    "beach":        ["a sandy beach", "a tropical coastline", "waves on the shore"],
    "sky":          ["a sky full of clouds", "a dramatic sky", "stars in the night sky"],
    "sunset":       ["a colorful sunset", "the sun setting over the horizon", "warm golden-hour light"],
    "architecture": ["a building or structure", "traditional architecture", "an arched doorway"],
    "portrait":     ["a portrait of a person", "a person's face", "a close-up of someone"],
    "people":       ["a group of people", "people interacting", "candid people on a trip"],
    "food":         ["a plate of food", "a meal on a table", "local cuisine"],
    "transit":      ["a road through the countryside", "a vehicle on a journey", "travel in transit"],
    "interior":     ["the inside of a room", "an interior space", "indoor lighting"],
    "water":        ["a river or lake", "flowing water", "a reflection on water"],
    "night":        ["a photograph taken at night", "a low-light scene", "city lights at night"],
    "animal":       ["an animal", "wildlife in nature", "a domesticated animal"],
    "street":       ["a busy street scene", "urban streetlife", "a city market"],
    "abstract":     ["an abstract pattern", "a close-up texture", "a minimalist composition"],
    "documents":    ["a document or sign", "text on a page", "a screenshot or receipt"],
    "close_up":     ["a close-up detail shot", "macro photography", "a focused detail"],
}

# Travel-generic theme buckets for pattern stories / thematic cross-cuts.
# label -> list of substrings matched against a photo's visual tags.
DEFAULT_KEYWORDS: dict[str, list[str]] = {
    "Indoor moments":     ["interior", "indoor", "inside", "room"],
    "Landscapes":         ["landscape", "mountain", "valley", "hills", "scenery", "vista", "field"],
    "Architecture":       ["architecture", "building", "temple", "church", "monument", "ruins", "tower"],
    "Food & dining":      ["food", "meal", "dish", "restaurant", "cafe", "drink", "breakfast"],
    "Wildlife & animals": ["animal", "wildlife", "bird", "dog", "cat", "horse"],
    "On the road":        ["road", "transit", "drive", "vehicle", "bus", "car", "train"],
    "Water & coast":      ["water", "river", "lake", "sea", "beach", "ocean", "waterfall"],
    "People & street":    ["people", "portrait", "crowd", "street", "market"],
    "Golden hour & sky":  ["sunset", "sunrise", "sky", "clouds", "dusk", "dawn"],
}


# --------------------------------------------------------------------------- #
# Loaders (cached per state_dir)                                              #
# --------------------------------------------------------------------------- #

_landmarks_cache: dict[str, list[dict]] = {}
_keywords_cache: dict[str, dict[str, list[str]]] = {}
_tag_prompts_cache: dict[str, dict[str, list[str]]] = {}


def _state_dir(cfg) -> Path:
    return Path(cfg.state_dir)


def _load_json(path: Path) -> Any | None:
    """Load JSON from ``path``. Return None if absent; on malformed JSON log a
    warning and return None so the caller falls back to defaults."""
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError) as exc:
        log.warning("malformed or unreadable %s: %s — using defaults", path, exc)
        return None


def load_landmarks(cfg) -> list[dict]:
    """Return the library's landmark table.

    Reads ``<state_dir>/landmarks.json`` (a list of
    ``{"name": str, "lat": float, "lon": float, "radius_m": number(optional)}``)
    if present, else returns ``[]``. With no landmarks, locations.py relies on
    its Nominatim reverse-geocoding path — landmarks are only a fast-path
    override for named POIs.
    """
    key = str(_state_dir(cfg))
    if key in _landmarks_cache:
        return _landmarks_cache[key]

    data = _load_json(_state_dir(cfg) / "landmarks.json")
    result: list[dict] = []
    if isinstance(data, list):
        for item in data:
            try:
                entry = {
                    "name": str(item["name"]),
                    "lat": float(item["lat"]),
                    "lon": float(item["lon"]),
                }
                if item.get("radius_m") is not None:
                    entry["radius_m"] = float(item["radius_m"])
                result.append(entry)
            except (KeyError, TypeError, ValueError) as exc:
                log.warning("skipping malformed landmark entry %r: %s", item, exc)
    elif data is not None:
        log.warning("landmarks.json is not a list — ignoring")

    _landmarks_cache[key] = result
    return result


def load_keywords(cfg) -> dict[str, list[str]]:
    """Return the theme keyword taxonomy (``{label: [keyword, ...]}``).

    Reads ``<state_dir>/keywords.json`` if present, else ``DEFAULT_KEYWORDS``.
    """
    key = str(_state_dir(cfg))
    if key in _keywords_cache:
        return _keywords_cache[key]

    data = _load_json(_state_dir(cfg) / "keywords.json")
    if isinstance(data, dict) and data:
        result = {str(k): [str(x) for x in v] for k, v in data.items()}
    else:
        if data is not None and not isinstance(data, dict):
            log.warning("keywords.json is not an object — using defaults")
        result = DEFAULT_KEYWORDS
    _keywords_cache[key] = result
    return result


def load_tag_prompts(cfg) -> dict[str, list[str]]:
    """Return the zero-shot tag taxonomy (``{tag: [prompt, ...]}``).

    Reads ``<state_dir>/tag_prompts.json`` if present, else
    ``DEFAULT_TAG_PROMPTS``.
    """
    key = str(_state_dir(cfg))
    if key in _tag_prompts_cache:
        return _tag_prompts_cache[key]

    data = _load_json(_state_dir(cfg) / "tag_prompts.json")
    if isinstance(data, dict) and data:
        result = {str(k): [str(x) for x in v] for k, v in data.items()}
    else:
        if data is not None and not isinstance(data, dict):
            log.warning("tag_prompts.json is not an object — using defaults")
        result = DEFAULT_TAG_PROMPTS
    _tag_prompts_cache[key] = result
    return result


def clear_cache() -> None:
    """Drop all cached trip data (used by tests)."""
    _landmarks_cache.clear()
    _keywords_cache.clear()
    _tag_prompts_cache.clear()
