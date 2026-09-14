"""Video culling analysis: timeline scoring, highlights, dead footage.

Pipeline stage (``run_video_stage``) that scores a cheap temporal pass into
canonical 1-second bins, flags hard-dead footage, and picks scene-aware
highlight peaks. Optional SigLIP/IQA, faces, audio, and Whisper sit on the
same timeline. See ``docs/superpowers/specs/2026-09-14-video-highlights-cull-design.md``.
"""
from __future__ import annotations

import json
import logging
import math
from dataclasses import asdict, field
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

import numpy as np

from selects.config import FolderConfig
from selects.db import init_db, session_scope
from selects.db.models import Video, VideoSegment, VideoTag
from selects.ml.video_cull import (
    ANALYSIS_VERSION,
    DEAD_FOOTAGE_RATIO,
    SecondBin,
    apply_face_enrichment,
    build_scenes,
    combine_score,
    dead_spans,
    focus_quality,
    interpolate_iqa,
    is_hard_dead_frame,
    is_low_activity,
    luma_delta,
    motion_score,
    sample_plan,
    select_highlights,
)
from selects.util import utcnow

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------

FRAME_SAMPLES = 12          # filmstrip JPEGs (canonical bins are 1 s)
BLUR_GOOD = 100.0           # Laplacian variance at/above which a frame is "sharp"
EXPOSURE_GOOD = 0.35        # exposure score at/above which a frame is "well exposed"
MIN_HIGHLIGHT_FRAMES = 2    # contiguous good frames needed to call it a highlight
FRAME_STRIP_LONG_EDGE = 512  # saved filmstrip JPEG size

FRAMES_SUBDIR = "video_frames"


class VideoDecodeError(RuntimeError):
    """Raised when OpenCV cannot open / decode a video at all."""


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class VideoInfo:
    width: int = 0
    height: int = 0
    fps: float = 0.0
    frame_count: int = 0
    duration_sec: float = 0.0


@dataclass
class FrameScore:
    index: int          # 0-based position within the sampled strip
    frame_index: int    # source frame number in the video
    t_sec: float        # timestamp of the frame
    blur: float         # Laplacian variance (higher = sharper)
    exposure: float     # [0,1] exposure score (higher = better)
    quality: float      # [0,1] blended quality for UI bars
    good: bool          # sharp AND well exposed


@dataclass
class VideoAnalysis:
    info: VideoInfo
    frames: list[FrameScore] = field(default_factory=list)
    bins: list[SecondBin] = field(default_factory=list)
    scenes: list[tuple[float, float]] = field(default_factory=list)
    dead_spans: list[tuple[float, float]] = field(default_factory=list)
    best_index: Optional[int] = None            # index into `frames`
    dead_footage: Optional[bool] = None         # None when nothing decodable
    dead_ratio: float = 0.0
    low_activity_ratio: float = 0.0
    usable_ratio: float = 1.0
    best_score: float = 0.0
    highlights: list[dict] = field(default_factory=list)
    missing_globally: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Frame extraction (cv2 only — no ffmpeg requirement)
# ---------------------------------------------------------------------------


def probe_video(path: Path) -> VideoInfo:
    """Read container metadata via cv2. Raises VideoDecodeError if unopenable."""
    import cv2

    cap = cv2.VideoCapture(str(path))
    try:
        if not cap.isOpened():
            raise VideoDecodeError(f"cv2 cannot open {path}")
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
        frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        duration = frame_count / fps if fps > 0 and frame_count > 0 else 0.0
        return VideoInfo(
            width=width, height=height, fps=fps,
            frame_count=frame_count, duration_sec=duration,
        )
    finally:
        cap.release()


