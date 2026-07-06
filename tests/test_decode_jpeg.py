import numpy as np

from selects.decode.jpeg import decode_jpeg


def test_decode_jpeg_returns_hwc_uint8(fixtures_dir):
    img = decode_jpeg(fixtures_dir / "small.jpg")
    assert img.dtype == np.uint8
    assert img.ndim == 3
    assert img.shape[2] == 3


def test_decode_jpeg_dimensions(fixtures_dir):
    img = decode_jpeg(fixtures_dir / "small.jpg")
    assert img.shape[0] == 480
    assert img.shape[1] == 640
