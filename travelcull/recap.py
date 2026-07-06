"""Trip recap: a single self-contained, shareable static HTML page.

Aggregates every per-day :class:`~travelcull.db.models.Story` in the library
(the "day-by-day" narrative rows built by :mod:`travelcull.ml.stories` — day
strings shaped ``YYYY-MM-DD``; the synthetic ``place:``/``pattern:``/``people:``
stories are cross-cuts and are not part of the chronological recap) into one
keepsake page: hero stats, an inline SVG route map traced from GPS points, and
day-by-day sections with the best photo(s) of each day, base64-embedded so the
result is a single file with no external references — it can be emailed,
AirDropped, or opened straight from disk.

The *story_id* passed to :func:`generate_recap` is only the trigger (matches
the per-story "Recap" button in the UI) — the recap itself is trip-wide,
since a travelcull library is one folder == one trip.
"""
from __future__ import annotations

import base64
import html
import logging
import math
import re
from dataclasses import dataclass, field
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Optional

from PIL import Image

from travelcull.config import FolderConfig
from travelcull.db import init_db, session_scope
from travelcull.db.models import AestheticScore, Embedding, Photo, Story, StoryItem, Visit

log = logging.getLogger(__name__)

DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# Overall photo cap for the generated page (keeps the file a reasonable size
# to email/share) and the resize target for embedded previews.
MAX_TOTAL_PHOTOS = 40
MAX_PHOTO_DIM = 1200
JPEG_QUALITY = 82

RECAPS_SUBDIR = "recaps"
# Optional cache location a future caption-persistence layer can write to;
# if present for a given day, its text is folded into that day's section.
CAPTIONS_SUBDIR = "captions"


class RecapError(Exception):
    """Raised when a recap cannot be generated (missing story, no photos, ...)."""


@dataclass
class _DayCandidate:
    day: str
    title: str
    items: list = field(default_factory=list)  # list of (StoryItem, Photo, score)


def recap_output_path(cfg: FolderConfig, story_id: int) -> Path:
    """Deterministic output path for a story's recap file (so GET can find it
    without any extra bookkeeping)."""
    return cfg.state_dir / RECAPS_SUBDIR / f"recap_{story_id}.html"


