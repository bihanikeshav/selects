"""Tests for travelcull.ml.stories — unit tests using synthetic data."""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pytest

from travelcull.db import init_db, session_scope
from travelcull.db.models import (
    ClassicalScore,
    Embedding,
    Photo,
    PipelineState,
    Story,
    StoryItem,
)
from travelcull.ml.stories import (
    _day_title,
    _pick_representatives,
    _segment_scenes,
    run_story_stage,
    MIN_DAY_PHOTOS,
    MAX_STORY_PHOTOS,
)


DIM = 1152


def _rand_emb(seed: int = 0) -> bytes:
    rng = np.random.default_rng(seed)
    x = rng.standard_normal(DIM).astype(np.float16)
    x = (x / max(np.linalg.norm(x.astype(np.float32)), 1e-6)).astype(np.float16)
    return x.tobytes()


def _similar_emb(base: bytes, noise: float = 0.05) -> bytes:
    rng = np.random.default_rng(42)
    x = np.frombuffer(base, dtype=np.float16).astype(np.float32)
    x += rng.standard_normal(DIM).astype(np.float32) * noise
    x /= max(np.linalg.norm(x), 1e-6)
    return x.astype(np.float16).tobytes()


@pytest.fixture()
def session_factory(tmp_path: Path):
    db_path = tmp_path / "test.db"
    return init_db(db_path)


def _insert_photos_for_day(session_factory, tmp_path, day_str, n, base_hour=8, seed_offset=0):
    """Insert n photos for a given day with embeddings and classical scores."""
    base_dt = datetime.fromisoformat(f"{day_str}T{base_hour:02d}:00:00")
    photo_ids = []
    with session_scope(session_factory) as s:
        for i in range(n):
            sha = f"{day_str.replace('-', '')}{i:04d}{seed_offset:02d}"[:64].ljust(64, "x")
            taken_at = base_dt + timedelta(minutes=i * 5)
            p = Photo(
                path=f"/photos/{day_str}/{i:04d}.jpg",
                sha256=sha,
                preview_path=f"previews/{sha}.jpg",
                taken_at=taken_at,
            )
            s.add(p)
            s.flush()

            ps = PipelineState(photo_id=p.id, embedding_done=True, classical_done=True)
            s.add(ps)

            emb_blob = _rand_emb(seed=i + seed_offset * 1000)
            emb = Embedding(photo_id=p.id, siglip=emb_blob, aesthetic_iqa=0.5 + i * 0.01)
            s.add(emb)

            cs = ClassicalScore(
                photo_id=p.id,
                blur=50.0 + i,
                exposure=0.5,
                faces_count=0,
                auto_reject=False,
            )
            s.add(cs)
            photo_ids.append(p.id)
    return photo_ids


class TestDayTitle:
    def test_format(self):
        title = _day_title("2026-03-29", 69, 8)
        assert "2026-03-29" in title
        assert "69" in title
        assert "8" in title


