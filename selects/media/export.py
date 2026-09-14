"""Safe FFmpeg command construction and atomic video export."""
from __future__ import annotations

import importlib
import json
import os
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from .edits import EditRecipe, EditRecipeError
from .stabilize import stabilization_filter


class ExportError(RuntimeError):
    """Base class for safe video export failures."""


class ExportValidationError(ExportError):
    """Raised when FFmpeg did not produce a valid output file."""


def _runtime_binary(name: str, fallback: str) -> str:
    try:
        runtime = importlib.import_module("selects.media.runtime")
    except ImportError:
        return fallback
    for attr in (f"resolve_{name}", f"get_{name}", name):
        value = getattr(runtime, attr, None)
        if callable(value):
            try:
                value = value()
            except TypeError:
                pass
            value = getattr(value, name, value)
        if value:
            return os.fspath(value)
    resolver = getattr(runtime, "resolve_ffmpeg", None)
    if callable(resolver):
        value = getattr(resolver(), name, None)
        if value:
            return os.fspath(value)
    return fallback


def _seconds(value: float) -> str:
    return f"{value:.6f}".rstrip("0").rstrip(".") or "0"


def _quality_args(recipe: EditRecipe) -> list[str]:
    quality = recipe.export.quality
    crf = {"source": "18", "high": "20", "balanced": "23", "small": "28"}[quality]
    return [
        "-c:v",
        recipe.export.video_codec,
        "-preset",
        recipe.export.preset,
        "-crf",
        crf,
        "-pix_fmt",
        "yuv420p",
    ]


def _audio_args(recipe: EditRecipe) -> list[str]:
    return ["-c:a", recipe.export.audio_codec, "-b:a", recipe.export.audio_bitrate]


def _precise_filters(recipe: EditRecipe, *, has_audio: bool, transform_path: str | None) -> tuple[str, str, str]:
    segments = recipe.segments
    video_labels: list[str] = []
    audio_labels: list[str] = []
    filters: list[str] = []
    video_effect = ""
    if recipe.stabilize.enabled:
        video_effect = stabilization_filter(
            recipe.stabilize.strength,
            recipe.stabilize.crop,
            transform_path=transform_path,
            prefer_vidstab=bool(transform_path),
        )
    if not segments:
        video_expr = "[0:v]setpts=PTS-STARTPTS"
        if video_effect:
            video_expr += "," + video_effect
        filters.append(video_expr + "[vout]")
        if has_audio and not recipe.mute_audio:
            audio_expr = "[0:a]asetpts=PTS-STARTPTS"
            if recipe.gain_db:
                audio_expr += f",volume={_seconds(recipe.gain_db)}dB"
            filters.append(audio_expr + "[aout]")
        return ";".join(filters), "[vout]", "[aout]" if has_audio and not recipe.mute_audio else ""

    for index, segment in enumerate(segments):
        video_label = f"[v{index}]"
        video_expr = f"[0:v]trim=start={_seconds(segment.start_sec)}:end={_seconds(segment.end_sec)},setpts=PTS-STARTPTS"
        if video_effect:
            video_expr += "," + video_effect
        filters.append(video_expr + video_label)
        video_labels.append(video_label)
        if has_audio and not recipe.mute_audio:
            audio_label = f"[a{index}]"
            audio_expr = f"[0:a]atrim=start={_seconds(segment.start_sec)}:end={_seconds(segment.end_sec)},asetpts=PTS-STARTPTS"
            if recipe.gain_db:
                audio_expr += f",volume={_seconds(recipe.gain_db)}dB"
            filters.append(audio_expr + audio_label)
            audio_labels.append(audio_label)
    filters.append("".join(video_labels) + f"concat=n={len(video_labels)}:v=1:a=0[vout]")
    if audio_labels:
        filters.append("".join(audio_labels) + f"concat=n={len(audio_labels)}:v=0:a=1[aout]")
    return ";".join(filters), "[vout]", "[aout]" if audio_labels else ""


