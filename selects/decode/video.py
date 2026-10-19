from __future__ import annotations

import subprocess
from dataclasses import dataclass
from datetime import datetime
import logging
from pathlib import Path

import numpy as np

from selects.decode.video_dates import parse_creation_time, read_mp4_creation_time

log = logging.getLogger(__name__)

@dataclass
class VideoMeta:
    width: int
    height: int
    duration_sec: float
    codec: str
    taken_at: datetime | None = None
    metadata_error: str | None = None


_PROBE_FAIL = (OSError, subprocess.SubprocessError, ValueError, KeyError, IndexError)


def probe(path: Path) -> VideoMeta:
    try:
        return _probe_ffprobe(path)
    except _PROBE_FAIL:
        return _probe_cv2(path)


def decode_first_frame(path: Path) -> np.ndarray:
    """Decode a single representative frame via ffmpeg, with OpenCV fallback."""
    try:
        meta = _probe_ffprobe(path)
        return _decode_ffmpeg(path, meta)
    except _PROBE_FAIL:
        return _decode_cv2(path)


def _parse_duration(value: str) -> float | None:
    if not value or value.upper() == "N/A":
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _probe_ffprobe(path: Path) -> VideoMeta:
    cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", (
            "stream=width,height,codec_name,duration:format=duration:"
            "stream_tags=creation_time:format_tags=creation_time,com.apple.quicktime.creationdate"
        ),
        "-of", "default=noprint_wrappers=1:nokey=0",
        str(path),
    ]
    out = subprocess.check_output(cmd, text=True)
    kv: dict[str, str] = {}
    durations: list[str] = []
    dates: list[datetime] = []
    for line in out.strip().splitlines():
        k, _, v = line.partition("=")
        k, v = k.strip(), v.strip()
        if k == "duration":
            durations.append(v)
        elif k in {"TAG:creation_time", "TAG:com.apple.quicktime.creationdate"}:
            if (date := parse_creation_time(v)) is not None:
                dates.append(date)
        else:
            kv[k] = v
    parsed = [d for d in (_parse_duration(v) for v in durations) if d is not None]
    return VideoMeta(
        width=int(kv["width"]),
        height=int(kv["height"]),
        duration_sec=parsed[0] if parsed else 0.0,
        codec=kv.get("codec_name", "unknown"),
        # Stream tags precede format tags; prefer the container's date.
        taken_at=dates[-1] if dates else None,
    )


def _probe_cv2(path: Path) -> VideoMeta:
    import cv2

    cap = cv2.VideoCapture(str(path))
    try:
        if not cap.isOpened():
            raise RuntimeError(f"cv2 cannot open {path}")
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
        frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        duration = frame_count / fps if fps > 0 and frame_count > 0 else 0.0
        fourcc = int(cap.get(cv2.CAP_PROP_FOURCC) or 0)
        codec = "".join(chr((fourcc >> (8 * i)) & 0xFF) for i in range(4)).strip("\x00 ") or "unknown"
        taken_at = None
        metadata_error = None
        try:
            taken_at = read_mp4_creation_time(path)
        except (OSError, ValueError) as exc:
            metadata_error = type(exc).__name__
            log.warning("Could not read video date (%s)", metadata_error)
        return VideoMeta(
            width=width, height=height, duration_sec=duration, codec=codec,
            taken_at=taken_at, metadata_error=metadata_error,
        )
    finally:
        cap.release()


def _decode_ffmpeg(path: Path, meta: VideoMeta) -> np.ndarray:
    # -noautorotate keeps the buffer aligned with ffprobe coded width/height.
    # ffmpeg otherwise auto-rotates phone video while probe still reports coded size.
    cmd = [
        "ffmpeg", "-v", "error",
        "-noautorotate",
        "-i", str(path),
        "-frames:v", "1",
        "-f", "image2pipe",
        "-pix_fmt", "rgb24",
        "-vcodec", "rawvideo",
        "-",
    ]
    raw = subprocess.check_output(cmd)
    return _reshape_rgb(raw, meta.width, meta.height)


def _reshape_rgb(raw: bytes, width: int, height: int) -> np.ndarray:
    arr = np.frombuffer(raw, dtype=np.uint8)
    expected = height * width * 3
    if arr.size == expected:
        return arr.reshape(height, width, 3).copy()
    if arr.size % 3 != 0:
        raise ValueError("ffmpeg frame is not RGB24")
    pixels = arr.size // 3
    if width > 0 and pixels % width == 0:
        return arr.reshape(pixels // width, width, 3).copy()
    if height > 0 and pixels % height == 0:
        return arr.reshape(height, pixels // height, 3).copy()
    raise ValueError(f"cannot reshape ffmpeg frame of {arr.size} bytes to {width}x{height}")


def _decode_cv2(path: Path) -> np.ndarray:
    import cv2

    cap = cv2.VideoCapture(str(path))
    try:
        if not cap.isOpened():
            raise RuntimeError(f"cv2 cannot open {path}")
        ok, bgr = cap.read()
        if not ok or bgr is None:
            raise RuntimeError(f"cv2 cannot read frame from {path}")
        return cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    finally:
        cap.release()
