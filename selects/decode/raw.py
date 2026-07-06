from __future__ import annotations

import io
from pathlib import Path

import numpy as np


def decode_raw_preview(path: Path) -> np.ndarray:
    """Read the embedded JPEG preview from a RAW file.

    Falls back to a full rawpy demosaic if no embedded preview exists.
    """
    import rawpy

    with rawpy.imread(str(path)) as raw:
        try:
            thumb = raw.extract_thumb()
            if thumb.format == rawpy.ThumbFormat.JPEG:
                from PIL import Image

                with Image.open(io.BytesIO(thumb.data)) as im:
                    return np.asarray(im.convert("RGB"), dtype=np.uint8)
            return np.asarray(thumb.data, dtype=np.uint8)
        except rawpy.LibRawNoThumbnailError:
            rgb = raw.postprocess(use_camera_wb=True, output_bps=8, no_auto_bright=True)
            return np.ascontiguousarray(rgb, dtype=np.uint8)
