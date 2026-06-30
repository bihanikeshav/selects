from pathlib import Path

import numpy as np
import pytest

from travelcull.decode.raw import decode_raw_preview

DNG = Path(__file__).parent / "fixtures" / "small.dng"


@pytest.mark.skipif(not DNG.exists(), reason="No DNG fixture")
def test_decode_raw_preview_returns_uint8_rgb():
    img = decode_raw_preview(DNG)
    assert img.dtype == np.uint8
    assert img.ndim == 3
    assert img.shape[2] == 3
    assert img.shape[0] >= 200 and img.shape[1] >= 200
