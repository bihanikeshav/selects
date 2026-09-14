"""Low-cost stabilization analysis and FFmpeg filter planning."""
from __future__ import annotations

import importlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Literal

import numpy as np


Strength = Literal["low", "medium", "high"]


@dataclass(frozen=True)
class StabilizationAnalysis:
    motion_score: float
    sample_count: int
    method: str
    suggested: bool
    note: str = ""


def _runtime_binary(name: str, fallback: str) -> str:
    try:
        runtime = importlib.import_module("selects.media.runtime")
    except ImportError:
        return fallback
    for attr in (f"resolve_{name}", f"get_{name}", name):
        value = getattr(runtime, attr, None)
        if callable(value):
            try:
                resolved = value()
            except TypeError:
                resolved = value
            if resolved:
                resolved = getattr(resolved, name, resolved)
                if resolved:
                    return os.fspath(resolved)
        elif value:
            return os.fspath(value)
    resolver = getattr(runtime, "resolve_ffmpeg", None)
    if callable(resolver):
        resolved = getattr(resolver(), name, None)
        if resolved:
            return os.fspath(resolved)
    return fallback


def motion_score(frames: Iterable[np.ndarray]) -> tuple[float, int]:
    """Return normalized inter-frame motion using tiny grayscale comparisons."""
    previous: np.ndarray | None = None
    deltas: list[float] = []
    count = 0
    for frame in frames:
        array = np.asarray(frame)
        if array.size == 0:
            continue
        if array.ndim == 3:
            gray = array.astype(np.float32).mean(axis=2)
        else:
            gray = array.astype(np.float32)
        # Keep the analysis cheap and stable across resolutions.
        stride_y = max(1, gray.shape[0] // 64)
        stride_x = max(1, gray.shape[1] // 64)
        gray = gray[::stride_y, ::stride_x]
        if previous is not None:
            height = min(previous.shape[0], gray.shape[0])
            width = min(previous.shape[1], gray.shape[1])
            deltas.append(float(np.abs(gray[:height, :width] - previous[:height, :width]).mean() / 255.0))
        previous = gray
        count += 1
    return (float(np.mean(deltas)) if deltas else 0.0, count)


def analyze_stabilization(
    source: str | os.PathLike[str] | None = None,
    *,
    frames: Iterable[np.ndarray] | None = None,
    max_samples: int = 24,
    threshold: float = 0.08,
) -> StabilizationAnalysis:
    """Analyze camera motion without producing a derived media file.

    Passing ``frames`` makes the function deterministic and testable without
    OpenCV or FFmpeg.  When frames are omitted, OpenCV decodes at most
    ``max_samples`` low-resolution frames.
    """
    if max_samples < 2:
        raise ValueError("max_samples must be at least 2")
    if frames is None:
        if source is None:
            raise ValueError("source or frames is required")
        try:
            import cv2
        except ImportError:
            return StabilizationAnalysis(0.0, 0, "fallback", False, "OpenCV is unavailable")
        capture = cv2.VideoCapture(str(source))
        decoded: list[np.ndarray] = []
        try:
            if not capture.isOpened():
                return StabilizationAnalysis(0.0, 0, "fallback", False, "video could not be opened")
            total = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
            indices = np.linspace(0, max(0, total - 1), num=max_samples, dtype=int) if total else np.arange(max_samples)
            for index in dict.fromkeys(int(value) for value in indices):
                capture.set(cv2.CAP_PROP_POS_FRAMES, index)
                ok, frame = capture.read()
                if ok and frame is not None:
                    decoded.append(frame)
        finally:
            capture.release()
        frames = decoded
    score, count = motion_score(frames)
    return StabilizationAnalysis(
        motion_score=score,
        sample_count=count,
        method="opencv-diff" if count else "fallback",
        suggested=score >= threshold and count >= 2,
        note="motion exceeds the default threshold" if score >= threshold else "no strong camera motion detected",
    )


def _crop_filter(crop: float) -> str:
    if crop <= 0:
        return ""
    scale = 1.0 - 2.0 * crop
    return f"crop=iw*{scale:.6f}:ih*{scale:.6f}:iw*{crop:.6f}:ih*{crop:.6f},scale=iw/{scale:.6f}:ih/{scale:.6f}"


def stabilization_filter(
    strength: Strength = "medium",
    crop: float = 0.08,
    *,
    transform_path: str | os.PathLike[str] | None = None,
    prefer_vidstab: bool = True,
) -> str:
    """Build an FFmpeg video filter, preferring cached vidstab transforms."""
    if strength not in {"low", "medium", "high"}:
        raise ValueError("strength must be low, medium, or high")
    if not 0 <= crop < 0.5:
        raise ValueError("crop must be between 0 and 0.5")
    smoothing = {"low": 12, "medium": 24, "high": 42}[strength]
    zoom = {"low": 2, "medium": 5, "high": 8}[strength]
    if prefer_vidstab and transform_path:
        filters = [f"vidstabtransform=input={Path(transform_path)}:smoothing={smoothing}:zoom={zoom}:optzoom=1"]
    else:
        # deshake is available in ordinary FFmpeg builds and needs no sidecar.
        radius = {"low": 16, "medium": 32, "high": 48}[strength]
        filters = [f"deshake=rx={radius}:ry={radius}:edge=mirror"]
    crop_expr = _crop_filter(crop)
    if crop_expr:
        filters.append(crop_expr)
    return ",".join(filters)


def build_stabilization_detect_command(
    source: str | os.PathLike[str],
    transform_path: str | os.PathLike[str],
    *,
    ffmpeg: str | None = None,
) -> list[str]:
    """Build the optional first pass for FFmpeg's vidstab filter."""
    return [
        ffmpeg or _runtime_binary("ffmpeg", "ffmpeg"),
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        os.fspath(source),
        "-vf",
        "vidstabdetect=shakiness=5:accuracy=9:result=" + os.fspath(transform_path),
        "-f",
        "null",
        "-",
    ]


def build_stabilization_preview_command(
    source: str | os.PathLike[str],
    output: str | os.PathLike[str],
    *,
    strength: Strength = "medium",
    crop: float = 0.08,
    ffmpeg: str | None = None,
    transform_path: str | os.PathLike[str] | None = None,
) -> list[str]:
    """Build a low-resolution preview command without modifying ``source``."""
    return [
        ffmpeg or _runtime_binary("ffmpeg", "ffmpeg"),
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        os.fspath(source),
        "-vf",
        stabilization_filter(strength, crop, transform_path=transform_path)
        + ",scale='min(1280,iw)':-2",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-an",
        os.fspath(output),
    ]