def generate_recap(
    cfg: FolderConfig,
    story_id: int,
    max_photos: int = MAX_TOTAL_PHOTOS,
    max_dim: int = MAX_PHOTO_DIM,
) -> Path:
    """Generate the self-contained recap HTML file and return its path."""
    Session = init_db(cfg.db_path)

    with session_scope(Session) as s:
        anchor = s.get(Story, story_id)
        if anchor is None:
            raise RecapError(f"story {story_id} not found")

        day_stories = (
            s.query(Story)
            .filter(Story.day.isnot(None))
            .order_by(Story.day)
            .all()
        )
        day_stories = [st for st in day_stories if DAY_RE.match(st.day)]

        # Fall back to treating the anchor alone as the whole trip if the
        # library has no proper day-shaped stories (e.g. only themed ones, or
        # a minimal test fixture).
        if not day_stories:
            day_stories = [anchor]

        candidates: list[_DayCandidate] = []
        all_visit_points: list[tuple[datetime, float, float]] = []
        total_kept_photo_ids: set[int] = set()

        for st in day_stories:
            rows = (
                s.query(StoryItem, Photo, AestheticScore, Embedding)
                .join(Photo, StoryItem.photo_id == Photo.id)
                .outerjoin(AestheticScore, AestheticScore.photo_id == Photo.id)
                .outerjoin(Embedding, Embedding.photo_id == Photo.id)
                .filter(StoryItem.story_id == st.id)
                .order_by(StoryItem.rank)
                .all()
            )
            total_kept_photo_ids.update(it.photo_id for it, _p, _a, _e in rows)

            scored = []
            for it, p, aesc, emb in rows:
                score = _photo_score(cfg, aesc, emb, it.rank)
                scored.append((it, p, score))
            candidates.append(_DayCandidate(day=st.day, title=st.title, items=scored))

            visits = (
                s.query(Visit)
                .filter(Visit.story_id == st.id)
                .order_by(Visit.rank)
                .all()
            )
            for v in visits:
                all_visit_points.append((v.arrived_at, v.lat, v.lon))

        total_photos = s.query(Photo).count()

    if not candidates or all(not c.items for c in candidates):
        raise RecapError(f"story {story_id} has no photos to recap")

    selected = _select_photos(candidates, max_photos)

    # Trip title: prefer the folder name (one library == one trip); fall back
    # to the anchor story's own title for single-day/thin libraries.
    folder_name = cfg.folder.name.replace("_", " ").replace("-", " ").strip()
    trip_title = folder_name.title() if folder_name else anchor.title

    days_sorted = sorted(c.day for c in candidates)
    date_range = _format_date_range(days_sorted)

    all_visit_points.sort(key=lambda t: t[0])
    total_km = _route_km(all_visit_points)
    route_svg = _build_route_svg([(lat, lon) for _t, lat, lon in all_visit_points])

    captions_dir = cfg.state_dir / CAPTIONS_SUBDIR

    sections_html = []
    for c in candidates:
        photos_for_day = selected.get(c.day, [])
        if not photos_for_day:
            continue
        caption_html = _load_caption_html(captions_dir, c.day)
        section = _render_day_section(cfg, c, photos_for_day, caption_html, max_dim)
        if section:
            sections_html.append(section)

    stats = {
        "days": len(candidates),
        "km": total_km,
        "taken": total_photos,
        "kept": len(total_kept_photo_ids),
    }

    doc = _render_page(trip_title, date_range, stats, route_svg, sections_html)

    out_path = recap_output_path(cfg, story_id)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(doc, encoding="utf-8")
    log.info("recap: wrote %s (%d day sections, %d photos)", out_path, len(sections_html),
              sum(len(v) for v in selected.values()))
    return out_path


# ─── scoring & selection ──────────────────────────────────────────────────────

def _photo_score(cfg: FolderConfig, aesc, emb, rank: int) -> float:
    """Higher is better. Prefers the combined NIMA/AP25 aesthetic score, falls
    back to the CLIP-IQA score on Embedding, falls back to curation rank
    (earlier-picked representatives score slightly higher)."""
    if aesc is not None and aesc.ap25_score is not None and aesc.nima_score is not None:
        return cfg.ap_weight * aesc.ap25_score + cfg.nima_weight * aesc.nima_score
    if emb is not None and emb.aesthetic_iqa is not None:
        return float(emb.aesthetic_iqa)
    return max(0.0, 1.0 - rank * 0.01)


