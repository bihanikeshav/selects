from __future__ import annotations

from pathlib import Path

import numpy as np

_nvimg_decoder = None


def _try_nvimg():
    global _nvimg_decoder
    if _nvimg_decoder is not None:
        return _nvimg_decoder
    try:
        from nvidia import nvimgcodec

        _nvimg_decoder = nvimgcodec.Decoder()
        return _nvimg_decoder
    except Exception:
        _nvimg_decoder = False
        return False


def _rgb_exif_transposed(im) -> np.ndarray:
    """HWC uint8 RGB with EXIF orientation applied. Does not write the file."""
    from PIL import ImageOps

    # pillow-heif (and similar plugins) may reset the EXIF tag to 1 and stash
    # the real value on Image.info; restore it so ImageOps.exif_transpose sees it.
    original = im.info.get("original_orientation")
    if original and original != 1:
        im.getexif()[0x0112] = int(original)
    out = ImageOps.exif_transpose(im)
    if out is None:
        out = im
    return np.asarray(out.convert("RGB"), dtype=np.uint8)


def _apply_file_exif_transpose(arr: np.ndarray, path: Path) -> np.ndarray:
    """Apply the source file's EXIF orientation to an already-decoded array."""
    from PIL import Image, ImageOps

    with Image.open(path) as src:
        orientation = src.getexif().get(0x0112, 1)
        original = src.info.get("original_orientation")
        if original and original != 1:
            orientation = int(original)
    if not orientation or orientation == 1:
        return arr.astype(np.uint8, copy=False)
    im = Image.fromarray(arr.astype(np.uint8, copy=False))
    im.getexif()[0x0112] = int(orientation)
    out = ImageOps.exif_transpose(im)
    if out is None:
        out = im
    return np.asarray(out.convert("RGB"), dtype=np.uint8)


def decode_jpeg(path: Path) -> np.ndarray:
    """Decode a standard image (JPEG/PNG/WebP/TIFF/BMP/GIF) to HWC uint8 RGB.

    Real JPEGs go through GPU nvImageCodec when available; other formats (and any
    failure) fall back to PIL, which opens them all. EXIF orientation is applied
    before the array is returned; the original file is not modified.
    """
    dec = _try_nvimg() if path.suffix.lower() in (".jpg", ".jpeg") else False
    if dec:
        try:
            with path.open("rb") as f:
                data = f.read()
            img = dec.decode(data)
            arr = np.asarray(img.cpu()) if hasattr(img, "cpu") else np.asarray(img)
            if arr.shape[-1] == 3:
                return _apply_file_exif_transpose(arr, path)
        except Exception:
            pass

    from PIL import Image

    with Image.open(path) as im:
        return _rgb_exif_transposed(im)
