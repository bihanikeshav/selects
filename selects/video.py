"""Video culling analysis: frame sampling, quality scoring, highlights.

Pipeline stage (``run_video_stage``) that brings videos to parity with the
photo pipeline:

* samples N evenly-spaced frames per video with ``cv2.VideoCapture`` (no
  ffmpeg dependency — codecs that OpenCV cannot open degrade gracefully to a
  "processed but empty" row instead of crashing the stage),
* scores every sampled frame with the existing classical metrics
  (:func:`selects.classical.blur.laplacian_variance`,
  :func:`selects.classical.exposure.exposure_score`),
* picks the best frame, rewrites the video's thumb/preview JPEGs from it
  (same ``<sha256>.jpg`` layout served by ``/api/thumb``), and stores a
  SigLIP embedding of that frame on the ``videos`` row for search,
* derives highlight segments (contiguous runs of sharp, well-exposed frames)
  and a dead-footage flag (>70% of sampled frames blurry/dark),
* persists sampled-frame JPEGs under ``<state>/video_frames/<sha256>/`` so
  ``GET /api/videos/{sha256}/frames`` can serve a filmstrip.
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

import numpy as np

from selects.config import FolderConfig
from selects.db import init_db, session_scope
from selects.db.models import Video

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------

FRAME_SAMPLES = 12          # evenly-spaced frames sampled per video
BLUR_GOOD = 100.0           # Laplacian variance at/above which a frame is "sharp"
EXPOSURE_GOOD = 0.35        # exposure score at/above which a frame is "well exposed"
DEAD_FOOTAGE_RATIO = 0.70   # > this fraction of bad frames => dead footage
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
    best_index: Optional[int] = None            # index into `frames`
    dead_footage: Optional[bool] = None         # None when nothing decodable
    highlights: list[dict] = field(default_factory=list)  # {start, end, frames}


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


def analyze_video(
    path: Path, n: int = FRAME_SAMPLES
) -> tuple[VideoAnalysis, list[np.ndarray]]:
    """Full per-video analysis. Returns ``(analysis, raw_rgb_frames)``.

    ``raw_rgb_frames`` aligns 1:1 with ``analysis.frames`` so callers can
    persist the filmstrip / thumbnail without re-decoding.
    """
    info, raw = extract_frames(path, n)

    frames: list[FrameScore] = []
    arrays: list[np.ndarray] = []
    for strip_i, (frame_idx, t, img) in enumerate(raw):
        blur, exp, quality, good = score_frame(img)
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

    analysis = VideoAnalysis(info=info, frames=frames)
    if frames:
        bad = sum(1 for f in frames if not f.good)
        analysis.dead_footage = (bad / len(frames)) > DEAD_FOOTAGE_RATIO
        analysis.best_index = max(range(len(frames)), key=lambda i: frames[i].quality)
        analysis.highlights = detect_highlights(frames)
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
        return feats[0].numpy().astype(np.float16).tobytes()
    except Exception as exc:  # noqa: BLE001 — ML extras are optional
        log.debug("SigLIP embedding for video frame skipped: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------


def frames_dir_for(cfg: FolderConfig, sha256: str) -> Path:
    return cfg.state_dir / FRAMES_SUBDIR / sha256


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
) -> int:
    """Analyse every video with ``processed_at IS NULL``.

    Returns the number of videos processed (including undecodable ones, which
    are marked processed with empty analysis so the stage never loops on
    them). Safe without ML extras: the SigLIP embedding is best-effort.
    """
    from selects.indexer.preview import write_previews

    Session = init_db(cfg.db_path)

    with session_scope(Session) as s:
        pending = [
            (v.id, v.path, v.sha256)
            for v in s.query(Video).filter(Video.processed_at.is_(None)).all()
        ]

    total = len(pending)
    if not total:
        log.info("no videos pending analysis")
        return 0

    processed = 0
    for i, (vid, vpath, sha) in enumerate(pending, start=1):
        name = Path(vpath).name
        if on_progress:
            on_progress(i, total, name)
        try:
            analysis, arrays = analyze_video(Path(vpath), n=n_frames)
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
            v.frames_json = json.dumps([asdict(f) for f in analysis.frames])
            v.highlights_json = json.dumps(analysis.highlights)
            if siglip_blob is not None:
                v.siglip = siglip_blob
            v.processed_at = datetime.utcnow()
            s.add(v)

        processed += 1

    log.info("video analysis done: %d videos", processed)
    return processed
