"""Streaming, low-memory audio activity analysis using FFmpeg PCM output."""

from __future__ import annotations

import math
import subprocess
from dataclasses import dataclass
from pathlib import Path
from threading import Event
from typing import Callable, Any

import numpy as np

from .probe import probe_media
from .runtime import FFmpegRuntime, resolve_ffmpeg, spawn_ffmpeg


@dataclass(frozen=True)
class WaveformBin:
    start: float
    end: float
    rms: float
    peak: float


@dataclass(frozen=True)
class EnergyWindow:
    start: float
    end: float
    rms: float
    peak: float
    speech_like: bool


@dataclass
class AudioAnalysis:
    source: Path
    sample_rate: int
    duration: float | None
    bins: list[WaveformBin]
    rms: float
    peak: float
    silence_ratio: float
    speech_windows: list[EnergyWindow]
    available: bool = True
    cancelled: bool = False
    error: str | None = None

    @property
    def waveform(self) -> list[WaveformBin]:
        return self.bins

    def as_dict(self) -> dict[str, Any]:
        return {
            "source": str(self.source),
            "sample_rate": self.sample_rate,
            "duration": self.duration,
            "bins": [item.__dict__ for item in self.bins],
            "rms": self.rms,
            "peak": self.peak,
            "silence_ratio": self.silence_ratio,
            "speech_windows": [item.__dict__ for item in self.speech_windows],
            "available": self.available,
            "cancelled": self.cancelled,
            "error": self.error,
        }


def _empty_result(source: Path, sample_rate: int, error: str, *, duration: float | None = None) -> AudioAnalysis:
    return AudioAnalysis(source, sample_rate, duration, [], 0.0, 0.0, 1.0, [], available=False, error=error)


def _cancelled(signal: Event | Callable[[], bool] | None) -> bool:
    if signal is None:
        return False
    return signal.is_set() if hasattr(signal, "is_set") else bool(signal())


def _finish_bin(
    total: float,
    peak: float,
    count: int,
    start: float,
    end: float,
) -> WaveformBin:
    return WaveformBin(start, end, math.sqrt(total / count) if count else 0.0, peak)


