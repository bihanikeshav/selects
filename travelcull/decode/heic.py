from __future__ import annotations

from pathlib import Path

import numpy as np
from pillow_heif import register_heif_opener

register_heif_opener()


def decode_heic(path: Path) -> np.ndarray:
    """Decode HEIC/HEIF to HWC uint8 RGB ndarray. CPU-bound (no GPU HEIC codec in OSS)."""
    from PIL import Image

    with Image.open(path) as im:
        return np.asarray(im.convert("RGB"), dtype=np.uint8)
