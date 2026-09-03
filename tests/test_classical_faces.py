import sys

import numpy as np
import pytest

from selects.classical.faces import detect_faces


def test_detect_returns_list_for_random_image():
    rng = np.random.default_rng(0)
    img = (rng.random((480, 640, 3)) * 255).astype(np.uint8)
    faces = detect_faces(img)
    assert isinstance(faces, list)


def test_no_faces_in_solid_image():
    img = np.full((480, 640, 3), 128, dtype=np.uint8)
    assert detect_faces(img) == []


def test_detect_faces_without_insightface_returns_list(monkeypatch: pytest.MonkeyPatch):
    import builtins

    import selects.classical.faces as faces_mod

    monkeypatch.setattr(faces_mod, "_detector", None)
    monkeypatch.setattr(faces_mod, "_detector_failed", False)
    monkeypatch.delitem(sys.modules, "insightface", raising=False)
    monkeypatch.delitem(sys.modules, "insightface.app", raising=False)

    real_import = builtins.__import__

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "insightface" or name.startswith("insightface."):
            raise ImportError("No module named 'insightface'")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    img = np.full((64, 64, 3), 255, dtype=np.uint8)
    faces = detect_faces(img)
    assert isinstance(faces, list)


def test_haar_fallback_invokes_detect_multiscale(monkeypatch: pytest.MonkeyPatch):
    import cv2

    import selects.classical.faces as faces_mod

    monkeypatch.setattr(faces_mod, "_get_detector", lambda: None)

    called: dict = {}

    class FakeCascade:
        def __init__(self, path):
            called["path"] = path

        def empty(self):
            return False

        def detectMultiScale(self, gray, *args, **kwargs):
            called["gray_shape"] = gray.shape
            return np.array([[10, 20, 30, 40]])

    monkeypatch.setattr(cv2, "CascadeClassifier", FakeCascade)

    img = np.full((80, 80, 3), 255, dtype=np.uint8)
    faces = detect_faces(img)
    assert "gray_shape" in called
    assert len(faces) == 1
    assert faces[0].x == 10
    assert faces[0].y == 20
    assert faces[0].w == 30
    assert faces[0].h == 40
    assert faces[0].confidence == 0.5
    assert faces[0].embedding is None