def build_ffmpeg_command(
    source: str | os.PathLike[str],
    destination: str | os.PathLike[str],
    recipe: EditRecipe | Mapping[str, Any],
    *,
    mode: str = "precise",
    ffmpeg: str | None = None,
    has_audio: bool = True,
    transform_path: str | os.PathLike[str] | None = None,
) -> list[str]:
    """Build an argument-array command; no user value is passed through a shell."""
    edit = EditRecipe.coerce(recipe)
    if mode not in {"fast", "precise"}:
        raise ValueError("mode must be fast or precise")
    source_path = Path(source)
    dest_path = Path(destination)
    if source_path.resolve() == dest_path.resolve():
        raise EditRecipeError("source and destination must be different")
    if mode == "fast":
        if len(edit.segments) > 1 or edit.mute_audio or edit.gain_db or edit.stabilize.enabled:
            raise EditRecipeError("fast export supports only one untouched trim interval")
        command = [ffmpeg or _runtime_binary("ffmpeg", "ffmpeg"), "-hide_banner", "-loglevel", "error", "-y"]
        if edit.segments:
            command += ["-ss", _seconds(edit.segments[0].start_sec), "-to", _seconds(edit.segments[0].end_sec)]
        command += ["-i", os.fspath(source), "-map", "0:v:0"]
        if has_audio:
            command += ["-map", "0:a:0?"]
        command += ["-c", "copy", "-avoid_negative_ts", "make_zero", "-progress", "pipe:1", "-nostats", os.fspath(destination)]
        return command

    filters, video_map, audio_map = _precise_filters(
        edit, has_audio=has_audio, transform_path=os.fspath(transform_path) if transform_path else None
    )
    command = [ffmpeg or _runtime_binary("ffmpeg", "ffmpeg"), "-hide_banner", "-loglevel", "error", "-y", "-i", os.fspath(source)]
    command += ["-filter_complex", filters, "-map", video_map]
    if audio_map:
        command += ["-map", audio_map]
    command += _quality_args(edit)
    if audio_map:
        command += _audio_args(edit)
    if edit.export.container in {"mp4", "mov"}:
        command += ["-movflags", "+faststart"]
    command += ["-progress", "pipe:1", "-nostats", os.fspath(destination)]
    return command


@dataclass(frozen=True)
class OutputProbe:
    duration_sec: float | None
    size_bytes: int


@dataclass(frozen=True)
class ExportResult:
    output_path: Path
    size_bytes: int
    duration_sec: float | None
    command: tuple[str, ...]


def probe_output(path: Path, *, ffprobe: str | None = None) -> OutputProbe:
    if not path.is_file() or path.stat().st_size <= 0:
        raise ExportValidationError("FFmpeg produced no output")
    executable = ffprobe or _runtime_binary("ffprobe", "ffprobe")
    command = [executable, "-v", "error", "-show_entries", "format=duration", "-of", "json", os.fspath(path)]
    try:
        result = subprocess.run(command, capture_output=True, text=True, check=True)
    except FileNotFoundError:
        # Unit-test/dev fallback: a non-empty file is still useful when no
        # ffprobe runtime is installed. Production bundles resolve ffprobe.
        return OutputProbe(None, path.stat().st_size)
    except subprocess.CalledProcessError as exc:
        raise ExportValidationError("ffprobe rejected the exported file") from exc
    try:
        payload = json.loads(result.stdout)
        raw_duration = payload.get("format", {}).get("duration")
        duration = float(raw_duration) if raw_duration not in (None, "N/A") else None
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        raise ExportValidationError("ffprobe returned invalid metadata") from exc
    return OutputProbe(duration, path.stat().st_size)


def run_export(
    source: str | os.PathLike[str],
    destination: str | os.PathLike[str],
    recipe: EditRecipe | Mapping[str, Any],
    *,
    mode: str = "precise",
    has_audio: bool = True,
    ffmpeg: str | None = None,
    ffprobe: str | None = None,
    transform_path: str | os.PathLike[str] | None = None,
    process_runner: Callable[..., Any] | None = None,
    on_progress: Callable[[Any], None] | None = None,
    cancel_event: Any = None,
    probe_fn: Callable[[Path], OutputProbe] | None = None,
) -> ExportResult:
    """Render to a sibling temporary file, validate, then atomically replace output."""
    source_path = Path(source).resolve()
    destination_path = Path(destination).resolve()
    if source_path == destination_path:
        raise ExportError("refusing to overwrite the source video")
    if not source_path.is_file():
        raise ExportError(f"source video does not exist: {source_path}")
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    suffix = destination_path.suffix or ".mp4"
    temp_path = destination_path.parent / f".{destination_path.stem}.selects-{uuid.uuid4().hex}{suffix}"
    command = build_ffmpeg_command(
        source_path,
        temp_path,
        recipe,
        mode=mode,
        ffmpeg=ffmpeg,
        has_audio=has_audio,
        transform_path=transform_path,
    )
    runner = process_runner
    if runner is None:
        from .jobs import run_process

        runner = run_process
    try:
        runner_result = runner(
            command,
            duration_sec=EditRecipe.coerce(recipe).duration_sec,
            on_progress=on_progress,
            cancel_event=cancel_event,
        )
        returncode = getattr(runner_result, "returncode", runner_result if isinstance(runner_result, int) else 0)
        if returncode:
            raise ExportError(f"FFmpeg exited with status {returncode}")
        output_probe = probe_fn(temp_path) if probe_fn else probe_output(temp_path, ffprobe=ffprobe)
        if output_probe.size_bytes <= 0:
            raise ExportValidationError("exported output is empty")
        os.replace(temp_path, destination_path)
        return ExportResult(destination_path, output_probe.size_bytes, output_probe.duration_sec, tuple(command))
    finally:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass
