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


def test_detect_faces_without_insightface_returns_empty(monkeypatch: pytest.MonkeyPatch):
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

    img = np.full((48, 48, 3), 128, dtype=np.uint8)
    assert detect_faces(img) == []
