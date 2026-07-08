"""Low-light luma diagnostics used by the quality buckets.

(Historically also hosted the Zero-DCE++ / Retinexformer ONNX enhancers, both
now retired in favour of the classical auto_tone.)
"""
from __future__ import annotations

import numpy as np
from PIL import Image


def is_low_light(img: Image.Image, threshold: float = 0.30) -> bool:
    """Cheap classifier: True if the image's mean luma is below the threshold
    (0-1 scale). Used by the Image Doctor to decide whether to suggest a
    Zero-DCE++ fix.
    """
    gray = np.asarray(img.convert("L"), dtype=np.float32) / 255.0
    return float(gray.mean()) < threshold


# Optional helper that returns full luma stats — used by the doctor classifier
def luma_stats(img: Image.Image) -> dict:
    gray = np.asarray(img.convert("L"), dtype=np.float32) / 255.0
    return {
        "mean": float(gray.mean()),
        "std": float(gray.std()),
        "clipped_low": float((gray < 8 / 255).mean()),
        "clipped_high": float((gray > 247 / 255).mean()),
        "p05": float(np.percentile(gray, 5)),
        "p95": float(np.percentile(gray, 95)),
    }