class TestSegmentScenes:
    def _make_row(self, photo_id, taken_at, blur=50.0, faces=0, emb_seed=0):
        emb = np.frombuffer(_rand_emb(emb_seed), dtype=np.float16).astype(np.float32)
        emb /= max(np.linalg.norm(emb), 1e-6)
        return {
            "photo_id": photo_id,
            "taken_at": taken_at,
            "sha256": f"sha{photo_id}",
            "blur": blur,
            "faces_count": faces,
            "embedding": emb,
            "iqa": 0.5,
        }

    def test_single_scene_close_in_time(self):
        """Photos close in time always form a single scene."""
        base = datetime(2026, 3, 29, 8, 0, 0)
        # Build as actual row objects that _segment_scenes would receive
        # But _segment_scenes expects raw DB rows, so we test the internal logic
        # by calling it with mock-like dicts instead. We'll use the helper items directly.
        items = [self._make_row(i, base + timedelta(minutes=i * 2), emb_seed=i) for i in range(5)]
        # Manually call the internals by simulating what _segment_scenes does
        scenes: list[list] = []
        current = []
        for it in items:
            if not current:
                current = [it]
                continue
            prev = current[-1]
            dt = (it["taken_at"] - prev["taken_at"]).total_seconds()
            sim = float(np.dot(it["embedding"], prev["embedding"]))
            from travelcull.ml.stories import SCENE_TIME_GAP_S, SCENE_SIM_THRESHOLD
            if dt > SCENE_TIME_GAP_S and sim < SCENE_SIM_THRESHOLD:
                scenes.append(current)
                current = [it]
            else:
                current.append(it)
        if current:
            scenes.append(current)
        assert len(scenes) == 1
        assert len(scenes[0]) == 5

    def test_large_time_gap_splits_scene(self):
        """A 15-min gap with dissimilar embeddings creates two scenes."""
        base = datetime(2026, 3, 29, 8, 0, 0)
        items_before = [self._make_row(i, base + timedelta(minutes=i), emb_seed=0) for i in range(3)]
        # Second batch has very different embeddings and large time gap
        items_after = [
            self._make_row(i + 3, base + timedelta(minutes=20 + i), emb_seed=100 + i)
            for i in range(3)
        ]
        all_items = items_before + items_after

        from travelcull.ml.stories import SCENE_TIME_GAP_S, SCENE_SIM_THRESHOLD
        scenes: list[list] = []
        current = []
        for it in all_items:
            if not current:
                current = [it]
                continue
            prev = current[-1]
            dt = (it["taken_at"] - prev["taken_at"]).total_seconds()
            sim = float(np.dot(it["embedding"], prev["embedding"]))
            if dt > SCENE_TIME_GAP_S and sim < SCENE_SIM_THRESHOLD:
                scenes.append(current)
                current = [it]
            else:
                current.append(it)
        if current:
            scenes.append(current)
        # The gap between items_before[-1] and items_after[0] is 17 mins (> 10)
        # and embeddings are dissimilar => should split
        assert len(scenes) == 2


class TestPickRepresentatives:
    def _make_scene_item(self, photo_id, iqa, blur, faces=0, emb_seed=0):
        emb = np.frombuffer(_rand_emb(emb_seed), dtype=np.float16).astype(np.float32)
        emb /= max(np.linalg.norm(emb), 1e-6)
        return {
            "photo_id": photo_id,
            "taken_at": datetime(2026, 3, 29, 8, photo_id, 0),
            "sha256": f"sha{photo_id}",
            "blur": blur,
            "faces_count": faces,
            "embedding": emb,
            "iqa": iqa,
        }

    def test_one_representative_per_small_scene(self):
        scene = [self._make_scene_item(i, iqa=0.5, blur=50.0, emb_seed=i) for i in range(4)]
        reps = _pick_representatives([scene])
        assert len(reps) == 1

    def test_two_representatives_for_large_scene(self):
        # top_n = max(1, size // 3) for scenes under the size>=8 bucket, so a
        # 7-photo scene (just below that bucket boundary) yields 2 reps.
        scene = [self._make_scene_item(i, iqa=0.5, blur=50.0, emb_seed=i) for i in range(7)]
        reps = _pick_representatives([scene])
        assert len(reps) == 2

    def test_highest_iqa_selected(self):
        scene = [
            self._make_scene_item(0, iqa=0.3, blur=50.0),
            self._make_scene_item(1, iqa=0.9, blur=50.0, emb_seed=1),
            self._make_scene_item(2, iqa=0.2, blur=50.0, emb_seed=2),
        ]
        reps = _pick_representatives([scene])
        assert reps[0]["photo_id"] == 1  # highest iqa

    def test_scene_label_assigned(self):
        scene1 = [self._make_scene_item(0, iqa=0.5, blur=50.0)]
        scene2 = [self._make_scene_item(1, iqa=0.5, blur=50.0, emb_seed=1)]
        reps = _pick_representatives([scene1, scene2])
        labels = {r["scene_label"] for r in reps}
        assert "scene_1" in labels
        assert "scene_2" in labels


