import numpy as np

from selects.classical.blur import laplacian_variance


def test_blur_high_variance_for_sharp_image():
    rng = np.random.default_rng(0)
    sharp = (rng.random((480, 640, 3)) * 255).astype(np.uint8)
    val = laplacian_variance(sharp)
    assert val > 1000


def test_blur_low_variance_for_uniform_image():
    uniform = np.full((480, 640, 3), 128, dtype=np.uint8)
    val = laplacian_variance(uniform)
    assert val < 1.0
