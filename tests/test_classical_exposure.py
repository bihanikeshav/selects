import numpy as np

from travelcull.classical.exposure import exposure_score


def test_balanced_image_scores_high():
    img = np.full((100, 100, 3), 128, dtype=np.uint8)
    s = exposure_score(img)
    assert 0.4 < s.score < 0.7
    assert s.clipped_ratio < 0.05


def test_black_image_scores_low():
    img = np.zeros((100, 100, 3), dtype=np.uint8)
    s = exposure_score(img)
    assert s.score < 0.2
    assert s.clipped_ratio > 0.9


def test_blown_out_image_scores_low():
    img = np.full((100, 100, 3), 255, dtype=np.uint8)
    s = exposure_score(img)
    assert s.score < 0.2
    assert s.clipped_ratio > 0.9
