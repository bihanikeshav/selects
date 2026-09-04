import numpy as np
import pytest

from selects.decode.video import VideoMeta, decode_first_frame, probe


def test_probe_returns_meta(fixtures_dir):
    m = probe(fixtures_dir / "small.mp4")
    assert isinstance(m, VideoMeta)
    assert m.width == 640
    assert m.height == 480
    assert 1.5 < m.duration_sec < 2.5


def test_decode_first_frame_returns_uint8_rgb(fixtures_dir):
    frame = decode_first_frame(fixtures_dir / "small.mp4")
    assert frame.dtype == np.uint8
    assert frame.shape[2] == 3
    assert frame.shape[:2] == (480, 640)


def _raise_missing(*_a, **_k):
    raise FileNotFoundError("ffmpeg")


def test_probe_falls_back_to_cv2_without_ffprobe(fixtures_dir, monkeypatch):
    monkeypatch.setattr("selects.decode.video.subprocess.check_output", _raise_missing)
    m = probe(fixtures_dir / "small.mp4")
    assert isinstance(m, VideoMeta)
    assert m.width == 640
    assert m.height == 480
    assert 1.5 < m.duration_sec < 2.5


def test_decode_first_frame_falls_back_to_cv2_without_ffmpeg(fixtures_dir, monkeypatch):
    monkeypatch.setattr("selects.decode.video.subprocess.check_output", _raise_missing)
    frame = decode_first_frame(fixtures_dir / "small.mp4")
    assert frame.dtype == np.uint8
    assert frame.shape[2] == 3
    assert frame.shape[:2] == (480, 640)


def test_probe_uses_format_duration_when_stream_is_na(tmp_path, monkeypatch):
    payload = "width=1920\nheight=1080\ncodec_name=h264\nduration=N/A\nduration=3.5\n"

    def fake_ffprobe(cmd, text=False, **_kwargs):
        if cmd[0] != "ffprobe":
            raise FileNotFoundError(cmd[0])
        return payload if text else payload.encode()

    monkeypatch.setattr("selects.decode.video.subprocess.check_output", fake_ffprobe)
    m = probe(tmp_path / "clip.mp4")
    assert m.width == 1920
    assert m.height == 1080
    assert m.duration_sec == pytest.approx(3.5)
    assert m.codec == "h264"