def extract_frames(
    path: Path, n: int = FRAME_SAMPLES
) -> tuple[VideoInfo, list[tuple[int, float, np.ndarray]]]:
    """Extract up to *n* evenly-spaced RGB frames.

    Returns ``(info, [(frame_index, t_sec, rgb_array), ...])``. Frames that
    fail to decode (partial codec support) are skipped rather than raising —
    the caller sees fewer frames. Raises :class:`VideoDecodeError` only when
    the file cannot be opened at all.
    """
    import cv2

    cap = cv2.VideoCapture(str(path))
    try:
        if not cap.isOpened():
            raise VideoDecodeError(f"cv2 cannot open {path}")

        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
        frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)

        out: list[tuple[int, float, np.ndarray]] = []

        if frame_count > 0:
            # Seekable path: evenly spaced indices across the whole clip.
            n_take = min(n, frame_count)
            if n_take == 1:
                indices = [frame_count // 2]
            else:
                step = (frame_count - 1) / (n_take - 1)
                indices = sorted({int(round(i * step)) for i in range(n_take)})
            for idx in indices:
                cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
                ok, bgr = cap.read()
                if not ok or bgr is None:
                    continue
                t = idx / fps if fps > 0 else 0.0
                out.append((idx, t, cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)))
        else:
            # Unknown frame count (some containers): sequential fallback —
            # keep 1 frame per second-ish stride, capped at n.
            stride = max(1, int(fps) or 30)
            idx = 0
            while len(out) < n:
                ok, bgr = cap.read()
                if not ok or bgr is None:
                    break
                if idx % stride == 0:
                    t = idx / fps if fps > 0 else float(len(out))
                    out.append((idx, t, cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)))
                idx += 1
            frame_count = idx

        duration = frame_count / fps if fps > 0 and frame_count > 0 else 0.0
        info = VideoInfo(
            width=width, height=height, fps=fps,
            frame_count=frame_count, duration_sec=duration,
        )
        return info, out
    finally:
        cap.release()


# ---------------------------------------------------------------------------
# Scoring / highlights
# ---------------------------------------------------------------------------


def score_frame(img: np.ndarray) -> tuple[float, float, float, bool]:
    """Return ``(blur, exposure, quality, good)`` for one RGB frame."""
    from selects.classical.blur import laplacian_variance
    from selects.classical.exposure import exposure_score

    blur = laplacian_variance(img)
    exp = exposure_score(img).score
    sharp_norm = min(1.0, blur / (2.0 * BLUR_GOOD))
    quality = 0.5 * sharp_norm + 0.5 * exp
    good = blur >= BLUR_GOOD and exp >= EXPOSURE_GOOD
    return float(blur), float(exp), float(quality), bool(good)


def detect_highlights(frames: list[FrameScore]) -> list[dict]:
    """Contiguous runs of >= MIN_HIGHLIGHT_FRAMES good frames -> segments.

    Returns ``[{start, end, frames}]`` with start/end in seconds.
    """
    segments: list[dict] = []
    run: list[FrameScore] = []

    def flush() -> None:
        if len(run) >= MIN_HIGHLIGHT_FRAMES:
            segments.append(
                {
                    "start": round(run[0].t_sec, 3),
                    "end": round(run[-1].t_sec, 3),
                    "frames": len(run),
                }
            )

    for f in frames:
        if f.good:
            run.append(f)
        else:
            flush()
            run = []
    flush()
    return segments


def _face_presence(img: np.ndarray) -> float:
    """Cheap presence only. InsightFace enrichment runs on highlight candidates."""
    try:
        from selects.classical.faces import _detect_haar

        faces = _detect_haar(img)
    except Exception:
        return 0.0
    return 1.0 if faces else 0.0


def _measure_iqa(images: list[np.ndarray]) -> list[float | None]:
    if not images:
        return []
    try:
        from PIL import Image

        from selects.ml.embed import encode_image_batch
        from selects.ml.onnx_rt import all_present

        if not all_present():
            return [None] * len(images)
        _feats, iqa = encode_image_batch([Image.fromarray(img) for img in images])
        return [float(value) for value in iqa]
    except Exception as exc:
        log.debug("video IQA skipped: %s", exc)
        return [None] * len(images)


