from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class ExposureResult:
    score: float
    mean: float
    clipped_ratio: float


def exposure_score(img: np.ndarray) -> ExposureResult:
    """Score in [0,1]. Higher = better exposed. 0 = all-black or all-white."""
    import cv2

    gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
    mean = float(gray.mean()) / 255.0

    n = gray.size
    clipped = float(((gray < 8).sum() + (gray > 247).sum()) / n)

    midness = 1.0 - 2.0 * abs(mean - 0.5)
    # Normalise std to [0,1]: max theoretical std for a bimodal 0/255 image is 127.5
    std_norm = min(float(gray.std()) / 127.5, 1.0)
    score = max(0.0, midness * (1.0 - clipped) * (0.5 + 0.5 * std_norm))
    return ExposureResult(score=score, mean=mean, clipped_ratio=clipped)
