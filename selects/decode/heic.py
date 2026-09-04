from __future__ import annotations

from pathlib import Path

import numpy as np
from pillow_heif import register_heif_opener

register_heif_opener()


def decode_heic(path: Path) -> np.ndarray:
    """Decode HEIC/HEIF to HWC uint8 RGB ndarray. CPU-bound (no GPU HEIC codec in OSS)."""
    from PIL import Image, ImageOps

    with Image.open(path) as im:
        # pillow-heif resets EXIF Orientation to 1 and stores the real value
        # on info["original_orientation"]; restore it so transpose sees it.
        original = im.info.get("original_orientation")
        if original and original != 1:
            im.getexif()[0x0112] = int(original)
        out = ImageOps.exif_transpose(im)
        if out is None:
            out = im
        return np.asarray(out.convert("RGB"), dtype=np.uint8)
