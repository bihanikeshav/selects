"""Small JSON based FFprobe wrapper and normalized media metadata."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .runtime import FFmpegRuntime, resolve_ffmpeg


def _number(value: Any) -> float | None:
    try:
        return float(value) if value not in (None, "", "N/A") else None
    except (TypeError, ValueError):
        return None


def _fraction(value: Any) -> float | None:
    if not value or value == "N/A":
        return None
    try:
        if isinstance(value, str) and "/" in value:
            numerator, denominator = value.split("/", 1)
            return float(numerator) / float(denominator)
        return float(value)
    except (TypeError, ValueError, ZeroDivisionError):
        return None


@dataclass(frozen=True)
class VideoStream:
    codec: str | None = None
    profile: str | None = None
    level: int | None = None
    width: int | None = None
    height: int | None = None
    pixel_format: str | None = None
    frame_rate: float | None = None


@dataclass(frozen=True)
class AudioStream:
    codec: str | None = None
    sample_rate: int | None = None
    channels: int | None = None
    channel_layout: str | None = None


@dataclass
class MediaProbe:
    """Normalized probe result; ``error`` is populated on graceful failure."""

    path: Path
    format_name: str | None = None
    duration: float | None = None
    size: int | None = None
    bit_rate: int | None = None
    video: VideoStream | None = None
    audio: AudioStream | None = None
    streams: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None

    @property
    def available(self) -> bool:
        return self.error is None

    @property
    def has_video(self) -> bool:
        return self.video is not None

    @property
    def has_audio(self) -> bool:
        return self.audio is not None

    @property
    def duration_sec(self) -> float | None:
        return self.duration

    @property
    def container(self) -> str | None:
        return self.format_name

    @property
    def video_codec(self) -> str | None:
        return self.video.codec if self.video else None

    @property
    def audio_codec(self) -> str | None:
        return self.audio.codec if self.audio else None

    @property
    def width(self) -> int | None:
        return self.video.width if self.video else None

    @property
    def height(self) -> int | None:
        return self.video.height if self.video else None

    @property
    def frame_rate(self) -> float | None:
        return self.video.frame_rate if self.video else None

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "format_name": self.format_name,
            "duration": self.duration,
            "size": self.size,
            "bit_rate": self.bit_rate,
            "video": self.video.__dict__ if self.video else None,
            "audio": self.audio.__dict__ if self.audio else None,
            "streams": self.streams,
            "error": self.error,
        }


def _normalize(path: Path, payload: dict[str, Any]) -> MediaProbe:
    fmt = payload.get("format") or {}
    stream_rows = payload.get("streams") or []
    video_row = next((row for row in stream_rows if row.get("codec_type") == "video"), None)
    audio_row = next((row for row in stream_rows if row.get("codec_type") == "audio"), None)
    video = None
    if video_row:
        level = video_row.get("level")
        try:
            level = int(level) if level is not None else None
        except (TypeError, ValueError):
            level = None
        video = VideoStream(
            codec=video_row.get("codec_name"),
            profile=video_row.get("profile"),
            level=level,
            width=video_row.get("width"),
            height=video_row.get("height"),
            pixel_format=video_row.get("pix_fmt"),
            frame_rate=_fraction(video_row.get("avg_frame_rate") or video_row.get("r_frame_rate")),
        )
    audio = None
    if audio_row:
        try:
            sample_rate = int(audio_row["sample_rate"]) if audio_row.get("sample_rate") else None
        except (TypeError, ValueError):
            sample_rate = None
        audio = AudioStream(
            codec=audio_row.get("codec_name"),
            sample_rate=sample_rate,
            channels=audio_row.get("channels"),
            channel_layout=audio_row.get("channel_layout"),
        )
    return MediaProbe(
        path=path,
        format_name=fmt.get("format_name"),
        duration=_number(fmt.get("duration")),
        size=int(fmt["size"]) if str(fmt.get("size", "")).isdigit() else None,
        bit_rate=int(fmt["bit_rate"]) if str(fmt.get("bit_rate", "")).isdigit() else None,
        video=video,
        audio=audio,
        streams=stream_rows,
    )


def probe_media(path: str | Path, *, runtime: FFmpegRuntime | None = None) -> MediaProbe:
    """Probe media, returning a result with ``error`` when unavailable."""

    source = Path(path)
    resolved = runtime or resolve_ffmpeg()
    if resolved.ffprobe is None:
        return MediaProbe(source, error="ffprobe is not available")
    command = [
        str(resolved.ffprobe),
        "-v", "error",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        str(source),
    ]
    try:
        completed = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            shell=False,
        )
        payload = json.loads(completed.stdout)
        if not isinstance(payload, dict):
            raise ValueError("ffprobe returned a non-object JSON payload")
        return _normalize(source, payload)
    except (OSError, subprocess.SubprocessError, ValueError, json.JSONDecodeError) as exc:
        return MediaProbe(source, error=f"ffprobe failed: {exc}")