def _audio_energy_by_second(path: Path, duration: float) -> tuple[dict[int, float], bool]:
    try:
        from selects.media.audio import analyze_audio

        result = analyze_audio(path)
    except Exception as exc:
        log.debug("video audio analysis skipped: %s", exc)
        return {}, False
    if not result.available:
        return {}, False
    by_sec: dict[int, float] = {}
    for window in result.speech_windows:
        score = 0.85 if window.speech_like else (0.20 if window.rms < 0.01 else 0.55)
        start = int(max(0, math.floor(window.start)))
        end = int(max(start, math.ceil(window.end)))
        for sec in range(start, end + 1):
            by_sec[sec] = max(by_sec.get(sec, 0.0), score)
    if duration and not by_sec:
        by_sec = {sec: 0.20 for sec in range(int(math.ceil(duration)))}
    return by_sec, True


def analyze_video(
    path: Path, n: int | None = None, *, use_ml: bool = True
) -> tuple[VideoAnalysis, list[np.ndarray]]:
    """Full per-video analysis. Returns ``(analysis, filmstrip_rgb_frames)``."""
    info = probe_video(path)
    duration = info.duration_sec or 0.0
    cheap_fps, cheap_cap, ml_cap = sample_plan(duration)
    take = n if n is not None else cheap_cap
    take = max(1, min(take, cheap_cap if duration else take))
    info, raw = extract_frames(path, n=take)

    missing: set[str] = set()
    metrics: list[dict] = []
    previous = None
    previous_hash = None
    identical_run = 0
    from selects.ml.video_search import perceptual_difference, scene_hash

    for frame_idx, t, img in raw:
        focus, contrast = focus_quality(img)
        from selects.classical.exposure import exposure_score as _exposure_score

        exp = _exposure_score(img)
        delta = luma_delta(previous, img) if previous is not None else 0.0
        scene_delta = perceptual_difference(previous, img) if previous is not None else 0.0
        digest = scene_hash(img)
        identical = previous_hash is not None and digest == previous_hash
        identical_run = identical_run + 1 if identical else 0
        freeze = identical_run >= 2  # evidence of freeze still requires changing sides later
        metrics.append(
            {
                "frame_index": frame_idx,
                "t_sec": float(t),
                "img": img,
                "focus": focus,
                "contrast": contrast,
                "exposure": exp.score,
                "mean": exp.mean,
                "clipped_ratio": exp.clipped_ratio,
                "motion": motion_score(delta),
                "scene_delta": scene_delta,
                "face_presence": _face_presence(img),
                "identical_run": identical_run,
                "freeze_candidate": freeze,
            }
        )
        previous = img
        previous_hash = digest

    # Freeze is hard-dead only with changing footage on both sides of a run.
    for i, row in enumerate(metrics):
        if not row["freeze_candidate"]:
            row["freeze"] = False
            continue
        left = any(m["identical_run"] == 0 for m in metrics[:i])
        right = any(m["identical_run"] == 0 for m in metrics[i + 1 :])
        row["freeze"] = bool(left and right)

    duration = max(duration, metrics[-1]["t_sec"] + 1.0 if metrics else 0.0)
    times = [m["t_sec"] for m in metrics]
    deltas = [m["scene_delta"] for m in metrics]
    scenes = build_scenes(times, deltas) if times else []

    ml_indexes = []
    if metrics:
        step = max(1, int(math.floor(len(metrics) / min(ml_cap, len(metrics)))))
        ml_indexes = list(range(0, len(metrics), step))[:ml_cap]
    iqa_vals = _measure_iqa([metrics[i]["img"] for i in ml_indexes])
    if not iqa_vals or all(v is None for v in iqa_vals):
        missing.add("iqa")
        iqa_by_t: dict[float, float] = {}
    else:
        iqa_by_t = {
            metrics[i]["t_sec"]: value
            for i, value in zip(ml_indexes, iqa_vals)
            if value is not None
        }

    audio_by_sec, audio_ok = _audio_energy_by_second(path, duration)
    if not audio_ok:
        missing.add("audio")

    seconds = int(math.ceil(duration)) if duration else 0
    bins: list[SecondBin] = []
    for sec in range(max(seconds, 1) if metrics else 0):
        group = [m for m in metrics if sec <= m["t_sec"] < sec + 1]
        if not group:
            nearest = min(metrics, key=lambda m: abs(m["t_sec"] - sec)) if metrics else None
            group = [nearest] if nearest is not None else []
        if not group:
            continue
        focus = float(np.mean([m["focus"] for m in group]))
        contrast = float(np.mean([m["contrast"] for m in group]))
        exposure = float(np.mean([m["exposure"] for m in group]))
        mean = float(np.mean([m["mean"] for m in group]))
        clipped = float(np.mean([m["clipped_ratio"] for m in group]))
        motion = float(np.mean([m["motion"] for m in group]))
        scene_delta = float(np.max([m["scene_delta"] for m in group]))
        face = float(np.max([m["face_presence"] for m in group]))
        freeze = any(m.get("freeze") for m in group)
        iqa = iqa_by_t.get(group[0]["t_sec"])
        iqa_source = "measured" if iqa is not None else "missing"
        audio = audio_by_sec.get(sec, 0.5 if audio_ok else 0.5)
        silent = audio_ok and audio <= 0.25
        low = is_low_activity(silent=silent, motion=motion, face_presence=face)
        hard = is_hard_dead_frame(
            focus=focus, contrast=contrast, exposure=exposure,
            mean=mean, clipped_ratio=clipped, freeze=freeze,
        )
        parts = {
            "iqa": iqa,
            "focus": focus,
            "exposure": exposure,
            "motion": motion,
            "faces": face,
            "audio": audio if audio_ok else None,
        }
        quality = combine_score(parts, missing_globally=missing, low_activity=low and not hard)
        bins.append(
            SecondBin(
                t_sec=float(sec),
                sharp=focus,
                exposure=exposure,
                motion=motion,
                scene_delta=scene_delta,
                iqa=iqa,
                iqa_source=iqa_source,
                face_presence=face,
                audio=audio,
                quality=quality,
                hard_dead=hard,
                low_activity=low and not hard,
                parts={k: (0.5 if v is None else float(v)) for k, v in parts.items()},
            )
        )

    interpolate_iqa(bins, scenes)
    if "iqa" not in missing:
        for bin_ in bins:
            if bin_.iqa is not None:
                bin_.parts["iqa"] = float(bin_.iqa)
                bin_.quality = combine_score(
                    {
                        "iqa": bin_.iqa,
                        "focus": bin_.sharp,
                        "exposure": bin_.exposure,
                        "motion": bin_.motion,
                        "faces": bin_.face_presence,
                        "audio": bin_.audio if "audio" not in missing else None,
                    },
                    missing_globally=missing,
                    low_activity=bin_.low_activity,
                )

    if use_ml and bins:
        images_by_sec: dict[int, np.ndarray] = {}
        for row in metrics:
            images_by_sec.setdefault(int(row["t_sec"]), row["img"])
        apply_face_enrichment(bins, images_by_sec, missing_globally=missing)

    dead = dead_spans(bins, scenes)
    dead_dur = sum(max(0.0, end - start) for start, end in dead)
    low_dur = sum(1.0 for bin_ in bins if bin_.low_activity)
    dead_ratio = (dead_dur / duration) if duration else 0.0
    highlights = select_highlights(bins, scenes)

    strip_n = min(FRAME_SAMPLES, max(1, len(raw)))
    if len(raw) <= strip_n:
        strip_raw = raw
    else:
        idxs = np.linspace(0, len(raw) - 1, strip_n).round().astype(int)
        strip_raw = [raw[int(i)] for i in sorted(set(idxs))]

    frames: list[FrameScore] = []
    arrays: list[np.ndarray] = []
    for strip_i, (frame_idx, t, img) in enumerate(strip_raw):
        nearest = min(bins, key=lambda b: abs(b.t_sec - t)) if bins else None
        blur, exp, quality, good = score_frame(img)
        if nearest is not None:
            quality = nearest.quality
            good = not nearest.hard_dead
            exp = nearest.exposure
        frames.append(
            FrameScore(
                index=strip_i,
                frame_index=frame_idx,
                t_sec=round(t, 3),
                blur=round(blur, 2),
                exposure=round(exp, 4),
                quality=round(quality, 4),
                good=good,
            )
        )
        arrays.append(img)

    analysis = VideoAnalysis(
        info=info,
        frames=frames,
        bins=bins,
        scenes=scenes,
        dead_spans=dead,
        dead_ratio=dead_ratio,
        low_activity_ratio=(low_dur / duration) if duration else 0.0,
        usable_ratio=max(0.0, 1.0 - dead_ratio),
        best_score=max((b.quality for b in bins), default=0.0),
        missing_globally=sorted(missing),
        highlights=[
            {
                "start": round(h.start, 3),
                "end": round(h.end, 3),
                "frames": max(1, int(round(h.end - h.start))),
                "score": round(h.score, 4),
                "reason": h.reason,
            }
            for h in highlights
        ],
    )
    if bins:
        analysis.dead_footage = dead_ratio > DEAD_FOOTAGE_RATIO
        if frames:
            analysis.best_index = max(range(len(frames)), key=lambda i: frames[i].quality)
    return analysis, arrays