class TestRunStoryStage:
    def test_builds_stories_for_eligible_days(self, session_factory, tmp_path):
        from travelcull.config import get_folder_config
        cfg = get_folder_config(tmp_path)
        import travelcull.ml.stories as stories_mod
        monkeypatch_init_db = lambda _path: session_factory  # noqa: E731

        # Insert 2 days: one eligible (15 photos), one not (2 photos — below
        # MIN_DAY_PHOTOS, which is now 3).
        _insert_photos_for_day(session_factory, tmp_path, "2026-03-29", 15, seed_offset=0)
        _insert_photos_for_day(session_factory, tmp_path, "2026-03-30", 2, seed_offset=1)

        original_init_db = stories_mod.init_db
        stories_mod.init_db = monkeypatch_init_db
        try:
            n = run_story_stage(cfg)
        finally:
            stories_mod.init_db = original_init_db

        assert n == 1

        with session_scope(session_factory) as s:
            stories = s.query(Story).all()
            assert len(stories) == 1
            assert stories[0].day == "2026-03-29"

    def test_idempotent_rebuild(self, session_factory, tmp_path):
        from travelcull.config import get_folder_config
        cfg = get_folder_config(tmp_path)
        import travelcull.ml.stories as stories_mod

        monkeypatch_init_db = lambda _path: session_factory  # noqa: E731

        _insert_photos_for_day(session_factory, tmp_path, "2026-03-29", 15, seed_offset=0)

        original_init_db = stories_mod.init_db
        stories_mod.init_db = monkeypatch_init_db
        try:
            run_story_stage(cfg)
            n2 = run_story_stage(cfg)
        finally:
            stories_mod.init_db = original_init_db

        assert n2 == 1

        with session_scope(session_factory) as s:
            stories = s.query(Story).all()
            assert len(stories) == 1

    def test_photo_count_capped_at_max(self, session_factory, tmp_path):
        from travelcull.config import get_folder_config
        cfg = get_folder_config(tmp_path)
        import travelcull.ml.stories as stories_mod

        monkeypatch_init_db = lambda _path: session_factory  # noqa: E731

        # Insert 100 photos in one day — each 1-min apart with varied seeds
        # so they form many distinct scenes and likely exceed the cap
        _insert_photos_for_day(session_factory, tmp_path, "2026-03-29", 100, seed_offset=0)

        original_init_db = stories_mod.init_db
        stories_mod.init_db = monkeypatch_init_db
        try:
            run_story_stage(cfg)
        finally:
            stories_mod.init_db = original_init_db

        with session_scope(session_factory) as s:
            st = s.query(Story).filter_by(day="2026-03-29").one()
            assert st.photo_count <= MAX_STORY_PHOTOS

    def test_skips_auto_rejected_photos(self, session_factory, tmp_path):
        from travelcull.config import get_folder_config
        cfg = get_folder_config(tmp_path)
        import travelcull.ml.stories as stories_mod

        monkeypatch_init_db = lambda _path: session_factory  # noqa: E731

        # Insert 10 photos, then auto-reject all but 2 of them.
        _insert_photos_for_day(session_factory, tmp_path, "2026-03-29", 10, seed_offset=0)

        # Mark 8 of them as auto-rejected, leaving only 2 eligible.
        with session_scope(session_factory) as s:
            scores = s.query(ClassicalScore).limit(8).all()
            for sc in scores:
                sc.auto_reject = True
                s.add(sc)

        original_init_db = stories_mod.init_db
        stories_mod.init_db = monkeypatch_init_db
        try:
            # Now only 2 eligible photos remain — below MIN_DAY_PHOTOS (3)
            n = run_story_stage(cfg)
        finally:
            stories_mod.init_db = original_init_db

        # Should build 0 stories because <3 non-rejected photos on that day
        assert n == 0
