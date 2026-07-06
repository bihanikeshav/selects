import numpy as np

from selects.classical.faces import detect_faces


def test_detect_returns_list_for_random_image():
    rng = np.random.default_rng(0)
    img = (rng.random((480, 640, 3)) * 255).astype(np.uint8)
    faces = detect_faces(img)
    assert isinstance(faces, list)


def test_no_faces_in_solid_image():
    img = np.full((480, 640, 3), 128, dtype=np.uint8)
    assert detect_faces(img) == []
