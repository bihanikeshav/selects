"""Calibrated video scoring, timeline fusion, and highlight selection."""
from __future__ import annotations

import numpy as np
import pytest

from selects.ml import video_cull as vc


def _flat(value: int) -> np.ndarray:
    return np.full((96, 128, 3), value, dtype=np.uint8)


def test_flat_sky_is_neutral_not_dead():
    focus, contrast = vc.focus_quality(_flat(180))
    assert contrast < vc.CONTRAST_MIN_FOR_BLUR
    assert focus == pytest.approx(vc.FOCUS_NEUTRAL, abs=0.05)
    assert not vc.is_hard_dead_frame(
        focus=focus, contrast=contrast, exposure=0.4, mean=0.7, clipped_ratio=0.0
    )


def _checkerboard() -> np.ndarray:
    yy, xx = np.indices((96, 128))
    block = ((xx // 8) + (yy // 8)) % 2 * 255
    return np.stack([block, block, block], axis=-1).astype(np.uint8)


def test_sharp_texture_is_not_hard_dead():
    focus, contrast = vc.focus_quality(_checkerboard())
    assert contrast >= vc.CONTRAST_MIN_FOR_BLUR
    assert focus >= 0.5
    assert not vc.is_hard_dead_frame(
        focus=focus, contrast=contrast, exposure=0.5, mean=0.5, clipped_ratio=0.0
    )


def test_textured_blur_is_hard_dead():
    import cv2

    blurry = cv2.GaussianBlur(_checkerboard(), (15, 15), 4)
    focus, contrast = vc.focus_quality(blurry)
    assert contrast >= vc.CONTRAST_MIN_FOR_BLUR
    assert focus < vc.FOCUS_HARD_DEAD
    assert vc.is_hard_dead_frame(
        focus=focus, contrast=contrast, exposure=0.5, mean=0.5, clipped_ratio=0.0
    )


def test_motion_still_gentle_violent():
    assert vc.motion_score(0.0) == pytest.approx(0.40, abs=0.02)
    assert vc.motion_score(0.06) > 0.85
    assert vc.motion_score(0.25) < 0.20


def test_low_activity_penalty_is_0_85():
    parts = {"iqa": 0.8, "focus": 0.8, "exposure": 0.8, "motion": 0.4, "faces": 0.0, "audio": 0.2}
    raw = vc.combine_score(parts, missing_globally=set())
    penalized = vc.combine_score(parts, missing_globally=set(), low_activity=True)
    assert penalized == pytest.approx(raw * 0.85, abs=1e-6)


def test_global_missing_iqa_renormalizes_once():
    parts = {"iqa": None, "focus": 1.0, "exposure": 1.0, "motion": 1.0, "faces": 1.0, "audio": 1.0}
    score = vc.combine_score(parts, missing_globally={"iqa"})
    assert score == pytest.approx(1.0, abs=1e-6)


def test_sparse_iqa_keeps_weights():
    measured = {"iqa": 0.8, "focus": 0.8, "exposure": 0.8, "motion": 0.8, "faces": 0.8, "audio": 0.8}
    # Interpolated neighbor still has an IQA value; weights stay the same mix.
    interpolated = dict(measured)
    interpolated["iqa"] = 0.8
    assert vc.combine_score(measured, missing_globally=set()) == vc.combine_score(
        interpolated, missing_globally=set()
    )


def test_reason_uses_contribution_not_weight():
    parts = {"iqa": 0.52, "focus": 0.5, "exposure": 0.5, "motion": 0.5, "faces": 0.95, "audio": 0.5}
    assert vc.reason_from_parts(parts, vc.SCORE_WEIGHTS) == "faces"


def test_sample_plan_tiers():
    assert vc.sample_plan(60)[0] == 2.0
    assert vc.sample_plan(21 * 60)[0] == 1.0
    fps, cheap_cap, ml_cap = vc.sample_plan(3 * 3600)
    assert fps == 1.0
    assert cheap_cap == 3600 and ml_cap == 1200


def test_iqa_does_not_cross_scene():
    bins = [
        vc.SecondBin(0, 0.5, 0.5, 0.4, 0.0, 1.0, "measured", 0, 0.5, 0.5, False, False),
        vc.SecondBin(1, 0.5, 0.5, 0.4, 0.0, None, "missing", 0, 0.5, 0.5, False, False),
        vc.SecondBin(2, 0.5, 0.5, 0.4, 0.5, 0.1, "measured", 0, 0.5, 0.5, False, False),
    ]
    vc.interpolate_iqa(bins, [(0.0, 2.0), (2.0, 3.0)])
    assert bins[1].iqa_source == "interpolated"
    assert bins[1].iqa == pytest.approx(1.0)
    assert bins[2].iqa == pytest.approx(0.1)
    assert bins[2].iqa_source == "measured"


def test_dead_span_does_not_expand_to_scene():
    bins = [
        vc.SecondBin(float(i), 0.5, 0.5, 0.4, 0.0, 0.5, "measured", 0, 0.5, 0.5, i == 5, False)
        for i in range(12)
    ]
    spans = vc.dead_spans(bins, [(0.0, 12.0)])
    assert spans == [(5.0, 6.0)]


def test_tripod_identical_is_not_freeze_dead():
    assert vc.freeze_is_hard_dead(identical_run=True, decoder_error=False, changing_sides=False) is False
    assert vc.freeze_is_hard_dead(identical_run=True, decoder_error=False, changing_sides=True) is True


def test_long_clip_scenes_only_at_sampled_timestamps():
    times = [0.0, 10.0, 20.0, 30.0]
    deltas = [0.0, 0.01, 0.5, 0.0]
    scenes = vc.build_scenes(times, deltas)
    starts = [start for start, _end in scenes]
    assert 20.0 in starts
    assert all(t in set(times) or t == times[0] for t in starts)


def test_dull_clip_returns_zero_highlights():
    bins = [
        vc.SecondBin(float(i), 0.2, 0.2, 0.4, 0.0, 0.2, "measured", 0, 0.2, 0.2, False, False)
        for i in range(30)
    ]
    assert vc.select_highlights(bins, [(0.0, 30.0)]) == []


def test_highlight_capped_at_15s_inside_long_scene():
    bins = []
    for i in range(48):
        q = 0.9 if 20 <= i <= 22 else 0.5
        bins.append(vc.SecondBin(float(i), q, 0.8, 0.7, 0.0, q, "measured", 0.2, 0.5, q, False, False))
    out = vc.select_highlights(bins, [(0.0, 48.0)])
    assert out
    assert max(h.end - h.start for h in out) <= 15.0 + 1e-6


def test_diversity_penalizes_near_duplicates_only():
    def peak_at(t: float, extra: float = 0.0) -> list[vc.SecondBin]:
        bins = []
        for i in range(40):
            q = 0.9 + extra if abs(i - t) < 1 else 0.5
            bins.append(vc.SecondBin(float(i), q, 0.8, 0.7, 0.0, q, "measured", 0.2, 0.5, q, False, False))
        return bins

    similar = np.array([1.0, 0.0, 0.0], dtype=np.float32)
    other = np.array([0.0, 1.0, 0.0], dtype=np.float32)
    bins = peak_at(8.0)
    # second peak at 30s
    bins[30] = vc.SecondBin(30.0, 0.9, 0.8, 0.7, 0.0, 0.9, "measured", 0.2, 0.5, 0.9, False, False)
    scenes = [(0.0, 20.0), (20.0, 40.0)]
    dup = vc.select_highlights(bins, scenes, embeddings={8.0: similar, 30.0: similar})
    distinct = vc.select_highlights(bins, scenes, embeddings={8.0: similar, 30.0: other})
    assert len(distinct) >= len(dup)
    assert len(distinct) >= 2


def test_face_enrichment_rescores_candidate_bins():
    bins = []
    for i in range(24):
        q = 0.7 if i in (6, 18) else 0.5
        bins.append(
            vc.SecondBin(
                float(i), q, 0.8, 0.7, 0.0, 0.6, "measured", 0.1, 0.5, q, False, False,
                parts={"iqa": 0.6, "focus": q, "exposure": 0.8, "motion": 0.7, "faces": 0.1, "audio": 0.5},
            )
        )
    img_low = np.zeros((16, 16, 3), dtype=np.uint8)
    img_high = np.full((16, 16, 3), 255, dtype=np.uint8)
    images = {6: img_low, 18: img_high}

    def enrich(img: np.ndarray) -> float:
        return 0.99 if img is img_high else 0.05

    vc.apply_face_enrichment(bins, images, enrich=enrich, missing_globally=set())
    assert bins[18].face_presence > bins[6].face_presence
    assert bins[18].quality > bins[6].quality


def test_enrich_face_quality_missing_detector_is_neutral(monkeypatch):
    monkeypatch.setattr("selects.classical.faces.detect_faces", lambda _img: (_ for _ in ()).throw(RuntimeError("no det")))
    assert vc.enrich_face_quality(_flat(128)) == pytest.approx(0.5)


def test_merge_kept_ranges_requires_keeps_and_subtracts_skips():
    assert vc.merge_kept_ranges([]) == []
    merged = vc.merge_kept_ranges([(0.0, 2.0), (2.1, 4.0)])
    assert merged == [(0.0, 4.0)]
    cut = vc.merge_kept_ranges([(0.0, 10.0)], skips=[(2.0, 3.0)])
    assert cut == [(0.0, 2.0), (3.0, 10.0)]
