"""Calibrated video scoring, 1-second timeline, and highlight selection.

See ``docs/superpowers/specs/2026-09-14-video-highlights-cull-design.md``.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

ANALYSIS_VERSION = "video-cull-v2"

CHEAP_SHORT_FPS = 2.0
CHEAP_NORMAL_FPS = 1.0
CHEAP_LONG_MAX_SAMPLES = 3600
ML_FPS = 1.0
ML_MAX_FRAMES = 1200
SCENE_THRESHOLD = 0.22
FOCUS_NEUTRAL = 0.55
FOCUS_HARD_DEAD = 0.12
CONTRAST_MIN_FOR_BLUR = 0.08
EXPOSURE_CLIP_DEAD = 0.95
BLACK_MEAN = 0.08
MOTION_STILL = 0.40
LOW_ACTIVITY_FACTOR = 0.85
DEAD_FOOTAGE_RATIO = 0.70
MIN_HIGHLIGHT_QUALITY = 0.45
MIN_HIGHLIGHT_PROMINENCE = 0.08
HIGHLIGHT_SPACING_SEC = 4.0
HIGHLIGHT_K_PER_SEC = 20.0
HIGHLIGHT_K_MAX = 12
HIGHLIGHT_CANDIDATE_MULT = 3
HIGHLIGHT_LEN_TARGET = (3.0, 8.0)
HIGHLIGHT_LEN_SOFT = 12.0
HIGHLIGHT_LEN_HARD = 15.0
DIVERSITY_COSINE_GATE = 0.88
DIVERSITY_PENALTY = 0.15
FALLOFF = 0.60
SHORT_MAX_MIN = 20.0 * 60.0
MEDIUM_MAX_MIN = 60.0 * 60.0

SCORE_WEIGHTS: dict[str, float] = {
    "iqa": 0.25,
    "focus": 0.20,
    "exposure": 0.15,
    "motion": 0.15,
    "faces": 0.15,
    "audio": 0.10,
}

REASON_NAMES = {
    "iqa": "aesthetic",
    "focus": "focus",
    "exposure": "exposure",
    "motion": "motion",
    "faces": "faces",
    "audio": "audio",
}


@dataclass
class SecondBin:
    t_sec: float
    sharp: float
    exposure: float
    motion: float
    scene_delta: float
    iqa: float | None
    iqa_source: str
    face_presence: float
    audio: float
    quality: float
    hard_dead: bool
    low_activity: bool
    parts: dict[str, float] = field(default_factory=dict)


@dataclass
class Highlight:
    start: float
    end: float
    score: float
    reason: str


def focus_quality(rgb: np.ndarray) -> tuple[float, float]:
    """Return ``(focus[0,1], contrast_std[0,1])``.

    Focus is "would extra blur change this?", not Laplacian texture energy.
    Low-contrast flats return :data:`FOCUS_NEUTRAL`.
    """
    import cv2

    if rgb.ndim == 3:
        gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    else:
        gray = rgb
    contrast = float(gray.std()) / 255.0
    if contrast < CONTRAST_MIN_FOR_BLUR:
        return FOCUS_NEUTRAL, contrast
    lap = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    # Tiny extra blur: sharp frames lose a lot of Laplacian; already-defocused
    # frames barely change. Square so defocus sits well below FOCUS_HARD_DEAD.
    extra = cv2.GaussianBlur(gray, (3, 3), 0.8)
    lap_b = float(cv2.Laplacian(extra, cv2.CV_64F).var())
    if lap <= 1e-9:
        return 0.0, contrast
    ratio = min(1.0, lap_b / (lap + 1e-9))
    residual = max(0.0, 1.0 - ratio)
    focus = max(0.0, min(1.0, residual * residual))
    return focus, contrast


def motion_score(delta: float) -> float:
    """Bell-shaped score from 64×36 mean-abs luma delta in ``[0, 1]``."""
    d = max(0.0, float(delta))
    if d < 0.015:
        useful = MOTION_STILL
    elif d < 0.10:
        t = (d - 0.015) / (0.10 - 0.015)
        useful = 0.75 + t * (1.0 - 0.75)
    elif d <= 0.18:
        t = (d - 0.10) / (0.18 - 0.10)
        useful = 1.0 + t * (0.55 - 1.0)
    else:
        useful = 0.25
    stability = 1.0 if d < 0.18 else 0.4
    return float(useful * stability)


def luma_delta(previous: np.ndarray, current: np.ndarray) -> float:
    """Mean absolute luma delta on a 64×36 gray downscale."""
    import cv2

    def small(image: np.ndarray) -> np.ndarray:
        if image.ndim == 3:
            gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
        else:
            gray = image
        return cv2.resize(gray, (64, 36), interpolation=cv2.INTER_AREA).astype(np.float32)

    a = small(previous)
    b = small(current)
    return float(np.mean(np.abs(a - b)) / 255.0)


def is_hard_dead_frame(
    *,
    focus: float,
    contrast: float,
    exposure: float,
    mean: float,
    clipped_ratio: float,
    freeze: bool = False,
) -> bool:
    if freeze:
        return True
    if mean < BLACK_MEAN and contrast < CONTRAST_MIN_FOR_BLUR:
        return True
    if clipped_ratio > EXPOSURE_CLIP_DEAD and (mean < 0.15 or mean > 0.85):
        return True
    if contrast >= CONTRAST_MIN_FOR_BLUR and focus < FOCUS_HARD_DEAD:
        return True
    return False


def freeze_is_hard_dead(*, identical_run: bool, decoder_error: bool, changing_sides: bool) -> bool:
    return bool(identical_run) and bool(decoder_error or changing_sides)


def is_low_activity(*, silent: bool, motion: float, face_presence: float) -> bool:
    return bool(silent) and motion <= MOTION_STILL + 0.02 and face_presence <= 0.05


def combine_score(
    parts: dict[str, float | None],
    *,
    missing_globally: set[str],
    low_activity: bool = False,
) -> float:
    weights = {key: value for key, value in SCORE_WEIGHTS.items() if key not in missing_globally}
    total_w = sum(weights.values()) or 1.0
    acc = 0.0
    for key, weight in weights.items():
        value = parts.get(key)
        if value is None:
            value = 0.5
        acc += (weight / total_w) * float(value)
    if low_activity:
        acc *= LOW_ACTIVITY_FACTOR
    return float(max(0.0, min(1.0, acc)))


def reason_from_parts(parts: dict[str, float], weights: dict[str, float] | None = None) -> str:
    used = weights or SCORE_WEIGHTS
    best_key = "iqa"
    best = float("-inf")
    for key, weight in used.items():
        signal = float(parts.get(key, 0.5))
        contribution = weight * (signal - 0.5)
        if contribution > best:
            best = contribution
            best_key = key
    return REASON_NAMES.get(best_key, best_key)


def sample_plan(duration_sec: float) -> tuple[float, int, int]:
    """Return ``(cheap_fps, cheap_cap, ml_cap)``."""
    duration = max(0.0, float(duration_sec))
    if duration <= SHORT_MAX_MIN:
        fps = CHEAP_SHORT_FPS
        return fps, max(1, int(math.ceil(duration * fps))), max(1, int(math.ceil(duration * ML_FPS)))
    if duration <= MEDIUM_MAX_MIN:
        fps = CHEAP_NORMAL_FPS
        return fps, max(1, int(math.ceil(duration * fps))), ML_MAX_FRAMES
    return CHEAP_NORMAL_FPS, CHEAP_LONG_MAX_SAMPLES, ML_MAX_FRAMES


def build_scenes(
    times: list[float],
    deltas: list[float],
    threshold: float = SCENE_THRESHOLD,
) -> list[tuple[float, float]]:
    if not times:
        return []
    cuts = [times[0]]
    for stamp, delta in zip(times, deltas):
        if delta >= threshold and stamp > cuts[-1]:
            cuts.append(stamp)
    end = times[-1] + 1.0
    scenes: list[tuple[float, float]] = []
    for index, start in enumerate(cuts):
        stop = cuts[index + 1] if index + 1 < len(cuts) else end
        if stop > start:
            scenes.append((start, stop))
    return scenes or [(times[0], end)]


def _scene_for(t_sec: float, scenes: list[tuple[float, float]]) -> tuple[float, float] | None:
    for start, end in scenes:
        if start <= t_sec < end:
            return start, end
    if scenes and t_sec == scenes[-1][1]:
        return scenes[-1]
    return None


def interpolate_iqa(bins: list[SecondBin], scenes: list[tuple[float, float]]) -> None:
    """Linear interpolate measured IQA inside a scene; never across a cut."""
    by_scene: dict[tuple[float, float], list[int]] = {}
    for index, bin_ in enumerate(bins):
        scene = _scene_for(bin_.t_sec, scenes)
        if scene is None:
            continue
        by_scene.setdefault(scene, []).append(index)
    for indexes in by_scene.values():
        measured = [(bins[i].t_sec, float(bins[i].iqa)) for i in indexes if bins[i].iqa is not None]
        if not measured:
            for i in indexes:
                if bins[i].iqa is None:
                    bins[i].iqa_source = "missing"
            continue
        measured.sort()
        for i in indexes:
            if bins[i].iqa is not None:
                bins[i].iqa_source = "measured"
                continue
            t = bins[i].t_sec
            if t <= measured[0][0]:
                bins[i].iqa = measured[0][1]
            elif t >= measured[-1][0]:
                bins[i].iqa = measured[-1][1]
            else:
                for (t0, v0), (t1, v1) in zip(measured, measured[1:]):
                    if t0 <= t <= t1:
                        span = (t1 - t0) or 1.0
                        bins[i].iqa = v0 + (v1 - v0) * (t - t0) / span
                        break
            bins[i].iqa_source = "interpolated"


def dead_spans(
    bins: list[SecondBin], scenes: list[tuple[float, float]]
) -> list[tuple[float, float]]:
    """Contiguous hard-dead observations; clip at scene edges; never expand."""
    spans: list[tuple[float, float]] = []
    run_start: float | None = None
    run_scene: tuple[float, float] | None = None
    for bin_ in bins:
        scene = _scene_for(bin_.t_sec, scenes)
        if bin_.hard_dead and scene is not None:
            if run_start is None:
                run_start = bin_.t_sec
                run_scene = scene
            elif scene != run_scene:
                spans.append((run_start, min(bin_.t_sec, run_scene[1] if run_scene else bin_.t_sec)))
                run_start = bin_.t_sec
                run_scene = scene
        elif run_start is not None:
            end = bin_.t_sec
            if run_scene is not None:
                end = min(end, run_scene[1])
            if end > run_start:
                spans.append((run_start, end))
            run_start = None
            run_scene = None
    if run_start is not None:
        end = bins[-1].t_sec + 1.0
        if run_scene is not None:
            end = min(end, run_scene[1])
        if end > run_start:
            spans.append((run_start, end))
    return spans


def desired_k(duration_sec: float) -> int:
    if duration_sec <= 0:
        return 0
    return min(HIGHLIGHT_K_MAX, max(0, math.ceil(duration_sec / HIGHLIGHT_K_PER_SEC)))


def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    a = np.asarray(a, dtype=np.float32).reshape(-1)
    b = np.asarray(b, dtype=np.float32).reshape(-1)
    denom = float(np.linalg.norm(a) * np.linalg.norm(b)) + 1e-12
    return float(np.dot(a, b) / denom)


def _crop_around_peak(peak_t: float, peak_q: float, bins: list[SecondBin], scene: tuple[float, float]) -> tuple[float, float]:
    by_t = {round(b.t_sec, 3): b for b in bins}
    left = peak_t
    right = peak_t + 1.0
    floor = FALLOFF * peak_q
    t = peak_t - 1.0
    while t >= scene[0]:
        bin_ = by_t.get(round(t, 3))
        if bin_ is None or bin_.hard_dead or bin_.quality < floor:
            break
        left = t
        t -= 1.0
    t = peak_t + 1.0
    while t < scene[1]:
        bin_ = by_t.get(round(t, 3))
        if bin_ is None or bin_.hard_dead or bin_.quality < floor:
            break
        right = t + 1.0
        t += 1.0
    right = min(right, scene[1])
    left = max(left, scene[0])
    duration = right - left
    target_lo, target_hi = HIGHLIGHT_LEN_TARGET
    if duration < target_lo:
        extra = target_lo - duration
        left = max(scene[0], left - extra / 2.0)
        right = min(scene[1], left + target_lo)
        left = max(scene[0], right - target_lo)
    elif duration > HIGHLIGHT_LEN_HARD:
        half = HIGHLIGHT_LEN_HARD / 2.0
        left = max(scene[0], peak_t - half)
        right = min(scene[1], left + HIGHLIGHT_LEN_HARD)
        left = max(scene[0], right - HIGHLIGHT_LEN_HARD)
    elif duration > HIGHLIGHT_LEN_SOFT:
        half = target_hi / 2.0
        left = max(scene[0], peak_t - half)
        right = min(scene[1], left + target_hi)
        left = max(scene[0], right - target_hi)
    return round(left, 3), round(right, 3)


def select_highlights(
    bins: list[SecondBin],
    scenes: list[tuple[float, float]],
    embeddings: dict[float, np.ndarray] | None = None,
) -> list[Highlight]:
    """Ignore hard-dead. Local max + min quality + prominence + 4 s spacing."""
    if not bins:
        return []
    n = len(bins)
    duration = bins[-1].t_sec + 1.0
    k = desired_k(duration)
    if k == 0:
        return []

    raw: list[tuple[SecondBin, int]] = []
    for index, bin_ in enumerate(bins):
        if bin_.hard_dead or bin_.quality < MIN_HIGHLIGHT_QUALITY:
            continue
        left_q = bins[index - 1].quality if index > 0 and not bins[index - 1].hard_dead else -1.0
        right_q = bins[index + 1].quality if index + 1 < n and not bins[index + 1].hard_dead else -1.0
        if bin_.quality < left_q or bin_.quality < right_q:
            continue
        if index > 0 and bin_.quality == left_q:
            continue
        lo = max(0, index - 3)
        hi = min(n, index + 4)
        valley = min(bins[j].quality for j in range(lo, hi))
        if bin_.quality - valley < MIN_HIGHLIGHT_PROMINENCE:
            continue
        if raw and bin_.t_sec - raw[-1][0].t_sec < HIGHLIGHT_SPACING_SEC:
            if bin_.quality > raw[-1][0].quality:
                raw[-1] = (bin_, index)
            continue
        raw.append((bin_, index))

    ranked = sorted(raw, key=lambda item: item[0].quality, reverse=True)
    chosen: list[Highlight] = []
    chosen_emb: list[np.ndarray] = []
    for bin_, _index in ranked:
        score = bin_.quality
        emb = None
        if embeddings is not None:
            emb = embeddings.get(bin_.t_sec)
            if emb is None:
                nearest = min(embeddings, key=lambda t: abs(t - bin_.t_sec), default=None)
                if nearest is not None and abs(nearest - bin_.t_sec) <= 1.0:
                    emb = embeddings[nearest]
        if emb is not None and chosen_emb:
            sim = max(_cosine(emb, other) for other in chosen_emb)
            if sim >= DIVERSITY_COSINE_GATE:
                score -= DIVERSITY_PENALTY * (sim - DIVERSITY_COSINE_GATE) / (1.0 - DIVERSITY_COSINE_GATE)
        if score < MIN_HIGHLIGHT_QUALITY:
            continue
        scene = _scene_for(bin_.t_sec, scenes) or (bins[0].t_sec, duration)
        start, end = _crop_around_peak(bin_.t_sec, bin_.quality, bins, scene)
        if end - start < 0.5:
            continue
        overlap = False
        for prev in chosen:
            if start < prev.end and end > prev.start:
                overlap = True
                break
        if overlap:
            continue
        parts = bin_.parts or {
            "iqa": bin_.iqa if bin_.iqa is not None else 0.5,
            "focus": bin_.sharp,
            "exposure": bin_.exposure,
            "motion": bin_.motion,
            "faces": bin_.face_presence,
            "audio": bin_.audio,
        }
        chosen.append(Highlight(start, end, float(score), reason_from_parts(parts)))
        if emb is not None:
            chosen_emb.append(emb)
        if len(chosen) >= k:
            break
    chosen.sort(key=lambda item: item.start)
    return chosen


def candidate_cap(duration_sec: float) -> int:
    return min(HIGHLIGHT_CANDIDATE_MULT * max(desired_k(duration_sec), 1), 36)


def candidate_indexes(bins: list[SecondBin], cap: int) -> list[int]:
    """Peak indexes for InsightFace enrichment, highest quality first."""
    if not bins or cap <= 0:
        return []
    n = len(bins)
    raw: list[tuple[float, int]] = []
    last_t = -1e9
    for index, bin_ in enumerate(bins):
        if bin_.hard_dead or bin_.quality < MIN_HIGHLIGHT_QUALITY:
            continue
        left_q = bins[index - 1].quality if index > 0 and not bins[index - 1].hard_dead else -1.0
        right_q = bins[index + 1].quality if index + 1 < n and not bins[index + 1].hard_dead else -1.0
        if bin_.quality < left_q or bin_.quality < right_q:
            continue
        if index > 0 and bin_.quality == left_q:
            continue
        lo = max(0, index - 3)
        hi = min(n, index + 4)
        valley = min(bins[j].quality for j in range(lo, hi))
        if bin_.quality - valley < MIN_HIGHLIGHT_PROMINENCE:
            continue
        if bin_.t_sec - last_t < HIGHLIGHT_SPACING_SEC:
            if raw and bin_.quality > raw[-1][0]:
                raw[-1] = (bin_.quality, index)
                last_t = bin_.t_sec
            continue
        raw.append((bin_.quality, index))
        last_t = bin_.t_sec
    raw.sort(reverse=True)
    return [index for _quality, index in raw[:cap]]


def apply_face_enrichment(
    bins: list[SecondBin],
    images_by_sec: dict[int, np.ndarray],
    *,
    enrich=None,
    missing_globally: set[str] | None = None,
) -> None:
    """Run InsightFace on ~3×K candidate seconds and rewrite those bins' scores."""
    if not bins:
        return
    fn = enrich or enrich_face_quality
    missing = missing_globally or set()
    duration = bins[-1].t_sec + 1.0
    for index in candidate_indexes(bins, candidate_cap(duration)):
        sec = int(round(bins[index].t_sec))
        image = images_by_sec.get(sec)
        if image is None and images_by_sec:
            nearest = min(images_by_sec, key=lambda key: abs(int(key) - sec))
            image = images_by_sec[nearest]
        if image is None:
            continue
        score = float(fn(image))
        bins[index].face_presence = score
        bins[index].parts["faces"] = score
        parts = {
            "iqa": bins[index].iqa,
            "focus": bins[index].sharp,
            "exposure": bins[index].exposure,
            "motion": bins[index].motion,
            "faces": score,
            "audio": bins[index].audio if "audio" not in missing else None,
        }
        bins[index].quality = combine_score(
            parts, missing_globally=missing, low_activity=bins[index].low_activity
        )


