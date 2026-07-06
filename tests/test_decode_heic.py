import numpy as np

from selects.decode.heic import decode_heic


def test_decode_heic_returns_uint8_rgb(fixtures_dir):
    img = decode_heic(fixtures_dir / "small.heic")
    assert img.dtype == np.uint8
    assert img.shape[2] == 3
    assert img.shape[0] == 480
    assert img.shape[1] == 640
