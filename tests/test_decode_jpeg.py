import numpy as np
from PIL import Image

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


def test_exif_orientation_6_is_transposed(tmp_path):
    path = tmp_path / "oriented.jpg"
    # Stored pixels are wide (64x32); Orientation=6 is 90° CW → tall 32x64.
    im = Image.new("RGB", (64, 32), (200, 10, 10))
    exif = im.getexif()
    exif[0x0112] = 6
    im.save(path, "JPEG", exif=exif)

    with Image.open(path) as raw_im:
        assert raw_im.getexif().get(0x0112) == 6
        untransposed = np.asarray(raw_im.convert("RGB"))

    decoded = decode_jpeg(path)
    assert untransposed.shape[:2] == (32, 64)
    assert decoded.shape[:2] == (untransposed.shape[1], untransposed.shape[0])
    assert decoded.shape[0] > decoded.shape[1]