def enrich_face_quality(rgb: np.ndarray) -> float:
    """InsightFace eyes/pose in [0, 1]; 0.5 if the detector is missing."""
    try:
        from selects.classical.faces import detect_faces
        from selects.ml.face_attributes import eyes_open_score

        faces = detect_faces(rgb)
    except Exception:
        return 0.5
    if not faces:
        return 0.0
    scores = []
    for face in faces:
        if face.landmark_2d_106 is not None and face.kps is not None:
            try:
                scores.append(eyes_open_score(face.landmark_2d_106, face.kps))
            except Exception:
                scores.append(0.5)
        else:
            scores.append(0.7)
    return float(max(scores) if scores else 0.0)


KEEP_MERGE_GAP_SEC = 0.25


def merge_kept_ranges(
    keeps: list[tuple[float, float]],
    skips: list[tuple[float, float]] | None = None,
    merge_gap: float = KEEP_MERGE_GAP_SEC,
) -> list[tuple[float, float]]:
    """Union of keep ranges, merge small gaps, subtract skip ranges.

    Empty ``keeps`` means the caller should use the full clip (no implicit cuts).
    """
    if not keeps:
        return []
    ordered = sorted((float(a), float(b)) for a, b in keeps if b > a)
    merged: list[list[float]] = [list(ordered[0])]
    for start, end in ordered[1:]:
        if start <= merged[-1][1] + merge_gap:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    if not skips:
        return [(a, b) for a, b in merged]
    result: list[tuple[float, float]] = []
    skip_list = sorted((float(a), float(b)) for a, b in skips if b > a)
    for start, end in merged:
        cursor = start
        for skip_a, skip_b in skip_list:
            if skip_b <= cursor or skip_a >= end:
                continue
            if skip_a > cursor:
                result.append((cursor, min(skip_a, end)))
            cursor = max(cursor, skip_b)
            if cursor >= end:
                break
        if cursor < end:
            result.append((cursor, end))
    return [(a, b) for a, b in result if b - a >= 0.05]