# ---------------------------------------------------------------------------
# Optional SigLIP embedding of the best frame (for search)
# ---------------------------------------------------------------------------


def _embed_best_frame(img: np.ndarray) -> Optional[bytes]:
    """SigLIP-embed one RGB frame -> fp16 blob, or None when ML deps are
    unavailable / the model cannot load. Kept module-level so tests can
    monkeypatch it away."""
    try:
        from PIL import Image

        from selects.ml.embed import encode_image_batch

        feats, _iqa = encode_image_batch([Image.fromarray(img)])
        return feats[0].astype(np.float16).tobytes()
    except Exception as exc:  # noqa: BLE001 — ML extras are optional
        log.debug("SigLIP embedding for video frame skipped: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------


def frames_dir_for(cfg: FolderConfig, sha256: str) -> Path:
    return cfg.state_dir / FRAMES_SUBDIR / sha256


def _replace_segments(session, video: Video, analysis: VideoAnalysis) -> None:
    session.query(VideoSegment).filter(
        VideoSegment.video_id == video.id,
        VideoSegment.kind.in_(("scene", "dead", "highlight")),
    ).delete(synchronize_session=False)
    fingerprint = f"{video.sha256 or ''}:{ANALYSIS_VERSION}"
    rows = []
    for start, end in analysis.scenes:
        rows.append(
            VideoSegment(
                video_id=video.id,
                start_ms=int(round(start * 1000.0)),
                end_ms=int(round(end * 1000.0)),
                kind="scene",
                source_fingerprint=fingerprint,
                processor_version=ANALYSIS_VERSION,
            )
        )
    for start, end in analysis.dead_spans:
        rows.append(
            VideoSegment(
                video_id=video.id,
                start_ms=int(round(start * 1000.0)),
                end_ms=int(round(end * 1000.0)),
                kind="dead",
                source_fingerprint=fingerprint,
                processor_version=ANALYSIS_VERSION,
            )
        )
    for item in analysis.highlights:
        rows.append(
            VideoSegment(
                video_id=video.id,
                start_ms=int(round(item["start"] * 1000.0)),
                end_ms=int(round(item["end"] * 1000.0)),
                kind="highlight",
                score=item.get("score"),
                ocr_text=item.get("reason"),
                source_fingerprint=fingerprint,
                processor_version=ANALYSIS_VERSION,
            )
        )
    session.add_all(rows)
    tags = {row.tag for row in session.query(VideoTag).filter(VideoTag.video_id == video.id).all()}
    if analysis.dead_footage and not (tags & {"kept", "archived", "review"}):
        session.add(VideoTag(video_id=video.id, tag="review", source="analysis", score=1.0))


def _replace_speech_segments(
    session,
    video: Video,
    *,
    silence: list[tuple[float, float]],
    filler: list[tuple[float, float]],
    topics: list[tuple[float, float, str]],
) -> None:
    session.query(VideoSegment).filter(
        VideoSegment.video_id == video.id,
        VideoSegment.kind.in_(("silence", "filler", "topic")),
    ).delete(synchronize_session=False)
    fingerprint = f"{video.sha256 or ''}:{ANALYSIS_VERSION}"
    rows = []
    for start, end in silence:
        rows.append(
            VideoSegment(
                video_id=video.id,
                start_ms=int(round(start * 1000.0)),
                end_ms=int(round(end * 1000.0)),
                kind="silence",
                source_fingerprint=fingerprint,
                processor_version=ANALYSIS_VERSION,
            )
        )
    for start, end in filler:
        rows.append(
            VideoSegment(
                video_id=video.id,
                start_ms=int(round(start * 1000.0)),
                end_ms=int(round(end * 1000.0)),
                kind="filler",
                source_fingerprint=fingerprint,
                processor_version=ANALYSIS_VERSION,
            )
        )
    for start, end, label in topics:
        rows.append(
            VideoSegment(
                video_id=video.id,
                start_ms=int(round(start * 1000.0)),
                end_ms=int(round(end * 1000.0)),
                kind="topic",
                ocr_text=label,
                source_fingerprint=fingerprint,
                processor_version=ANALYSIS_VERSION,
            )
        )
    session.add_all(rows)


def _save_frame_strip(cfg: FolderConfig, sha256: str, arrays: list[np.ndarray]) -> None:
    from selects.indexer.preview import _resize_and_save

    out_dir = frames_dir_for(cfg, sha256)
    out_dir.mkdir(parents=True, exist_ok=True)
    for i, img in enumerate(arrays):
        _resize_and_save(img, FRAME_STRIP_LONG_EDGE, out_dir / f"{i:02d}.jpg")


# ---------------------------------------------------------------------------
# Pipeline stage
# ---------------------------------------------------------------------------

ProgressCb = Callable[[int, int, str], None] | None


def run_video_stage(
    cfg: FolderConfig,
    on_progress: ProgressCb = None,
    n_frames: int = FRAME_SAMPLES,
    embed: bool = True,
    should_cancel: Optional[Callable[[], bool]] = None,
) -> int:
    """Analyse every video with ``processed_at IS NULL``.

    Returns the number of videos processed (including undecodable ones, which
    are marked processed with empty analysis so the stage never loops on
    them). Safe without ML extras: the SigLIP embedding is best-effort.

    *should_cancel* is polled between videos; when it returns True the stage
    raises :class:`~selects.pipeline.PipelineCancelled`. Analysing one video is
    not interruptible, so a cancel lands at the next boundary — every video
    already finished stays committed and is not re-analysed on the next run.
    """
    from selects.pipeline import PipelineCancelled
    from selects.indexer.preview import write_previews

    Session = init_db(cfg.db_path)

    with session_scope(Session) as s:
        pending = [
            (v.id, v.path, v.sha256)
            for v in s.query(Video).all()
            if v.processed_at is None or v.analysis_version != ANALYSIS_VERSION
        ]

    total = len(pending)
    if not total:
        log.info("no videos pending analysis")
        return 0

    processed = 0
    for i, (vid, vpath, sha) in enumerate(pending, start=1):
        if should_cancel is not None and should_cancel():
            log.info("video analysis cancelled after %d of %d video(s)", processed, total)
            raise PipelineCancelled()
        name = Path(vpath).name
        if on_progress:
            on_progress(i, total, name)
        try:
            analysis, arrays = analyze_video(
                Path(vpath),
                n=n_frames,
                use_ml=embed and cfg.speed_mode != "fast",
            )
        except (VideoDecodeError, Exception) as exc:  # noqa: BLE001
            log.warning("video analysis failed for %s: %s", vpath, exc)
            analysis, arrays = VideoAnalysis(info=VideoInfo()), []

        siglip_blob: Optional[bytes] = None
        if analysis.frames and sha:
            try:
                _save_frame_strip(cfg, sha, arrays)
                best = arrays[analysis.best_index or 0]
                # Best frame becomes the video's thumb/preview (same cache
                # layout /api/thumb serves).
                write_previews(best, sha, cfg.thumbs_dir, cfg.previews_dir)
                if embed:
                    siglip_blob = _embed_best_frame(best)
            except Exception as exc:  # noqa: BLE001
                log.warning("failed to persist frames for %s: %s", vpath, exc)

        best_frame = (
            analysis.frames[analysis.best_index]
            if analysis.frames and analysis.best_index is not None
            else None
        )

        with session_scope(Session) as s:
            v = s.get(Video, vid)
            if v is None:
                continue
            if analysis.info.fps:
                v.fps = analysis.info.fps
            if analysis.info.frame_count:
                v.frame_count = analysis.info.frame_count
            if analysis.info.duration_sec and not v.duration_sec:
                v.duration_sec = analysis.info.duration_sec
            if analysis.info.width and not v.width:
                v.width = analysis.info.width
            if analysis.info.height and not v.height:
                v.height = analysis.info.height
            v.best_frame_index = analysis.best_index
            v.sharpness = best_frame.blur if best_frame else None
            v.exposure = best_frame.exposure if best_frame else None
            v.dead_footage = analysis.dead_footage
            v.dead_ratio = analysis.dead_ratio
            v.low_activity_ratio = analysis.low_activity_ratio
            v.usable_ratio = analysis.usable_ratio
            v.best_score = analysis.best_score
            v.analysis_version = ANALYSIS_VERSION
            v.frames_json = json.dumps([asdict(f) for f in analysis.frames])
            v.highlights_json = json.dumps(analysis.highlights)
            if siglip_blob is not None:
                v.siglip = siglip_blob
            v.processed_at = utcnow()
            s.add(v)
            _replace_segments(s, v, analysis)
            if cfg.speed_mode != "fast":
                from selects.ml import video_speech

                words = video_speech.transcribe_video(Path(vpath), cancel=should_cancel)
                if words:
                    fingerprint = f"{v.sha256 or ''}:{ANALYSIS_VERSION}"
                    video_speech.persist_transcript(s, v.id, fingerprint, words)
                    covered = []
                    for start, end in analysis.scenes:
                        dur = max(end - start, 1e-6)
                        spoken = sum(w.d for w in words if start <= w.t < end)
                        covered.append(spoken / dur)
                    silence = video_speech.silence_spans(words, analysis.scenes, covered)
                    filler = video_speech.filler_spans(words)
                    topics: list[tuple[float, float, str]] = []
                    phrases = [(a, b, text) for a, b, text, _group in video_speech.words_to_phrases(words)]
                    if phrases:
                        try:
                            from selects.ml.embed import encode_text_prompts

                            embeddings = encode_text_prompts([text for _a, _b, text in phrases])
                            topics = video_speech.topic_spans(phrases, list(embeddings))
                        except Exception:
                            topics = [(a, b, text) for a, b, text in phrases]
                    _replace_speech_segments(
                        s, v, silence=silence, filler=filler, topics=topics
                    )

        if embed and analysis.frames and sha:
            best_source_index = (
                analysis.frames[analysis.best_index].frame_index
                if analysis.best_index is not None
                else None
            )
            try:
                from selects.ml.video_search import persist_index

                persist_index(
                    cfg,
                    vid,
                    sha,
                    Path(vpath),
                    analysis.info.duration_sec,
                    embed=embed,
                    best_frame_index=best_source_index,
                )
            except Exception as exc:  # noqa: BLE001 — sparse indexing is best-effort.
                log.warning("failed to persist sparse video search index for %s: %s", vpath, exc)

        processed += 1

    log.info("video analysis done: %d videos", processed)
    return processed
