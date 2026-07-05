"""GPS clustering + reverse geocoding + Wikipedia enrichment for story visits.

Algorithm per day:
1. Cluster photos with valid GPS using DBSCAN (eps=0.005 deg ~500m, min_samples=3).
2. For each cluster compute centroid, time range, photo count.
3. Reverse-geocode the centroid via Nominatim (cached in geocode_cache table).
4. Fetch Wikipedia summary for identifiable landmarks (cached in same row).
5. Return ordered list of Visit objects ready for DB insertion.
"""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import requests
from sklearn.cluster import DBSCAN
import numpy as np

log = logging.getLogger(__name__)

# Nominatim strict rate-limit: 1 req/sec
_LAST_NOMINATIM_CALL: float = 0.0
_NOMINATIM_INTERVAL = 1.1  # seconds between calls

_SESSION = requests.Session()
_SESSION.headers.update({
    "User-Agent": "travelcull/0.1 (research)",
    "Accept-Language": "en",
})

# Words that indicate a non-landmark generic OSM result — skip Wikipedia
_GENERIC_WORDS = {
    "road", "lane", "path", "track", "way", "street", "highway", "route",
    "village", "hamlet", "settlement", "area", "district", "region",
    "unnamed", "unknown", "unspecified",
}

# DBSCAN parameters — tight ~250m radius keeps places from snowballing
# into multi-km blobs along travel routes. Was 0.005 (~550m) and produced
# clusters spanning >10km when stitched together transitively.
DBSCAN_EPS = 0.0025     # degrees (~275 m at Ladakh latitude)
DBSCAN_MIN_SAMPLES = 3

# Maximum landmark-match radius. Earlier this used per-landmark radii up to
# 0.30 deg (~33 km), which made unrelated GPS clusters all collapse into
# "Pangong Tso" or "Nubra Valley". Cap at ~1.5 km so a landmark only names
# a cluster when it's genuinely close.
MAX_LANDMARK_RADIUS = 0.014   # degrees (~1.5 km at this latitude)

# Known Ladakh landmarks with precise GPS coordinates.
# Used to improve on Nominatim's generic results for remote/natural features.
# Matching radius: 0.05 degrees (~5 km) — coarse enough to catch shore photos.
_KNOWN_LANDMARKS: list[tuple[float, float, float, str]] = [
    # (lat, lon, radius_deg, name)
    (33.77, 78.67, 0.30, "Pangong Tso"),         # Pangong lake — very spread
    (33.96, 78.42, 0.20, "Pangong Tso"),         # western shore camp area
    (34.281, 76.700, 0.08, "Pangong Tso"),       # south Pangong
    (34.28, 77.60, 0.12, "Nubra Valley"),        # Hunder area
    (34.29, 76.71, 0.10, "Diskit"),              # Diskit Monastery area
    (34.30, 76.70, 0.08, "Diskit Monastery"),
    (34.29, 76.70, 0.10, "Nubra Valley"),
    (34.22, 77.17, 0.08, "Hunder"),              # Hunder sand dunes
    (34.24, 77.15, 0.06, "Hunder Sand Dunes"),
    (34.28, 77.60, 0.06, "Nubra Valley"),
    (34.28, 77.63, 0.06, "Nubra Valley"),
    (34.27, 77.60, 0.08, "Nubra Valley"),
    (34.2796, 77.6045, 0.10, "Nubra Valley"),
    (34.27, 77.60, 0.15, "Nubra Valley"),
    (34.2670, 77.6263, 0.08, "Nubra Valley"),
    (34.283, 77.605, 0.10, "Nubra Valley"),
    (34.26, 77.63, 0.10, "Nubra Valley"),
    (34.29, 76.70, 0.12, "Nubra Valley"),        # Diskit side
    (34.2879, 76.7095, 0.08, "Diskit"),
    (34.23, 77.17, 0.10, "Hunder"),
    (34.28, 77.60, 0.08, "Sumur, Nubra"),
    (34.28, 76.71, 0.08, "Diskit Monastery"),
    (34.167, 77.588, 0.08, "Leh"),
    (34.165, 77.590, 0.05, "Leh"),
    (34.166, 77.587, 0.06, "Leh"),
    (34.164, 77.584, 0.06, "Leh"),
    (34.168, 77.584, 0.06, "Leh Old Town"),
    (34.152, 77.570, 0.06, "Leh"),
    (34.1435, 77.5564, 0.06, "Leh"),
    (34.17, 77.58, 0.08, "Leh"),
    (34.09, 77.77, 0.06, "Thiksey Monastery"),
    (34.076, 77.664, 0.06, "Hemis Monastery"),
    (34.073, 77.633, 0.06, "Hemis"),
    (34.056, 77.667, 0.06, "Hemis Monastery"),
    (34.076, 77.643, 0.06, "Hemis"),
    (34.145, 77.526, 0.08, "Shanti Stupa, Leh"),
    (34.152, 77.527, 0.06, "Shanti Stupa, Leh"),
    (34.16, 77.57, 0.06, "Leh"),
    (34.168, 77.589, 0.06, "Leh Market"),
    (34.175, 77.35, 0.08, "Alchi Monastery"),
    (34.193, 77.34, 0.08, "Alchi"),
    (34.196, 77.334, 0.08, "Alchi Monastery"),
    (34.225, 77.27, 0.06, "Nimmu"),
    (34.240, 77.15, 0.06, "Hunder"),
    (34.235, 77.175, 0.06, "Hunder"),
    (34.199, 77.60, 0.06, "Spituk Monastery"),
    (34.282, 77.607, 0.08, "Panamik, Nubra"),
    (34.072, 77.645, 0.07, "Hemis"),
    (34.059, 77.667, 0.06, "Hemis Monastery"),
    (34.071, 77.638, 0.07, "Hemis National Park"),
]


