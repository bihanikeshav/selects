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

from travelcull.ml.trip_data import KM_PER_DEG_LAT, km_per_deg_lon, load_landmarks

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

# DBSCAN parameters — tight ~275m radius keeps places from snowballing
# into multi-km blobs along travel routes. Was 0.005 (~550m) and produced
# clusters spanning >10km when stitched together transitively.
# NOTE: eps is in degree-space with euclidean metric, so its metric size varies
# slightly with latitude. ~275 m holds near mid-latitudes; converting to a
# metric (haversine) DBSCAN would be more invasive, so it's left as-is.
DBSCAN_EPS = 0.0025     # degrees (~275 m near mid-latitudes)
DBSCAN_MIN_SAMPLES = 3

# Maximum landmark-match radius (metres). Earlier per-landmark radii went up to
# ~33 km, which made unrelated GPS clusters all collapse into one named place.
# Cap at ~1.5 km so a landmark only names a cluster when it's genuinely close.
MAX_LANDMARK_RADIUS_M = 1500.0
# Default match radius for a landmark entry that omits "radius_m".
DEFAULT_LANDMARK_RADIUS_M = 1500.0

# Landmarks (name/lat/lon/radius_m) are loaded per-library from
# <state_dir>/landmarks.json via trip_data.load_landmarks(). With no file the
# list is empty and reverse geocoding relies entirely on Nominatim.


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


def _check_known_landmark(lat: float, lon: float, landmarks: list[dict]) -> Optional[str]:
    """Return the nearest landmark name if (lat, lon) is within the smaller of
    the landmark's declared radius or ``MAX_LANDMARK_RADIUS_M`` (~1.5 km).

    Distance is computed in metres using a latitude-corrected equirectangular
    approximation so matching works at any latitude.
    """
    best_name: Optional[str] = None
    best_dist: float = float("inf")
    for lm in landmarks:
        klat, klon, name = lm["lat"], lm["lon"], lm["name"]
        radius_m = lm.get("radius_m") or DEFAULT_LANDMARK_RADIUS_M
        effective_radius_m = min(radius_m, MAX_LANDMARK_RADIUS_M)
        dlat_km = (lat - klat) * KM_PER_DEG_LAT
        dlon_km = (lon - klon) * km_per_deg_lon((lat + klat) / 2.0)
        dist_m = ((dlat_km ** 2 + dlon_km ** 2) ** 0.5) * 1000.0
        if dist_m <= effective_radius_m and dist_m < best_dist:
            best_dist = dist_m
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


def reverse_geocode(
    lat: float,
    lon: float,
    session_db,
    landmarks: Optional[list[dict]] = None,
) -> tuple[str, Optional[str]]:
    """Return (display_name, wikipedia_summary) for a lat/lon.

    Uses geocode_cache table to avoid repeat API calls.
    Priority: 1) cache, 2) known-landmarks table, 3) Nominatim API.
    session_db is a SQLAlchemy session — we open/close it outside this fn.
    ``landmarks`` is the per-library landmark table (empty list if none).
    """
    from travelcull.db.models import GeocodeCache

    lat_r = round(lat, 3)
    lon_r = round(lon, 3)

    # Check cache first
    cached = session_db.get(GeocodeCache, (lat_r, lon_r))
    if cached is not None:
        return cached.display_name or "Unknown location", cached.wikipedia_summary

    # Check known-landmarks table for reliable POI names
    landmark_name = _check_known_landmark(lat, lon, landmarks or [])

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
                pass  # fall through to opensearch
            else:
                extract = data.get("extract", "").strip()
                if extract and len(extract) > 20:
                    return _trim_summary(extract)
    except Exception as exc:
        log.debug("Wikipedia direct lookup failed for %r: %s", title, exc)

    # Fall back: opensearch — require title relevance check
    try:
        search_title = title
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
    cfg=None,             # FolderConfig — used to load per-library landmarks
) -> list[VisitData]:
    """Cluster, geocode, enrich, and return ordered VisitData for a story day.

    Opens its own short-lived DB sessions per geocode call to avoid long holds.
    """
    clusters = cluster_day_photos(photos)
    if not clusters:
        return []

    landmarks = load_landmarks(cfg) if cfg is not None else []

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
            name, summary = reverse_geocode(lat_c, lon_c, s, landmarks)

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
                mean_lat = (group[i].lat + group[j].lat) / 2.0
                dlat_km = (group[i].lat - group[j].lat) * KM_PER_DEG_LAT
                dlon_km = (group[i].lon - group[j].lon) * km_per_deg_lon(mean_lat)
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
