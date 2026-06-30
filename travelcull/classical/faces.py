from __future__ import annotations

from dataclasses import dataclass

import numpy as np

_detector = None


@dataclass
class Face:
    x: int
    y: int
    w: int
    h: int
    confidence: float


def _get_detector():
    global _detector
    if _detector is not None:
        return _detector
    from insightface.app import FaceAnalysis

    providers = ["CUDAExecutionProvider", "CPUExecutionProvider"]
    app = FaceAnalysis(name="buffalo_l", providers=providers)
    app.prepare(ctx_id=0, det_size=(640, 640))
    _detector = app
    return app


def detect_faces(img: np.ndarray) -> list[Face]:
    import cv2

    bgr = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
    det = _get_detector()
    faces = det.get(bgr)
    result = []
    for f in faces:
        x1, y1, x2, y2 = (int(v) for v in f.bbox)
        result.append(Face(x=x1, y=y1, w=x2 - x1, h=y2 - y1, confidence=float(f.det_score)))
    return result
