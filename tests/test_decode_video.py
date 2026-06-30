import numpy as np

from travelcull.decode.video import VideoMeta, decode_first_frame, probe


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