def _select_photos(candidates: list[_DayCandidate], max_photos: int) -> dict[str, list]:
    """Pick up to *max_photos* total across all days, guaranteeing every day
    with photos gets at least one slot (when the budget allows), then filling
    remaining budget with the globally best leftovers."""
    n_days = sum(1 for c in candidates if c.items)
    if n_days == 0:
        return {}

    per_day_quota = max(1, max_photos // n_days)

    selected: dict[str, list] = {c.day: [] for c in candidates}
    leftovers: list[tuple[float, str, tuple]] = []

    for c in candidates:
        if not c.items:
            continue
        ranked = sorted(c.items, key=lambda t: -t[2])
        take, rest = ranked[:per_day_quota], ranked[per_day_quota:]
        selected[c.day].extend(take)
        for entry in rest:
            leftovers.append((entry[2], c.day, entry))

    used = sum(len(v) for v in selected.values())
    budget = max_photos - used
    if budget > 0 and leftovers:
        leftovers.sort(key=lambda t: -t[0])
        for _score, day, entry in leftovers[:budget]:
            selected[day].append(entry)

    # Order each day's picks chronologically for display.
    for day, entries in selected.items():
        entries.sort(key=lambda t: (t[1].taken_at or datetime.min))

    return selected


# ─── geometry ─────────────────────────────────────────────────────────────────

def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


def _route_km(points: list[tuple[datetime, float, float]]) -> Optional[float]:
    if len(points) < 2:
        return None
    total = 0.0
    for (_t0, lat0, lon0), (_t1, lat1, lon1) in zip(points, points[1:]):
        total += _haversine_km(lat0, lon0, lat1, lon1)
    return total


def _build_route_svg(points: list[tuple[float, float]]) -> Optional[str]:
    """A minimal inline SVG polyline of the trip's GPS route over a plain
    background — no external map tiles, just the shape of the journey."""
    if len(points) < 2:
        return None

    lats = [p[0] for p in points]
    lons = [p[1] for p in points]
    lat_min, lat_max = min(lats), max(lats)
    lon_min, lon_max = min(lons), max(lons)
    mean_lat_rad = math.radians((lat_min + lat_max) / 2.0)
    lon_scale = max(math.cos(mean_lat_rad), 0.05)  # avoid blow-up near poles

    W, H, PAD = 720.0, 320.0, 36.0
    span_x = max((lon_max - lon_min) * lon_scale, 1e-6)
    span_y = max(lat_max - lat_min, 1e-6)
    scale = min((W - 2 * PAD) / span_x, (H - 2 * PAD) / span_y)

    def project(lat: float, lon: float) -> tuple[float, float]:
        x = PAD + (lon - lon_min) * lon_scale * scale
        y = PAD + (lat_max - lat) * scale  # invert: north is up
        return x, y

    xy = [project(lat, lon) for lat, lon in points]
    # Center the (possibly narrower-than-canvas) route within the canvas.
    xs = [p[0] for p in xy]
    ys = [p[1] for p in xy]
    dx = (W - (max(xs) + min(xs))) / 2.0
    dy = (H - (max(ys) + min(ys))) / 2.0
    xy = [(x + dx, y + dy) for x, y in xy]

    path_d = "M " + " L ".join(f"{x:.1f},{y:.1f}" for x, y in xy)
    dots = "".join(
        f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{4 if 0 < i < len(xy) - 1 else 6}" '
        f'class="{"recap-route-dot" if 0 < i < len(xy) - 1 else "recap-route-end"}" />'
        for i, (x, y) in enumerate(xy)
    )
    return (
        f'<svg viewBox="0 0 {W:.0f} {H:.0f}" class="recap-route-svg" role="img" aria-label="Trip route">'
        f'<rect x="0" y="0" width="{W:.0f}" height="{H:.0f}" class="recap-route-bg" />'
        f'<path d="{path_d}" class="recap-route-line" fill="none" />'
        f"{dots}"
        f"</svg>"
    )


# ─── rendering ────────────────────────────────────────────────────────────────

def _embed_photo(cfg: FolderConfig, photo: Photo, max_dim: int) -> Optional[str]:
    if not photo.preview_path:
        return None
    src = cfg.state_dir / photo.preview_path
    if not src.exists():
        return None
    try:
        img = Image.open(src).convert("RGB")
        img.thumbnail((max_dim, max_dim))
        buf = BytesIO()
        img.save(buf, format="JPEG", quality=JPEG_QUALITY)
        b64 = base64.b64encode(buf.getvalue()).decode("ascii")
        return f"data:image/jpeg;base64,{b64}"
    except Exception as exc:  # pragma: no cover - corrupt/odd image formats
        log.warning("recap: could not embed %s: %s", src, exc)
        return None


def _load_caption_html(captions_dir: Path, day: str) -> str:
    """Best-effort pickup of a previously generated caption/narrative for this
    day, if some caching layer wrote one to ``<state_dir>/captions/<day>.json``
    (``{"caption": str, "hashtags": [str, ...]}``). Silently absent otherwise."""
    import json

    path = captions_dir / f"{day}.json"
    if not path.exists():
        return ""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    caption = str(data.get("caption") or "").strip()
    hashtags = [str(t) for t in (data.get("hashtags") or [])]
    if not caption:
        return ""
    parts = [f'<p class="recap-caption">{html.escape(caption)}</p>']
    if hashtags:
        tags = " ".join(f"#{html.escape(t)}" for t in hashtags)
        parts.append(f'<p class="recap-hashtags">{tags}</p>')
    return "".join(parts)


_MONTHS = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]