@dataclass
class VisitData:
    """Transient struct built before DB insertion."""
    rank: int
    name: str
    summary: Optional[str]
    lat: float
    lon: float
    elevation_m: Optional[int]
    arrived_at: datetime
    departed_at: datetime
    photo_count: int
    cover_photo_id: Optional[int]


def cluster_day_photos(
    photos: list[dict],  # dicts with keys: photo_id, taken_at, gps_lat, gps_lon
) -> list[list[dict]]:
    """DBSCAN-cluster photos by (lat, lon). Return list of clusters (each = list of photos).

    Photos without GPS are dropped from clusters. Noise points (-1 label) are dropped.
    Returns clusters ordered chronologically by median taken_at.
    """
    gps_photos = [p for p in photos if p.get("gps_lat") is not None and p.get("gps_lon") is not None]
    if len(gps_photos) < DBSCAN_MIN_SAMPLES:
        return []

    coords = np.array([[p["gps_lat"], p["gps_lon"]] for p in gps_photos])
    labels = DBSCAN(eps=DBSCAN_EPS, min_samples=DBSCAN_MIN_SAMPLES, metric="euclidean").fit_predict(coords)

    clusters: dict[int, list[dict]] = {}
    for label, photo in zip(labels, gps_photos):
        if label == -1:
            continue
        clusters.setdefault(label, []).append(photo)

    if not clusters:
        return []

    # Order clusters by median taken_at
    def median_time(photos_in_cluster: list[dict]) -> datetime:
        times = sorted(p["taken_at"] for p in photos_in_cluster)
        return times[len(times) // 2]

    ordered = sorted(clusters.values(), key=median_time)
    return ordered


def _check_known_landmark(lat: float, lon: float) -> Optional[str]:
    """Return known landmark name if (lat, lon) is within the smaller of
    the landmark's declared radius or ``MAX_LANDMARK_RADIUS`` (~1.5 km).
    """
    best_name: Optional[str] = None
    best_dist: float = float("inf")
    for klat, klon, radius, name in _KNOWN_LANDMARKS:
        effective_radius = min(radius, MAX_LANDMARK_RADIUS)
        dist = ((lat - klat) ** 2 + (lon - klon) ** 2) ** 0.5
        if dist <= effective_radius and dist < best_dist:
            best_dist = dist
            best_name = name
    return best_name


def _throttle_nominatim() -> None:
    global _LAST_NOMINATIM_CALL
    elapsed = time.monotonic() - _LAST_NOMINATIM_CALL
    if elapsed < _NOMINATIM_INTERVAL:
        time.sleep(_NOMINATIM_INTERVAL - elapsed)
    _LAST_NOMINATIM_CALL = time.monotonic()


def _pick_display_name(data: dict) -> str:
    """Extract the best human-readable name from a Nominatim jsonv2 response."""
    # Prefer extratags.name, then various address fields
    extratags = data.get("extratags") or {}
    address = data.get("address") or {}
    osm_name = data.get("name", "")

    # OSM name for the feature itself (e.g., "Hemis Monastery") — highly reliable
    # But only use if the osm_name is not generic admin boundary
    if osm_name and data.get("type") not in ("administrative",):
        return osm_name

    candidates = [
        extratags.get("name"),
        address.get("tourism"),
        address.get("natural"),
        address.get("amenity"),
        address.get("leisure"),
        address.get("historic"),
        address.get("mountain_pass"),
        address.get("peak"),
        address.get("water"),
        address.get("waterway"),
        address.get("suburb"),
        address.get("neighbourhood"),
        address.get("quarter"),
    ]
    for c in candidates:
        if c and c.strip():
            return c.strip()

    # City/town/village fallback
    for key in ("city", "town", "village", "hamlet", "county"):
        val = address.get(key)
        if val and val.strip():
            return val.strip()

    # Fall back to Nominatim's display_name (first comma-separated segment)
    display = data.get("display_name", "")
    if display:
        first = display.split(",")[0].strip()
        if first:
            return first

    return "Unknown location"


def _is_generic(name: str) -> bool:
    """Return True if the resolved name is too generic to warrant a Wikipedia lookup."""
    lower = name.lower()
    for word in _GENERIC_WORDS:
        if word in lower.split():
            return True
    # Very short names under 4 chars are also suspect
    if len(name) < 4:
        return True
    return False


def reverse_geocode(lat: float, lon: float, session_db) -> tuple[str, Optional[str]]:
    """Return (display_name, wikipedia_summary) for a lat/lon.

    Uses geocode_cache table to avoid repeat API calls.
    Priority: 1) cache, 2) known-landmarks table, 3) Nominatim API.
    session_db is a SQLAlchemy session — we open/close it outside this fn.
    """
    from travelcull.db.models import GeocodeCache

    lat_r = round(lat, 3)
    lon_r = round(lon, 3)

    # Check cache first
    cached = session_db.get(GeocodeCache, (lat_r, lon_r))
    if cached is not None:
        return cached.display_name or "Unknown location", cached.wikipedia_summary

    # Check known-landmarks table for reliable POI names
    landmark_name = _check_known_landmark(lat, lon)

    if landmark_name:
        # Known landmark — fetch Wikipedia summary directly
        summary = _fetch_wikipedia_summary(landmark_name)
        row = GeocodeCache(lat_round=lat_r, lon_round=lon_r, payload=None,
                           display_name=landmark_name, wikipedia_summary=summary)
        session_db.add(row)
        session_db.commit()
        log.info("Landmark match: (%.4f, %.4f) -> %s", lat, lon, landmark_name)
        return landmark_name, summary

    # Fall back to Nominatim API
    _throttle_nominatim()
    try:
        resp = _SESSION.get(
            "https://nominatim.openstreetmap.org/reverse",
            params={
                "format": "jsonv2",
                "lat": lat,
                "lon": lon,
                # zoom=17 targets specific POIs / building / hamlet level so
                # we don't end up with "Nubra Valley" naming clusters 85 km
                # apart. Was zoom=14 (city/town level).
                "zoom": 17,
                "addressdetails": 1,
                "extratags": 1,
            },
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        log.warning("Nominatim error at (%.4f, %.4f): %s", lat, lon, exc)
        # Cache a failed result so we don't retry forever
        row = GeocodeCache(lat_round=lat_r, lon_round=lon_r, payload=None,
                           display_name="Unknown location", wikipedia_summary=None)
        session_db.add(row)
        session_db.commit()
        return "Unknown location", None

    payload_text = json.dumps(data)
    name = _pick_display_name(data)
    summary = None

    if not _is_generic(name):
        summary = _fetch_wikipedia_summary(name)

    row = GeocodeCache(lat_round=lat_r, lon_round=lon_r, payload=payload_text,
                       display_name=name, wikipedia_summary=summary)
    session_db.add(row)
    session_db.commit()
    return name, summary


def _fetch_wikipedia_summary(title: str) -> Optional[str]:
    """Fetch a ~2-3 sentence summary from Wikipedia REST API.

    First tries direct page lookup. Falls back to opensearch if the direct title
    returns a disambiguation or 404. The opensearch result is only accepted when
    the canonical title begins with (case-insensitive) the search term — this
    prevents wild mismatches like 'Assoo' -> 'Barbara Dolores Assoon'.
    Returns None on any failure or irrelevant match.
    """
    # Normalize title for URL
    slug = title.replace(" ", "_")
    try:
        resp = _SESSION.get(
            f"https://en.wikipedia.org/api/rest_v1/page/summary/{requests.utils.quote(slug)}",
            timeout=10,
        )
        if resp.status_code == 200:
            data = resp.json()
            # Reject disambiguation pages
            if data.get("type") == "disambiguation":
                pass  # fall through to opensearch with Ladakh context
            else:
                extract = data.get("extract", "").strip()
                if extract and len(extract) > 20:
                    return _trim_summary(extract)
    except Exception as exc:
        log.debug("Wikipedia direct lookup failed for %r: %s", title, exc)

    # Fall back: opensearch with Ladakh context — require title relevance check
    try:
        # Try searching with " Ladakh" appended for ambiguous names
        search_title = f"{title} Ladakh" if len(title) < 10 else title
        resp = _SESSION.get(
            "https://en.wikipedia.org/w/api.php",
            params={
                "action": "opensearch",
                "search": search_title,
                "limit": 3,
                "namespace": 0,
                "format": "json",
            },
            timeout=10,
        )
        if resp.status_code == 200:
            data = resp.json()
            candidates = data[1] if len(data) > 1 else []
            for canonical in candidates:
                # Accept only if canonical title contains the search term (case-insensitive)
                # This prevents e.g. 'Assoo' matching 'Barbara Dolores Assoon'
                title_lower = title.lower()
                canonical_lower = canonical.lower()
                if not (title_lower in canonical_lower or canonical_lower.startswith(title_lower)):
                    continue
                slug2 = canonical.replace(" ", "_")
                resp2 = _SESSION.get(
                    f"https://en.wikipedia.org/api/rest_v1/page/summary/{requests.utils.quote(slug2)}",
                    timeout=10,
                )
                if resp2.status_code == 200:
                    data2 = resp2.json()
                    if data2.get("type") == "disambiguation":
                        continue
                    extract = data2.get("extract", "").strip()
                    if extract and len(extract) > 20:
                        return _trim_summary(extract)
    except Exception as exc:
        log.debug("Wikipedia opensearch failed for %r: %s", title, exc)

    return None


def _trim_summary(text: str, max_chars: int = 480) -> str:
    """Trim to approximately 2-3 sentences, max_chars hard cap."""
    # Split on sentence endings
    sentences = re.split(r'(?<=[.!?])\s+', text.strip())
    result = ""
    for s in sentences:
        if len(result) + len(s) + 1 <= max_chars:
            result = (result + " " + s).strip()
        else:
            break
    return result or text[:max_chars]


def build_visits_for_day(
    story_id: int,
    photos: list[dict],   # dicts: photo_id, taken_at, gps_lat, gps_lon, aesthetic_iqa
    Session,              # SQLAlchemy sessionmaker
) -> list[VisitData]:
    """Cluster, geocode, enrich, and return ordered VisitData for a story day.

    Opens its own short-lived DB sessions per geocode call to avoid long holds.
    """
    clusters = cluster_day_photos(photos)
    if not clusters:
        return []

    visits = []
    for rank, cluster in enumerate(clusters):
        lats = [p["gps_lat"] for p in cluster]
        lons = [p["gps_lon"] for p in cluster]
        lat_c = float(np.mean(lats))
        lon_c = float(np.mean(lons))

        times = sorted(p["taken_at"] for p in cluster)
        arrived_at = times[0]
        departed_at = times[-1]

        # Pick cover photo = highest aesthetic_iqa in cluster
        cover = max(cluster, key=lambda p: p.get("aesthetic_iqa") or 0.0)
        cover_photo_id = cover["photo_id"]

        # Elevation: photos don't have elevation in the schema, skip
        elevation_m = None

        # Reverse geocode using a fresh session
        with Session() as s:
            name, summary = reverse_geocode(lat_c, lon_c, s)

        visits.append(VisitData(
            rank=rank,
            name=name,
            summary=summary,
            lat=lat_c,
            lon=lon_c,
            elevation_m=elevation_m,
            arrived_at=arrived_at,
            departed_at=departed_at,
            photo_count=len(cluster),
            cover_photo_id=cover_photo_id,
        ))

    # Disambiguate visits that landed on the same Nominatim name but are
    # geographically far apart. Without this, a /best/place/<name> view
    # would aggregate clusters that are 50+ km apart under one label.
    _disambiguate_same_name_visits(visits, min_separation_km=2.0)

    log.info("Day story %d: %d visits — %s", story_id, len(visits),
             " > ".join(v.name for v in visits))
    return visits


def _disambiguate_same_name_visits(visits: list, min_separation_km: float = 2.0) -> None:
    """Mutate ``visits`` so that same-name entries separated by more than
    ``min_separation_km`` between centroids get a numeric suffix.

    Example: three "Nubra Valley" centroids that are 20 km apart become
    "Nubra Valley", "Nubra Valley (2)", "Nubra Valley (3)".
    """
    from collections import defaultdict
    by_name: dict[str, list] = defaultdict(list)
    for v in visits:
        by_name[v.name].append(v)
    for name, group in by_name.items():
        if len(group) < 2:
            continue
        # Compute pairwise centroid separation; if >min_separation between any
        # pair, treat them as distinct sub-locations.
        far_apart = False
        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                dlat_km = (group[i].lat - group[j].lat) * 111.0
                dlon_km = (group[i].lon - group[j].lon) * 92.0
                if (dlat_km * dlat_km + dlon_km * dlon_km) ** 0.5 > min_separation_km:
                    far_apart = True
                    break
            if far_apart:
                break
        if not far_apart:
            continue
        # Sort by arrival time so the suffix order is intuitive.
        group.sort(key=lambda v: v.arrived_at)
        for idx, v in enumerate(group):
            if idx == 0:
                continue  # the earliest keeps the bare name
            v.name = f"{v.name} ({idx + 1})"
