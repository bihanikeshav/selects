"""On-demand browser-compatible proxy generation."""

from __future__ import annotations

import hashlib
import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from threading import Event
from typing import Callable, Any

from .probe import probe_media
from .runtime import FFmpegRuntime, resolve_ffmpeg, spawn_ffmpeg


@dataclass(frozen=True)
class ProxyProgress:
    fraction: float | None
    elapsed_seconds: float | None = None
    duration: float | None = None
    status: str = "running"


@dataclass(frozen=True)
class ProxyResult:
    source: Path
    path: Path | None
    cached: bool = False
    cancelled: bool = False
    available: bool = True
    error: str | None = None

    @property
    def output_path(self) -> Path | None:
        return self.path

    @property
    def success(self) -> bool:
        return self.path is not None and not self.cancelled and self.error is None


ProgressCallback = Callable[[ProxyProgress], Any]


def proxy_cache_path(
    source: str | Path,
    cache_dir: str | Path,
    *,
    preset: str = "veryfast",
    video_crf: int = 23,
    audio_bitrate: str = "128k",
) -> Path:
    """Return a stable cache path without touching the source file."""

    source_path = Path(source).expanduser().resolve()
    try:
        stat = source_path.stat()
        identity = f"{source_path}\0{stat.st_size}\0{stat.st_mtime_ns}\0{preset}\0{video_crf}\0{audio_bitrate}"
    except OSError:
        identity = f"{source_path}\0{preset}\0{video_crf}\0{audio_bitrate}"
    key = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    return Path(cache_dir).expanduser() / f"{key}.mp4"


def _cancelled(signal: Event | Callable[[], bool] | None) -> bool:
    if signal is None:
        return False
    return signal.is_set() if hasattr(signal, "is_set") else bool(signal())


def _emit(callback: ProgressCallback | None, progress: ProxyProgress) -> None:
    if callback:
        callback(progress)


def _parse_progress(line: str) -> tuple[float | None, float | None]:
    key, separator, value = line.partition("=")
    if not separator:
        return None, None
    if key not in {"out_time_ms", "out_time_us", "out_time"}:
        return None, None
    try:
        if key == "out_time":
            hours, minutes, seconds = value.split(":")
            elapsed = float(hours) * 3600 + float(minutes) * 60 + float(seconds)
        else:
            # FFmpeg labels this in microseconds in current releases, despite
            # older builds/documentation using the ``_ms`` spelling.
            elapsed = float(value) / 1_000_000
        return elapsed, None
    except (ValueError, TypeError):
        return None, None


def generate_proxy(
    source: str | Path,
    cache_dir: str | Path,
    *,
    runtime: FFmpegRuntime | None = None,
    cancel: Event | Callable[[], bool] | None = None,
    progress: ProgressCallback | None = None,
    force: bool = False,
    preset: str = "veryfast",
    video_crf: int = 23,
    audio_bitrate: str = "128k",
) -> ProxyResult:
    """Create an H.264/AAC MP4 proxy, atomically and with progress updates.

    A completed cache file is returned immediately.  FFmpeg absence, source
    errors, non-zero encoding exits, and cancellation are represented in the
    result; no exception is required for a request handler to recover.
    """

    source_path = Path(source).expanduser().resolve()
    target = proxy_cache_path(
        source_path,
        cache_dir,
        preset=preset,
        video_crf=video_crf,
        audio_bitrate=audio_bitrate,
    )
    if not force and target.is_file() and target.stat().st_size > 0:
        _emit(progress, ProxyProgress(1.0, status="cached"))
        return ProxyResult(source_path, target, cached=True)
    resolved = runtime or resolve_ffmpeg()
    if _cancelled(cancel):
        _emit(progress, ProxyProgress(None, duration=None, status="cancelled"))
        return ProxyResult(source_path, None, cancelled=True)
    if resolved.ffmpeg is None:
        return ProxyResult(source_path, None, available=False, error="ffmpeg is not available")
    if not source_path.is_file():
        return ProxyResult(source_path, None, error="source media does not exist")

    duration = probe_media(source_path, runtime=resolved).duration
    target.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Path | None = None
    process: subprocess.Popen[str] | None = None
    try:
        fd, temporary = tempfile.mkstemp(prefix=f".{target.stem}-", suffix=".mp4", dir=target.parent)
        os.close(fd)
        temp_path = Path(temporary)
        command = [
            "-hide_banner", "-loglevel", "error",
            "-i", source_path,
            "-map", "0:v:0?", "-map", "0:a:0?",
            "-c:v", "libx264", "-preset", preset, "-crf", video_crf,
            "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", audio_bitrate,
            "-movflags", "+faststart", "-progress", "pipe:1", "-nostats", "-y", temp_path,
        ]
        process = spawn_ffmpeg(
            command,
            runtime=resolved,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
        )
        _emit(progress, ProxyProgress(0.0, duration=duration))
        assert process.stdout is not None
        for line in process.stdout:
            if _cancelled(cancel):
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                _emit(progress, ProxyProgress(None, duration=duration, status="cancelled"))
                return ProxyResult(source_path, None, cancelled=True)
            elapsed, _ = _parse_progress(line.strip())
            fraction = min(1.0, elapsed / duration) if elapsed is not None and duration and duration > 0 else None
            if elapsed is not None or line.startswith("progress="):
                _emit(progress, ProxyProgress(fraction, elapsed, duration))
        return_code = process.wait()
        if return_code != 0:
            return ProxyResult(source_path, None, error=f"ffmpeg exited with status {return_code}")
        if not temp_path.is_file() or temp_path.stat().st_size == 0:
            return ProxyResult(source_path, None, error="ffmpeg produced no proxy output")
        os.replace(temp_path, target)
        temp_path = None
        _emit(progress, ProxyProgress(1.0, duration, duration, "complete"))
        return ProxyResult(source_path, target)
    except (OSError, subprocess.SubprocessError) as exc:
        if process is not None and process.poll() is None:
            process.kill()
            process.wait()
        return ProxyResult(source_path, None, error=f"proxy generation failed: {exc}")
    finally:
        if temp_path is not None:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass


create_proxy = generate_proxy
get_or_create_proxy = generate_proxy