def _month_day(dt: datetime) -> str:
    """"%B %-d" without relying on the non-portable %-d directive (Windows
    doesn't support it; %#d is the MSVCRT equivalent)."""
    return f"{_MONTHS[dt.month - 1]} {dt.day}"


def _format_date_range(days_sorted: list[str]) -> str:
    if not days_sorted:
        return ""
    try:
        dts = [datetime.strptime(d, "%Y-%m-%d") for d in days_sorted]
    except ValueError:
        return f"{days_sorted[0]} – {days_sorted[-1]}" if len(days_sorted) > 1 else days_sorted[0]
    lo, hi = dts[0], dts[-1]
    if lo.date() == hi.date():
        return f"{_month_day(lo)}, {lo.year}"
    if lo.year == hi.year and lo.month == hi.month:
        return f"{_MONTHS[lo.month - 1]} {lo.day}–{hi.day}, {hi.year}"
    if lo.year == hi.year:
        return f"{_month_day(lo)} – {_month_day(hi)}, {hi.year}"
    return f"{_month_day(lo)}, {lo.year} – {_month_day(hi)}, {hi.year}"


def _render_day_section(cfg: FolderConfig, c: _DayCandidate, photos: list, caption_html: str, max_dim: int) -> str:
    figs = []
    for it, p, _score in photos:
        data_uri = _embed_photo(cfg, p, max_dim)
        if not data_uri:
            continue
        figs.append(f'<figure class="recap-photo"><img src="{data_uri}" loading="lazy" alt="" /></figure>')
    if not figs:
        return ""
    day_label = c.day
    try:
        dt = datetime.strptime(c.day, "%Y-%m-%d")
        day_label = f"{dt.strftime('%A')}, {_month_day(dt)}"
    except ValueError:
        pass
    return (
        '<section class="recap-day">'
        f'<h2 class="recap-day-title">{html.escape(day_label)}</h2>'
        f'<p class="recap-day-subtitle">{html.escape(_clean_story_title(c.title))}</p>'
        f"{caption_html}"
        f'<div class="recap-grid">{"".join(figs)}</div>'
        "</section>"
    )


def _clean_story_title(title: str) -> str:
    """Story titles embed photo/scene counts (e.g. "· 42 photos, 6 scenes") for
    internal browsing; strip that machinery for the keepsake page."""
    return re.split(r"\s*[·—]\s*\d+\s+photos", title)[0].strip()


def _render_page(
    trip_title: str,
    date_range: str,
    stats: dict,
    route_svg: Optional[str],
    sections_html: list[str],
) -> str:
    km_stat = f'{stats["km"]:.0f} km' if stats.get("km") is not None else "—"
    stat_blocks = "".join(
        f'<div class="recap-stat"><span class="recap-stat-num">{v}</span><span class="recap-stat-label">{l}</span></div>'
        for v, l in [
            (stats["days"], "days"),
            (km_stat, "traveled"),
            (stats["taken"], "photos taken"),
            (stats["kept"], "kept"),
        ]
    )
    map_section = (
        f'<section class="recap-map">{route_svg}</section>' if route_svg else ""
    )
    body_sections = "".join(sections_html) or '<p class="recap-empty">No photos to show.</p>'

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>{html.escape(trip_title)} — Trip Recap</title>
<style>
{_CSS}
</style>
</head>
<body>
<header class="recap-hero">
  <p class="recap-kicker">Trip Recap</p>
  <h1 class="recap-title">{html.escape(trip_title)}</h1>
  <p class="recap-dates">{html.escape(date_range)}</p>
  <div class="recap-stats">{stat_blocks}</div>
</header>
{map_section}
<main class="recap-main">
{body_sections}
</main>
<footer class="recap-footer">
  <p>Made with travelcull</p>
</footer>
</body>
</html>
"""


_CSS = """
:root {
  color-scheme: dark;
}
* { box-sizing: border-box; }
html, body {
  margin: 0;
  padding: 0;
  background: #0b0b0d;
  color: #f2f0ec;
  font-family: Georgia, "Times New Roman", serif;
}
.recap-hero {
  max-width: 920px;
  margin: 0 auto;
  padding: 96px 32px 64px;
  text-align: center;
}
.recap-kicker {
  font-family: -apple-system, "Segoe UI", Roboto, sans-serif;
  text-transform: uppercase;
  letter-spacing: 0.28em;
  font-size: 12px;
  color: #a89f8e;
  margin: 0 0 20px;
}
.recap-title {
  font-size: clamp(40px, 7vw, 76px);
  line-height: 1.05;
  margin: 0 0 18px;
  font-weight: 400;
  letter-spacing: -0.01em;
}
.recap-dates {
  font-family: -apple-system, "Segoe UI", Roboto, sans-serif;
  font-size: 16px;
  color: #b8ad98;
  margin: 0 0 56px;
}
.recap-stats {
  display: flex;
  justify-content: center;
  gap: clamp(24px, 5vw, 64px);
  flex-wrap: wrap;
}
.recap-stat {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 6px;
}
.recap-stat-num {
  font-size: 30px;
  font-family: -apple-system, "Segoe UI", Roboto, sans-serif;
  font-weight: 600;
  color: #f2f0ec;
}
.recap-stat-label {
  font-family: -apple-system, "Segoe UI", Roboto, sans-serif;
  font-size: 12px;
  text-transform: uppercase;
  letter-spacing: 0.12em;
  color: #8b8272;
}
.recap-map {
  max-width: 800px;
  margin: 0 auto 64px;
  padding: 0 32px;
}
.recap-route-svg { width: 100%; height: auto; display: block; }
.recap-route-bg { fill: #131316; }
.recap-route-line { stroke: #d4a95e; stroke-width: 2; }
.recap-route-dot { fill: #d4a95e; opacity: 0.55; }
.recap-route-end { fill: #f2f0ec; stroke: #d4a95e; stroke-width: 2; }
.recap-main {
  max-width: 1040px;
  margin: 0 auto;
  padding: 0 24px 96px;
}
.recap-day {
  padding: 56px 0;
  border-top: 1px solid #232227;
}
.recap-day:first-child { border-top: none; }
.recap-day-title {
  font-size: clamp(26px, 4vw, 36px);
  font-weight: 400;
  margin: 0 0 6px;
}
.recap-day-subtitle {
  font-family: -apple-system, "Segoe UI", Roboto, sans-serif;
  color: #a89f8e;
  font-size: 14px;
  margin: 0 0 24px;
}
.recap-caption {
  font-size: 17px;
  line-height: 1.6;
  color: #d8d3c8;
  max-width: 640px;
  margin: 0 0 8px;
}
.recap-hashtags {
  font-family: -apple-system, "Segoe UI", Roboto, sans-serif;
  color: #8b8272;
  font-size: 13px;
  margin: 0 0 24px;
}
.recap-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
  gap: 12px;
  margin-top: 20px;
}
.recap-photo {
  margin: 0;
  overflow: hidden;
  border-radius: 6px;
  background: #131316;
}
.recap-photo img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
  aspect-ratio: 4 / 3;
}
.recap-empty {
  text-align: center;
  color: #8b8272;
  padding: 64px 0;
}
.recap-footer {
  text-align: center;
  padding: 40px 0 72px;
  font-family: -apple-system, "Segoe UI", Roboto, sans-serif;
  font-size: 12px;
  color: #55524c;
}

@media (prefers-color-scheme: light) {
  html, body { background: #0b0b0d; color: #f2f0ec; }
}
"""
