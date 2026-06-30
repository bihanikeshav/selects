from __future__ import annotations

from pathlib import Path

import numpy as np

THUMB_LONG_EDGE = 256
PREVIEW_LONG_EDGE = 1024
JPEG_QUALITY = 85


def write_previews(
    img: np.ndarray, sha256: str, thumbs_dir: Path, previews_dir: Path
) -> tuple[Path, Path]:
    """Write 256px thumb and 1024px preview JPEGs. Returns absolute paths."""
    thumbs_dir.mkdir(parents=True, exist_ok=True)
    previews_dir.mkdir(parents=True, exist_ok=True)

    thumb_path = thumbs_dir / f"{sha256}.jpg"
    preview_path = previews_dir / f"{sha256}.jpg"

    _resize_and_save(img, THUMB_LONG_EDGE, thumb_path)
    _resize_and_save(img, PREVIEW_LONG_EDGE, preview_path)

    return thumb_path, preview_path


def _resize_and_save(img: np.ndarray, long_edge: int, out_path: Path) -> None:
    from PIL import Image

    h, w = img.shape[:2]
    scale = long_edge / max(h, w)
    if scale >= 1.0:
        Image.fromarray(img).save(out_path, "JPEG", quality=JPEG_QUALITY)
        return
    new_w, new_h = int(w * scale), int(h * scale)
    with Image.fromarray(img) as im:
        im_resized = im.resize((new_w, new_h), Image.Resampling.LANCZOS)
        im_resized.save(out_path, "JPEG", quality=JPEG_QUALITY, optimize=False)