def analyze_audio(
    source: str | Path,
    *,
    runtime: FFmpegRuntime | None = None,
    bins: int = 256,
    sample_rate: int = 16_000,
    window_seconds: float = 0.5,
    silence_threshold: float = 0.01,
    cancel: Event | Callable[[], bool] | None = None,
) -> AudioAnalysis:
    """Analyze audio without retaining decoded PCM.

    Waveform bins and half-second energy windows retain only sums, counts and
    peaks.  ``speech_like`` is a deliberately cheap activity heuristic, not a
    speech recognizer: it marks non-silent, moderate-energy windows.
    """

    source_path = Path(source).expanduser().resolve()
    if bins <= 0 or sample_rate <= 0 or window_seconds <= 0:
        raise ValueError("bins, sample_rate, and window_seconds must be positive")
    resolved = runtime or resolve_ffmpeg()
    if resolved.ffmpeg is None:
        return _empty_result(source_path, sample_rate, "ffmpeg is not available")
    probe = probe_media(source_path, runtime=resolved)
    duration = probe.duration
    if not probe.error and not probe.audio:
        return _empty_result(source_path, sample_rate, "media has no audio stream", duration=duration)

    expected_samples = max(1, round(duration * sample_rate)) if duration and duration > 0 else None
    bin_count = bins
    bin_totals = np.zeros(bin_count, dtype=np.float64)
    bin_peaks = np.zeros(bin_count, dtype=np.float64)
    bin_counts = np.zeros(bin_count, dtype=np.int64)
    window_samples = max(1, round(window_seconds * sample_rate))
    window_totals: list[float] = []
    window_peaks: list[float] = []
    window_counts: list[int] = []
    total_sum = 0.0
    total_peak = 0.0
    total_count = 0
    silent_count = 0
    process: subprocess.Popen[bytes] | None = None
    try:
        if _cancelled(cancel):
            return AudioAnalysis(source_path, sample_rate, duration, [], 0.0, 0.0, 1.0, [], cancelled=True)
        process = spawn_ffmpeg(
            ["-hide_banner", "-loglevel", "error", "-i", source_path, "-vn", "-ac", 1, "-ar", sample_rate, "-f", "s16le", "pipe:1"],
            runtime=resolved,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        assert process.stdout is not None
        sample_index = 0
        while True:
            if _cancelled(cancel):
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                return AudioAnalysis(source_path, sample_rate, duration, [], 0.0, 0.0, 1.0, [], cancelled=True)
            raw = process.stdout.read(64 * 1024)
            if not raw:
                break
            samples = np.frombuffer(raw[: len(raw) - (len(raw) % 2)], dtype="<i2").astype(np.float32) / 32768.0
            if samples.size == 0:
                continue
            for offset in range(0, samples.size, 4096):
                block = samples[offset : offset + 4096]
                start_index = sample_index + offset
                indices = np.arange(start_index, start_index + block.size)
                if expected_samples:
                    bin_indices = np.minimum((indices * bin_count // expected_samples), bin_count - 1)
                else:
                    # Unknown-duration media still gets deterministic bins;
                    # the final output is normalized below.
                    bin_indices = np.minimum(indices // max(1, sample_rate // 4), bin_count - 1)
                abs_block = np.abs(block)
                total_sum += float(np.dot(block, block))
                total_peak = max(total_peak, float(abs_block.max(initial=0.0)))
                total_count += int(block.size)
                silent_count += int(np.count_nonzero(abs_block < silence_threshold))
                for bin_index in np.unique(bin_indices):
                    selected = block[bin_indices == bin_index]
                    bin_totals[bin_index] += float(np.dot(selected, selected))
                    bin_peaks[bin_index] = max(bin_peaks[bin_index], float(np.abs(selected).max(initial=0.0)))
                    bin_counts[bin_index] += selected.size
                while len(window_totals) <= (start_index + block.size - 1) // window_samples:
                    window_totals.append(0.0)
                    window_peaks.append(0.0)
                    window_counts.append(0)
                for window_index in np.unique(indices // window_samples):
                    selected = block[(indices // window_samples) == window_index]
                    window_totals[window_index] += float(np.dot(selected, selected))
                    window_peaks[window_index] = max(window_peaks[window_index], float(np.abs(selected).max(initial=0.0)))
                    window_counts[window_index] += selected.size
            sample_index += samples.size
        return_code = process.wait()
        if return_code != 0:
            return _empty_result(source_path, sample_rate, f"ffmpeg exited with status {return_code}", duration=duration)
        actual_duration = total_count / sample_rate if total_count else duration
        waveform: list[WaveformBin] = []
        for index in range(bin_count):
            start = (index / bin_count) * actual_duration if actual_duration else index / bin_count
            end = ((index + 1) / bin_count) * actual_duration if actual_duration else (index + 1) / bin_count
            waveform.append(_finish_bin(float(bin_totals[index]), float(bin_peaks[index]), int(bin_counts[index]), start, end))
        windows: list[EnergyWindow] = []
        # Speech-like activity uses an energy band, excluding both near-silence
        # and clipping/noise-like full-scale windows.
        for index, (sum_squares, peak, count) in enumerate(zip(window_totals, window_peaks, window_counts)):
            rms = math.sqrt(sum_squares / count) if count else 0.0
            start = index * window_seconds
            end = start + (count / sample_rate if index == len(window_totals) - 1 else window_seconds)
            windows.append(EnergyWindow(start, end, rms, peak, silence_threshold <= rms <= 0.55 and peak >= silence_threshold * 2))
        return AudioAnalysis(
            source_path, sample_rate, actual_duration, waveform,
            math.sqrt(total_sum / total_count) if total_count else 0.0,
            total_peak,
            silent_count / total_count if total_count else 1.0,
            windows,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        if process is not None and process.poll() is None:
            process.kill()
            process.wait()
        return _empty_result(source_path, sample_rate, f"audio analysis failed: {exc}", duration=duration)


analyze_audio_stream = analyze_audio
