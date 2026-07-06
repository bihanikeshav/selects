"""Tests for travelcull.recap: self-contained trip-recap HTML generation."""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest
from PIL import Image

from travelcull.config import get_folder_config
from travelcull.db import init_db, session_scope
from travelcull.db.models import AestheticScore, Embedding, Photo, Story, StoryItem, Visit
from travelcull.recap import RecapError, generate_recap, recap_output_path


def _make_preview(cfg, sha: str, color=(120, 80, 40)) -> str:
    rel = f"previews/{sha}.jpg"
    abs_path = cfg.state_dir / rel
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (200, 150), color=color).save(abs_path, "JPEG")
    return rel


def _build_trip(cfg, n_days=2, photos_per_day=4, with_gps=True):
    Session = init_db(cfg.db_path)
    story_ids = []
    with session_scope(Session) as s:
        for d in range(n_days):
            day = f"2026-03-{10 + d:02d}"
            base_dt = datetime.fromisoformat(f"{day}T09:00:00")
            story = Story(day=day, title=f"{day} · Exploring Somewhere · {photos_per_day} photos", photo_count=photos_per_day)
            s.add(story)
            s.flush()
            story_ids.append(story.id)

            for i in range(photos_per_day):
                sha = f"d{d}p{i}".ljust(64, "x")
                preview_rel = _make_preview(cfg, sha, color=(40 * d, 60 + i * 10, 90))
                p = Photo(
                    path=f"/photos/{day}/{i:04d}.jpg",
                    sha256=sha,
                    preview_path=preview_rel,
                    taken_at=base_dt + timedelta(minutes=i * 10),
                    gps_lat=(34.0 + d * 0.1 + i * 0.001) if with_gps else None,
                    gps_lon=(77.5 + d * 0.1 + i * 0.001) if with_gps else None,
                )
                s.add(p)
                s.flush()

                s.add(Embedding(photo_id=p.id, siglip=b"\x00" * 2304, aesthetic_iqa=0.4 + i * 0.05))
                s.add(AestheticScore(photo_id=p.id, nima_score=0.5 + i * 0.02, ap25_score=0.5 + i * 0.03))
                s.add(StoryItem(story_id=story.id, rank=i, photo_id=p.id))

            if with_gps:
                s.add(Visit(
                    story_id=story.id,
                    rank=0,
                    name=f"Place {d}",
                    lat=34.0 + d * 0.1,
                    lon=77.5 + d * 0.1,
                    arrived_at=base_dt,
                    departed_at=base_dt + timedelta(hours=2),
                    photo_count=photos_per_day,
                ))
    return story_ids


class TestGenerateRecap:
    def test_valid_self_contained_html_no_network_refs(self, tmp_path: Path):
        cfg = get_folder_config(tmp_path)
        story_ids = _build_trip(cfg, n_days=2, photos_per_day=4)

        out_path = generate_recap(cfg, story_ids[0])

        assert out_path.exists()
        html = out_path.read_text(encoding="utf-8")

        assert html.strip().startswith("<!doctype html>")
        assert "</html>" in html
        assert "http://" not in html
        assert "https://" not in html
        # embedded images only
        assert "data:image/jpeg;base64," in html
        assert '<img src="/' not in html

    def test_output_path_is_deterministic(self, tmp_path: Path):
        cfg = get_folder_config(tmp_path)
        story_ids = _build_trip(cfg, n_days=1, photos_per_day=3)
        out_path = generate_recap(cfg, story_ids[0])
        assert out_path == recap_output_path(cfg, story_ids[0])

    def test_includes_stats_and_day_sections(self, tmp_path: Path):
        cfg = get_folder_config(tmp_path)
        story_ids = _build_trip(cfg, n_days=3, photos_per_day=5)
        out_path = generate_recap(cfg, story_ids[0])
        html = out_path.read_text(encoding="utf-8")

        assert "recap-stats" in html
        assert html.count('class="recap-day"') == 3
        assert 'class="recap-route-svg"' in html  # GPS present -> route map rendered

    def test_no_gps_skips_route_map(self, tmp_path: Path):
        cfg = get_folder_config(tmp_path)
        story_ids = _build_trip(cfg, n_days=1, photos_per_day=3, with_gps=False)
        out_path = generate_recap(cfg, story_ids[0])
        html = out_path.read_text(encoding="utf-8")
        assert 'class="recap-route-svg"' not in html
        assert '<section class="recap-map">' not in html

    def test_missing_story_raises(self, tmp_path: Path):
        cfg = get_folder_config(tmp_path)
        with pytest.raises(RecapError):
            generate_recap(cfg, 99999)

    def test_caps_total_embedded_photos(self, tmp_path: Path):
        cfg = get_folder_config(tmp_path)
        story_ids = _build_trip(cfg, n_days=10, photos_per_day=10)
        out_path = generate_recap(cfg, story_ids[0], max_photos=40)
        html = out_path.read_text(encoding="utf-8")
        assert html.count("data:image/jpeg;base64,") <= 40

    def test_download_path_matches_generated_file(self, tmp_path: Path):
        cfg = get_folder_config(tmp_path)
        story_ids = _build_trip(cfg, n_days=1, photos_per_day=2)
        out_path = generate_recap(cfg, story_ids[0])
        assert recap_output_path(cfg, story_ids[0]) == out_path
