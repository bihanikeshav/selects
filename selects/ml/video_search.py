"""Low-compute visual indexing and search helpers for videos.

The index deliberately stays sparse: uniform anchors provide coverage, while
cheap low-resolution scene differences add frames around cuts.  SigLIP is run
only on the selected representatives and pooled segment vectors are used for
the first search pass.
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import numpy as np

from selects.db import init_db, session_scope

log = logging.getLogger(__name__)

VIDEO_INDEX_VERSION = "video-search-v1"
VIDEO_MODEL_VERSION = "siglip-v1"
VIDEO_PROCESSOR_VERSION = "adaptive-scenes-v1"
SHORT_VIDEO_SECONDS = 60.0
MEDIUM_VIDEO_SECONDS = 600.0
SHORT_VIDEO_CAP = 24
MEDIUM_VIDEO_CAP = 48
LONG_VIDEO_CAP = 64
SCENE_SCAN_FPS = 1.0
SCENE_THRESHOLD = 0.22
SEGMENT_GROUP_SIZE = 4


@dataclass(frozen=True)
class SampledFrame:
    frame_index: int
    t_sec: float
    image: np.ndarray
    kind: str
    scene_hash: str
    difference: float = 0.0


@dataclass(frozen=True)
class SegmentPlan:
    start_sec: float
    end_sec: float
    frame_indexes: tuple[int, ...]
    kind: str = "scene"


def frame_cap(duration_sec: float) -> int:
    """Return the hard keyframe cap for a video duration."""
    if duration_sec <= SHORT_VIDEO_SECONDS:
        return SHORT_VIDEO_CAP
    if duration_sec <= MEDIUM_VIDEO_SECONDS:
        return MEDIUM_VIDEO_CAP
    return LONG_VIDEO_CAP


def _small_gray(image: np.ndarray) -> np.ndarray:
    import cv2

    small = cv2.resize(image, (64, 36), interpolation=cv2.INTER_AREA)
    return cv2.cvtColor(small, cv2.COLOR_RGB2GRAY)


def perceptual_difference(previous: np.ndarray, current: np.ndarray) -> float:
    """Cheap [0, 1] scene difference from grayscale pixels and histograms."""
    import cv2

    a = _small_gray(previous)
    b = _small_gray(current)
    pixel_delta = float(np.mean(np.abs(a.astype(np.float32) - b.astype(np.float32))) / 255.0)
    hist_a = cv2.calcHist([a], [0], None, [32], [0, 256])
    hist_b = cv2.calcHist([b], [0], None, [32], [0, 256])
    cv2.normalize(hist_a, hist_a)
    cv2.normalize(hist_b, hist_b)
    hist_delta = float(cv2.compareHist(hist_a, hist_b, cv2.HISTCMP_BHATTACHARYYA))
    return float(min(1.0, 0.65 * pixel_delta + 0.35 * hist_delta))


def scene_hash(image: np.ndarray) -> str:
    """Stable hash of a tiny grayscale image used for cache/debug metadata."""
    return hashlib.sha1(_small_gray(image).tobytes()).hexdigest()[:16]


def source_fingerprint(sha256: str) -> str:
    """Identify one source plus the sampling policy used to derive its rows."""
    payload = f"{sha256}:{VIDEO_INDEX_VERSION}:{VIDEO_PROCESSOR_VERSION}:{VIDEO_MODEL_VERSION}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _scan_frames(path: Path, scan_fps: float = SCENE_SCAN_FPS) -> tuple[Any, list[tuple[int, float, np.ndarray]]]:
    """Decode only low-rate candidates; no ML work happens in this pass."""
    import cv2

    cap = cv2.VideoCapture(str(path))
    try:
        if not cap.isOpened():
            from selects.video import VideoDecodeError

            raise VideoDecodeError(f"cv2 cannot open {path}")
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
        count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        duration = count / fps if fps > 0 and count > 0 else 0.0
        stride = max(1, int(round(fps / max(scan_fps, 0.1)))) if fps else 1
        rows: list[tuple[int, float, np.ndarray]] = []
        idx = 0
        while True:
            ok, bgr = cap.read()
            if not ok or bgr is None:
                break
            if idx % stride == 0:
                rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
                height_px, width_px = rgb.shape[:2]
                scale = min(1.0, 256.0 / max(height_px, width_px, 1))
                if scale < 1.0:
                    rgb = cv2.resize(rgb, (max(1, int(width_px * scale)), max(1, int(height_px * scale))), interpolation=cv2.INTER_AREA)
                rows.append((idx, idx / fps if fps else float(len(rows)), rgb))
            idx += 1
        if not count:
            count = idx
            duration = count / fps if fps > 0 else (rows[-1][1] if rows else 0.0)
        from selects.video import VideoInfo

        return VideoInfo(width=width, height=height, fps=fps, frame_count=count, duration_sec=duration), rows
    finally:
        cap.release()


def _nearest(rows: list[tuple[int, float, np.ndarray]], target: float) -> tuple[int, float, np.ndarray]:
    return min(rows, key=lambda row: abs(row[1] - target))


def select_adaptive_frames(
    rows: list[tuple[int, float, np.ndarray]],
    duration_sec: float,
    cap: int | None = None,
    scene_threshold: float = SCENE_THRESHOLD,
) -> list[SampledFrame]:
    """Select uniform anchors plus scene changes, bounded by ``cap``."""
    if not rows:
        return []
    cap = cap or frame_cap(duration_sec)
    anchors = min(12, cap, len(rows))
    targets = np.linspace(rows[0][1], rows[-1][1], anchors)
    chosen: dict[int, SampledFrame] = {}
    for target in targets:
        frame_index, t_sec, image = _nearest(rows, float(target))
        chosen[frame_index] = SampledFrame(
            frame_index, t_sec, image, "anchor", scene_hash(image)
        )

    changes: list[tuple[float, int, float, np.ndarray]] = []
    for previous, current in zip(rows, rows[1:]):
        difference = perceptual_difference(previous[2], current[2])
        if difference >= scene_threshold:
            changes.append((difference, current[0], current[1], current[2]))
    changes.sort(reverse=True, key=lambda item: item[0])
    for difference, frame_index, t_sec, image in changes:
        if len(chosen) >= cap:
            break
        chosen[frame_index] = SampledFrame(
            frame_index, t_sec, image, "scene", scene_hash(image), difference
        )

    ordered = sorted(chosen.values(), key=lambda frame: frame.t_sec)
    if len(ordered) > cap:
        # Keep temporal coverage when there are more anchors than the cap.
        indices = np.linspace(0, len(ordered) - 1, cap).round().astype(int)
        ordered = [ordered[int(i)] for i in sorted(set(indices))]
    return ordered


def adaptive_keyframes(path: Path, duration_sec: float | None = None) -> tuple[Any, list[SampledFrame]]:
    """Return low-rate scene/anchor candidates and never invoke SigLIP."""
    info, rows = _scan_frames(path)
    duration = duration_sec if duration_sec is not None else info.duration_sec
    return info, select_adaptive_frames(rows, duration)


def build_segment_plan(frames: list[SampledFrame], duration_sec: float) -> list[SegmentPlan]:
    """Group adjacent selected frames into small temporal search segments."""
    if not frames:
        return []
    plans: list[SegmentPlan] = []
    for start in range(0, len(frames), SEGMENT_GROUP_SIZE):
        group = frames[start:start + SEGMENT_GROUP_SIZE]
        end = frames[start + SEGMENT_GROUP_SIZE].t_sec if start + SEGMENT_GROUP_SIZE < len(frames) else duration_sec
        end = max(group[-1].t_sec, float(end))
        plans.append(SegmentPlan(group[0].t_sec, end, tuple(frame.frame_index for frame in group)))
    return plans


def encode_keyframes(frames: Iterable[SampledFrame], batch_size: int = 16) -> list[bytes | None]:
    """Batch selected frames through the existing ONNX SigLIP image encoder."""
    from PIL import Image
    from selects.ml.embed import encode_image_batch
    from selects.ml.onnx_rt import all_present

    selected = list(frames)
    output: list[bytes | None] = [None] * len(selected)
    if not all_present():
        log.info("SigLIP model bundle is not installed; keeping video keyframes without embeddings")
        return output
    for start in range(0, len(selected), batch_size):
        chunk = selected[start:start + batch_size]
        try:
            features, _ = encode_image_batch([Image.fromarray(frame.image) for frame in chunk])
            for offset, feature in enumerate(features):
                output[start + offset] = feature.astype(np.float16).tobytes()
        except Exception as exc:  # ML extras are optional for video analysis.
            log.warning("video keyframe embedding batch failed: %s", exc)
            break
    return output


def _model_classes() -> tuple[Any | None, Any | None]:
    from selects.db import models

    return getattr(models, "VideoKeyframe", None), getattr(models, "VideoSegment", None)


def persist_index(
    cfg: Any,
    video_id: int,
    sha256: str,
    path: Path,
    duration_sec: float,
    embed: bool = True,
    best_frame_index: int | None = None,
) -> int:
    """Persist sparse keyframes and pooled segments when the schema is present."""
    keyframe_model, segment_model = _model_classes()
    if keyframe_model is None or segment_model is None:
        return 0
    from selects.indexer.preview import _resize_and_save

    try:
        _info, frames = adaptive_keyframes(path, duration_sec)
        if not frames:
            return 0
        blobs = encode_keyframes(frames) if embed else [None] * len(frames)
        out_dir = cfg.state_dir / "video_frames" / sha256 / "keyframes"
        out_dir.mkdir(parents=True, exist_ok=True)
        plans = build_segment_plan(frames, duration_sec)
        fingerprint = source_fingerprint(sha256)
        Session = init_db(cfg.db_path)
        with session_scope(Session) as s:
            s.query(segment_model).filter(segment_model.video_id == video_id).delete(synchronize_session=False)
            s.query(keyframe_model).filter(keyframe_model.video_id == video_id).delete(synchronize_session=False)
            rows = []
            for ordinal, (frame, blob) in enumerate(zip(frames, blobs)):
                image_path = out_dir / f"{ordinal:03d}.jpg"
                _resize_and_save(frame.image, 512, image_path)
                sharpness, exposure, quality, _good = _score_selected_frame(frame.image)
                selected_best = (
                    best_frame_index is not None
                    and frame.frame_index == min(frames, key=lambda item: abs(item.frame_index - best_frame_index)).frame_index
                )
                row = keyframe_model(
                    video_id=video_id,
                    frame_index=frame.frame_index,
                    timestamp_ms=int(round(frame.t_sec * 1000.0)),
                    kind="quality" if selected_best else frame.kind,
                    quality=quality,
                    sharpness=sharpness,
                    exposure=exposure,
                    image_path=str(image_path.relative_to(cfg.state_dir)),
                    siglip=blob,
                    source_fingerprint=fingerprint,
                    processor_version=VIDEO_PROCESSOR_VERSION,
                    index_version=VIDEO_INDEX_VERSION,
                    model_version=VIDEO_MODEL_VERSION,
                )
                rows.append(row)
            s.add_all(rows)
            s.flush()
            for plan in plans:
                group_rows = [row for row, frame in zip(rows, frames) if frame.frame_index in plan.frame_indexes]
                group_blobs = [blob for blob, frame in zip(blobs, frames) if frame.frame_index in plan.frame_indexes and blob]
                if not group_blobs:
                    continue
                matrix = np.stack([np.frombuffer(blob, dtype=np.float16).astype(np.float32) for blob in group_blobs])
                pooled = matrix.mean(axis=0)
                pooled /= np.linalg.norm(pooled) + 1e-12
                s.add(segment_model(
                    video_id=video_id,
                    start_ms=int(round(plan.start_sec * 1000.0)),
                    end_ms=int(round(plan.end_sec * 1000.0)),
                    representative_keyframe_id=group_rows[0].id,
                    kind=plan.kind,
                    score=float(max((row.quality or 0.0) for row in group_rows)),
                    embedding=pooled.astype(np.float16).tobytes(),
                    source_fingerprint=fingerprint,
                    processor_version=VIDEO_PROCESSOR_VERSION,
                    index_version=VIDEO_INDEX_VERSION,
                    model_version=VIDEO_MODEL_VERSION,
                ))
        clear_segment_cache(str(cfg.db_path))
        return len(frames)
    except Exception as exc:  # Schema/runtime upgrades should not break baseline culling.
        log.warning("sparse video search index skipped for %s: %s", path, exc)
        return 0


def _score_selected_frame(image: np.ndarray) -> tuple[float, float, float, bool]:
    """Keep child-row quality consistent with the baseline video analysis."""
    from selects.video import score_frame

    return score_frame(image)


_SEGMENT_CACHE: dict[str, tuple[tuple[int, tuple[str, ...]], np.ndarray, list[dict[str, Any]]]] = {}


def clear_segment_cache(db_key: str | None = None) -> None:
    if db_key is None:
        _SEGMENT_CACHE.clear()
    else:
        _SEGMENT_CACHE.pop(db_key, None)


def segment_matrix(cfg: Any) -> tuple[np.ndarray, list[dict[str, Any]]]:
    """Return cached normalized segment vectors and metadata.

    The cache signature includes both row count and versions, so replacing an
    index in place cannot leave stale vectors resident.
    """
    _keyframe_model, segment_model = _model_classes()
    if segment_model is None:
        return np.zeros((0, 0), dtype=np.float32), []
    Session = init_db(cfg.db_path)
    try:
        with session_scope(Session) as s:
            rows = s.query(segment_model).all()
            versions = tuple(sorted(
                f"{getattr(row, 'index_version', '')}:{getattr(row, 'model_version', '')}"
                for row in rows
            ))
            signature = (len(rows), versions)
            cached = _SEGMENT_CACHE.get(str(cfg.db_path))
            if cached is not None and cached[0] == signature:
                return cached[1], cached[2]
            valid = [
                row for row in rows
                if getattr(row, "embedding", None)
                and getattr(row, "index_version", None) == VIDEO_INDEX_VERSION
                and getattr(row, "model_version", None) == VIDEO_MODEL_VERSION
            ]
            if not valid:
                empty = np.zeros((0, 0), dtype=np.float32)
                _SEGMENT_CACHE[str(cfg.db_path)] = (signature, empty, [])
                return empty, []
            video_ids = {row.video_id for row in valid}
            from selects.db.models import Video

            videos = {v.id: v for v in s.query(Video).filter(Video.id.in_(video_ids)).all()}
            matrix = np.stack([np.frombuffer(row.embedding, dtype=np.float16).astype(np.float32) for row in valid])
            matrix /= np.linalg.norm(matrix, axis=1, keepdims=True) + 1e-12
            metadata = [
                {
                    "segment_id": row.id,
                    "video_id": row.video_id,
                    "sha256": getattr(videos.get(row.video_id), "sha256", None),
                    "start_sec": float(row.start_ms) / 1000.0,
                    "end_sec": float(row.end_ms) / 1000.0,
                    "kind": getattr(row, "kind", "scene"),
                }
                for row in valid
            ]
        _SEGMENT_CACHE[str(cfg.db_path)] = (signature, matrix, metadata)
        return matrix, metadata
    except Exception as exc:
        log.debug("video segment matrix unavailable: %s", exc)
        return np.zeros((0, 0), dtype=np.float32), []


def best_frame_matrix(cfg: Any) -> tuple[np.ndarray, list[dict[str, Any]]]:
    """Load existing ``videos.siglip`` vectors for immediate video search."""
    from selects.db.models import Video

    Session = init_db(cfg.db_path)
    try:
        with session_scope(Session) as s:
            rows = s.query(Video.id, Video.sha256, Video.siglip).filter(Video.siglip.isnot(None)).all()
        valid = [(vid, sha, blob) for vid, sha, blob in rows if blob]
        if not valid:
            return np.zeros((0, 0), dtype=np.float32), []
        dims = [len(blob) // 2 for _, _, blob in valid]
        common_dim = max(set(dims), key=dims.count)
        valid = [row for row, dim in zip(valid, dims) if dim == common_dim]
        matrix = np.stack([np.frombuffer(blob, dtype=np.float16).astype(np.float32) for _, _, blob in valid])
        matrix /= np.linalg.norm(matrix, axis=1, keepdims=True) + 1e-12
        return matrix, [{"video_id": vid, "sha256": sha} for vid, sha, _ in valid]
    except Exception as exc:
        log.debug("video best-frame matrix unavailable: %s", exc)
        return np.zeros((0, 0), dtype=np.float32), []


def score_video_search(cfg: Any, query_vec: np.ndarray, limit: int = 120) -> list[dict[str, Any]]:
    """Return one result per video, preferring timestamped segments when indexed."""
    query_vec = np.asarray(query_vec, dtype=np.float32)
    query_vec = query_vec / (np.linalg.norm(query_vec) + 1e-12)
    results: dict[int, dict[str, Any]] = {}
    best_matrix, best_meta = best_frame_matrix(cfg)
    if len(best_meta) and best_matrix.shape[1] == query_vec.shape[0]:
        scores = best_matrix @ query_vec
        for meta, score in zip(best_meta, scores):
            results[meta["video_id"]] = {
                "asset_type": "video",
                "video_id": meta["video_id"],
                "sha256": meta["sha256"],
                "score": float(score),
                "semantic_score": float(score),
                "match_start_sec": None,
                "match_end_sec": None,
                "match_kind": "best-frame",
            }

    matrix, metadata = segment_matrix(cfg)
    if len(metadata) and matrix.shape[1] == query_vec.shape[0]:
        scores = matrix @ query_vec
        grouped: dict[int, list[tuple[float, dict[str, Any]]]] = {}
        for meta, score in zip(metadata, scores):
            grouped.setdefault(meta["video_id"], []).append((float(score), meta))
        for video_id, matches in grouped.items():
            matches.sort(key=lambda item: item[0], reverse=True)
            top = [score for score, _ in matches[:3]]
            refined = 0.8 * top[0] + 0.2 * float(np.mean(top))
            score, meta = matches[0]
            previous = results.get(video_id)
            if previous is None or refined >= previous["score"]:
                results[video_id] = {
                    "asset_type": "video",
                    "video_id": video_id,
                    "sha256": meta["sha256"],
                    "score": float(refined),
                    "semantic_score": float(refined),
                    "match_start_sec": meta["start_sec"],
                    "match_end_sec": meta["end_sec"],
                    "match_kind": "segment",
                }
    return sorted(results.values(), key=lambda result: result["score"], reverse=True)[:limit]
