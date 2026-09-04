"""Map markers and the Best-Of curate facets."""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import FastAPI, HTTPException, Query

from selects.config import FolderConfig
from selects.db import init_db, session_scope
from selects.db.models import Photo, PhotoCategory, PhotoPerson, Visit

log = logging.getLogger(__name__)


def register_map_routes(app: FastAPI, cfg: FolderConfig) -> None:
    def Session():
        return init_db(cfg.db_path)()

    @app.get("/api/map/markers")
    def map_markers(grid_deg: float = Query(0.01, gt=0, lt=1)):
        """Return GPS-binned photo clusters for the map view.

        Buckets photos onto a ~1km grid (default 0.01° ≈ 1.1 km at the
        equator, slightly less at high latitudes) and returns a cover photo
        for each bucket so the frontend renders one pin per geographic
        cluster instead of 1000 overlapping pins.
        """
        from collections import defaultdict as _dd

        with session_scope(Session) as s:
            rows = s.query(
                Photo.id,
                Photo.sha256,
                Photo.gps_lat,
                Photo.gps_lon,
                Photo.taken_at,
            ).filter(
                Photo.gps_lat.is_not(None),
                Photo.gps_lon.is_not(None),
            ).all()

            visit_rows = s.query(Visit.name, Visit.lat, Visit.lon, Visit.arrived_at, Visit.departed_at).all()

        # Bin into grid cells
        cells: dict[tuple[int, int], dict] = _dd(lambda: {"photos": [], "lat_sum": 0.0, "lon_sum": 0.0})
        for pid, sha, lat, lon, _taken in rows:
            key = (round(lat / grid_deg), round(lon / grid_deg))
            cells[key]["photos"].append((pid, sha, lat, lon, _taken))
            cells[key]["lat_sum"] += lat
            cells[key]["lon_sum"] += lon

        # Map cell → nearest named visit (if any)
        def nearest_visit(lat: float, lon: float) -> Optional[str]:
            best = None
            best_d = 0.05  # ~5km cap
            for name, vlat, vlon, *_ in visit_rows:
                if vlat is None or vlon is None:
                    continue
                d = ((lat - vlat) ** 2 + (lon - vlon) ** 2) ** 0.5
                if d < best_d:
                    best_d = d
                    best = name
            return best

        markers = []
        for cell, data in cells.items():
            n = len(data["photos"])
            lat = data["lat_sum"] / n
            lon = data["lon_sum"] / n
            # Pick the latest photo as cover (most recent moment)
            cover_pid, cover_sha, _, _, _ = max(data["photos"], key=lambda p: p[4] or "")
            markers.append({
                "lat": lat,
                "lon": lon,
                "count": n,
                "cover_sha256": cover_sha,
                "cover_url": f"/api/thumb/{cover_sha}",
                "place": nearest_visit(lat, lon),
            })

        markers.sort(key=lambda m: -m["count"])
        return {"total": sum(m["count"] for m in markers), "markers": markers}

    # ── Best-Of / curated facets ─────────────────────────────────────────────
    @app.get("/api/curate")
    def curate_scope(
        facet: str = Query(..., description="day|place|person|category"),
        value: str = Query(..., description="facet value"),
        limit: int = Query(200, ge=1, le=1000),
        scope_pct: float = Query(None, description="override per-scope percentile gate"),
        library_pct: float = Query(None, description="override library-wide percentile floor"),
    ):
        """Return a curated ranked set of photos for a Best-Of facet.

        Pipeline: scope filter → library-wide top-25% aesthetic gate →
        burst dedup → sort by combined_aesthetic desc.

        Facets:
          - day=YYYY-MM-DD
          - place=NAME           (matches Visit.name)
          - person=N             (Person.id)
          - category=landscape|portrait|object|unclassified
        """
        from selects.ml.curation import curate

        with session_scope(Session) as s:
            # Resolve the scope to a set of photo IDs
            if facet == "day":
                from sqlalchemy import text as _text
                scope_ids = [
                    row[0]
                    for row in s.execute(
                        _text("SELECT id FROM photos WHERE strftime('%Y-%m-%d', taken_at) = :d"),
                        {"d": value},
                    ).fetchall()
                ]
            elif facet == "place":
                # photos at any Visit with this name — by overlapping taken_at
                visits = s.query(Visit).filter(Visit.name == value).all()
                if not visits:
                    return {"facet": facet, "value": value, "total": 0, "photos": []}
                # Photos taken between any visit's arrived_at and departed_at
                scope_ids = []
                for v in visits:
                    rows = (
                        s.query(Photo.id)
                        .filter(Photo.taken_at >= v.arrived_at)
                        .filter(Photo.taken_at <= v.departed_at)
                        .all()
                    )
                    scope_ids.extend(r[0] for r in rows)
                scope_ids = list(set(scope_ids))
            elif facet == "person":
                try:
                    pid_int = int(value)
                except ValueError:
                    raise HTTPException(400, detail="person value must be an integer id")
                rows = s.query(PhotoPerson.photo_id).filter(PhotoPerson.person_id == pid_int).all()
                scope_ids = [r[0] for r in rows]
            elif facet == "category":
                if value not in ("landscape", "portrait", "object", "unclassified"):
                    raise HTTPException(400, detail="unknown category")
                rows = (
                    s.query(PhotoCategory.photo_id)
                    .filter(PhotoCategory.primary_category == value)
                    .all()
                )
                scope_ids = [r[0] for r in rows]
            else:
                raise HTTPException(400, detail=f"unknown facet '{facet}'")

            if not scope_ids:
                return {"facet": facet, "value": value, "total": 0, "photos": []}

            eff_scope = scope_pct if scope_pct is not None else cfg.aesthetic_per_scope_pct
            eff_library = library_pct if library_pct is not None else cfg.aesthetic_library_pct
            curated_list = curate(
                s, scope_ids,
                sort="score",
                ap_w=cfg.ap_weight, nima_w=cfg.nima_weight,
                pct_floor=eff_scope,
                library_pct_floor=eff_library,
            )
            curated_list = curated_list[:limit]

            return {
                "facet": facet,
                "value": value,
                "total": len(curated_list),
                "photos": [
                    {
                        "photo_id": c.photo_id,
                        "sha256": c.sha256,
                        "taken_at": c.taken_at,
                        "thumb_url": f"/api/thumb/{c.sha256}",
                        "preview_url": f"/api/preview/{c.sha256}",
                        "combined": c.combined,
                        "iqa": c.iqa,
                        "ap25": c.ap25,
                        "nima": c.nima,
                        "moment_id": c.moment_id,
                        "moment_size": c.moment_size,
                    }
                    for c in curated_list
                ],
            }

    @app.get("/api/curate/facets")
    def curate_facets():
        """Return available facet values for the Best-Of dropdown."""
        with session_scope(Session) as s:
            # Days with at least one photo
            from sqlalchemy import text as _text
            day_rows = s.execute(_text(
                "SELECT strftime('%Y-%m-%d', taken_at) d, COUNT(*) n FROM photos "
                "WHERE taken_at IS NOT NULL GROUP BY d ORDER BY d"
            )).fetchall()
            # Places: distinct Visit.name with count
            place_rows = (
                s.query(Visit.name, Visit.photo_count)
                .order_by(Visit.photo_count.desc())
                .all()
            )
            # Aggregate place by name
            place_agg: dict[str, int] = {}
            for name, n in place_rows:
                place_agg[name] = place_agg.get(name, 0) + (n or 0)
            # Persons
            from selects.db.models import Person
            persons = s.query(Person.id, Person.label, Person.photo_count).all()
            # Categories
            cat_rows = s.execute(_text(
                "SELECT primary_category, COUNT(*) FROM photo_categories "
                "GROUP BY primary_category"
            )).fetchall()
            return {
                "days": [{"value": d, "count": n} for d, n in day_rows if d],
                "places": [
                    {"value": k, "count": v}
                    for k, v in sorted(place_agg.items(), key=lambda kv: -kv[1])
                ],
                "persons": [
                    {"value": str(pid), "label": label or f"P{pid}", "count": n}
                    for pid, label, n in sorted(persons, key=lambda p: -(p[2] or 0))
                ],
                "categories": [
                    {"value": cat, "count": n}
                    for cat, n in cat_rows if cat
                ],
            }
