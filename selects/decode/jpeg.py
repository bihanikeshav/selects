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


def decode_jpeg(path: Path) -> np.ndarray:
    """Decode a standard image (JPEG/PNG/WebP/TIFF/BMP/GIF) to HWC uint8 RGB.

    Real JPEGs go through GPU nvImageCodec when available; other formats (and any
    failure) fall back to PIL, which opens them all.
    """
    dec = _try_nvimg() if path.suffix.lower() in (".jpg", ".jpeg") else False
    if dec:
        try:
            with path.open("rb") as f:
                data = f.read()
            img = dec.decode(data)
            arr = np.asarray(img.cpu()) if hasattr(img, "cpu") else np.asarray(img)
            if arr.shape[-1] == 3:
                return arr.astype(np.uint8, copy=False)
        except Exception:
            pass

    from PIL import Image

    with Image.open(path) as im:
        return np.asarray(im.convert("RGB"), dtype=np.uint8)
