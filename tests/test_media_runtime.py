"""Unit tests for the framework-neutral media runtime helpers."""

from __future__ import annotations

import json
import shutil
import subprocess
from threading import Event
from pathlib import Path

import pytest

from selects.media import (
    AudioStream,
    ByteRange,
    FFmpegRuntime,
    MediaProbe,
    VideoStream,
    analyze_audio,
    decide_playback,
    generate_proxy,
    iter_file_range,
    parse_range_header,
    probe_media,
)


def test_single_and_suffix_ranges():
    assert parse_range_header("bytes=2-5", 10) == ByteRange(2, 5, 10)
    assert parse_range_header("bytes=7-", 10).content_range == "bytes 7-9/10"
    assert parse_range_header("bytes=-3", 10).length == 3
    assert parse_range_header(None, 10) is None


@pytest.mark.parametrize("value", ["items=0-1", "bytes=", "bytes=20-", "bytes=5-2", "bytes=0-1,3-4"])
def test_invalid_ranges_are_explicit(value):
    from selects.media.ranges import RangeNotSatisfiable

    with pytest.raises(RangeNotSatisfiable):
        parse_range_header(value, 10)


def test_file_range_is_bounded(tmp_path):
    path = tmp_path / "media.bin"
    path.write_bytes(b"0123456789")
    assert b"".join(iter_file_range(path, ByteRange(3, 7, 10), chunk_size=2)) == b"34567"


def test_playback_policy_accepts_h264_aac_mp4():
    probe = MediaProbe(
        Path("clip.mp4"),
        format_name="mov,mp4,m4a,3gp,3g2,mj2",
        video=VideoStream(codec="h264", profile="High", level=41, pixel_format="yuv420p"),
        audio=AudioStream(codec="aac"),
    )
    decision = decide_playback(probe)
    assert decision.compatible and not decision.needs_proxy and decision.direct


def test_playback_policy_proxies_unsupported_codec():
    probe = MediaProbe(Path("clip.mkv"), format_name="matroska,webm", video=VideoStream(codec="hevc"))
    decision = decide_playback(probe)
    assert not decision.compatible and decision.needs_proxy
    assert "video codec hevc" in decision.reason


def test_probe_normalizes_ffprobe_json(monkeypatch, tmp_path):
    source = tmp_path / "clip.mov"
    source.write_bytes(b"not actually media")
    runtime = FFmpegRuntime(Path("ffmpeg"), Path("ffprobe"), "test")
    payload = {
        "format": {"format_name": "mov,mp4", "duration": "12.5", "size": "1234", "bit_rate": "8000"},
        "streams": [
            {"codec_type": "video", "codec_name": "h264", "width": 1920, "height": 1080, "pix_fmt": "yuv420p", "avg_frame_rate": "30000/1001"},
            {"codec_type": "audio", "codec_name": "aac", "sample_rate": "48000", "channels": 2},
        ],
    }

    def fake_run(command, **kwargs):
        assert command == ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(source)]
        assert kwargs["shell"] is False
        return subprocess.CompletedProcess(command, 0, stdout=json.dumps(payload), stderr="")

    monkeypatch.setattr("selects.media.probe.subprocess.run", fake_run)
    result = probe_media(source, runtime=runtime)
    assert result.available and result.duration_sec == 12.5
    assert result.video_codec == "h264" and result.audio_codec == "aac"
    assert result.frame_rate == pytest.approx(29.97, rel=1e-3)


def test_probe_degrades_when_ffprobe_is_missing(tmp_path):
    result = probe_media(tmp_path / "missing.mp4", runtime=FFmpegRuntime(None, None))
    assert not result.available and "not available" in (result.error or "")


def test_proxy_and_audio_degrade_without_ffmpeg(tmp_path):
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"original")
    runtime = FFmpegRuntime(None, None)
    proxy = generate_proxy(source, tmp_path / "cache", runtime=runtime)
    assert not proxy.available and proxy.path is None
    analysis = analyze_audio(source, runtime=runtime)
    assert not analysis.available and analysis.bins == []
    assert source.read_bytes() == b"original"


def test_proxy_honors_pre_cancel_without_starting_ffmpeg(tmp_path):
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"original")
    cancelled = Event()
    cancelled.set()
    result = generate_proxy(source, tmp_path / "cache", runtime=FFmpegRuntime(None, None), cancel=cancelled)
    assert result.cancelled and result.path is None


def test_proxy_cache_path_changes_when_source_changes(tmp_path):
    import os

    source = tmp_path / "clip.mp4"
    source.write_bytes(b"one")
    from selects.media.proxy import proxy_cache_path

    first = proxy_cache_path(source, tmp_path / "cache")
    source.write_bytes(b"two")
    stat = source.stat()
    os.utime(source, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))
    assert first != proxy_cache_path(source, tmp_path / "cache")


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="FFmpeg is optional")
def test_real_ffmpeg_proxy_smoke(tmp_path):
    """Exercise the real encoder only on developer/CI images that have it."""
    source = tmp_path / "source.mp4"
    subprocess.run([shutil.which("ffmpeg"), "-y", "-f", "lavfi", "-i", "color=c=black:s=16x16:d=0.2", str(source)], check=True, capture_output=True)
    result = generate_proxy(source, tmp_path / "cache")
    assert result.success and result.path is not None and result.path != source


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="FFmpeg is optional")
def test_real_ffmpeg_audio_analysis_is_streaming_and_compact(tmp_path):
    source = tmp_path / "tone.wav"
    subprocess.run(
        [shutil.which("ffmpeg"), "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=0.4", str(source)],
        check=True,
        capture_output=True,
    )
    result = analyze_audio(source, bins=32, window_seconds=0.1)
    assert result.available and result.rms > 0.01 and result.peak > 0.01
    assert len(result.bins) == 32 and result.speech_windows
    assert all(hasattr(item, "speech_like") for item in result.speech_windows)
