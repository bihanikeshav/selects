from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np

log = logging.getLogger(__name__)

_detector = None
_detector_failed = False


@dataclass
class Face:
    x: int
    y: int
    w: int
    h: int
    confidence: float
    embedding: Optional[np.ndarray] = field(default=None, compare=False)
    # Sub-model outputs from buffalo_l (the default FaceAnalysis loads all
    # modules — detection, recognition, landmark_2d_106, landmark_3d_68/pose).
    kps: Optional[np.ndarray] = field(default=None, compare=False)              # 5x2 keypoints
    landmark_2d_106: Optional[np.ndarray] = field(default=None, compare=False)  # 106x2 landmarks
    pose: Optional[np.ndarray] = field(default=None, compare=False)             # (pitch, yaw, roll) deg


def _get_detector():
    global _detector, _detector_failed
    if _detector_failed:
        return None
    if _detector is not None:
        return _detector
    try:
        import os

        from insightface.app import FaceAnalysis

        # NOTE: DirectML cannot run buffalo_l's SCRFD detector (its Reshape ops throw
        # under DmlExecutionProvider, same limitation as our SigLIP/RAM++ models), so
        # we deliberately DON'T offer DML here. CUDA accelerates on NVIDIA builds; on
        # the DirectML/CPU build onnxruntime simply uses CPU (CUDA EP absent).
        providers = ["CUDAExecutionProvider", "CPUExecutionProvider"]
        app = FaceAnalysis(name="buffalo_l", providers=providers)
        # det_size drives small-face recall: at 640x640 distant/small faces vanish.
        # 1280x1280 recovers them (slower). det_thresh 0.4 (< default 0.5) keeps more
        # low-confidence faces. Both overridable via env for tuning.
        det = int(os.environ.get("SELECTS_FACE_DET_SIZE", "1280"))
        thr = float(os.environ.get("SELECTS_FACE_DET_THRESH", "0.4"))
        app.prepare(ctx_id=0, det_size=(det, det), det_thresh=thr)
        _detector = app
        return app
    except Exception as exc:
        log.warning("face detector unavailable (%s); falling back to Haar", exc)
        _detector_failed = True
        return None


def _detect_haar(img: np.ndarray) -> list[Face]:
    """OpenCV Haar fallback when InsightFace is unavailable. Boxes only, no embeddings."""
    import cv2

    try:
        cascade_dir = getattr(getattr(cv2, "data", None), "haarcascades", None)
        if not cascade_dir:
            return []
        xml = str(Path(cascade_dir) / "haarcascade_frontalface_default.xml")
        classifier = cv2.CascadeClassifier(xml)
        if classifier.empty():
            return []
        if img.ndim == 3:
            gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
        else:
            gray = img
        rects = classifier.detectMultiScale(gray)
    except Exception as exc:
        log.warning("Haar face detector unavailable (%s)", exc)
        return []
    faces: list[Face] = []
    for rect in rects:
        x, y, w, h = (int(v) for v in rect[:4])
        faces.append(Face(x=x, y=y, w=w, h=h, confidence=0.5, embedding=None))
    return faces


def detect_faces(img: np.ndarray) -> list[Face]:
    import cv2

    try:
        det = _get_detector()
    except Exception:
        det = None
    if det is None:
        return _detect_haar(img)
    bgr = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
    faces = det.get(bgr)
    result = []
    for f in faces:
        x1, y1, x2, y2 = (int(v) for v in f.bbox)
        emb: Optional[np.ndarray] = None
        if hasattr(f, "embedding") and f.embedding is not None:
            emb = np.array(f.embedding, dtype=np.float32)

        def _arr(name: str) -> Optional[np.ndarray]:
            v = getattr(f, name, None)
            return np.array(v, dtype=np.float64) if v is not None else None

        result.append(
            Face(
                x=x1, y=y1, w=x2 - x1, h=y2 - y1,
                confidence=float(f.det_score), embedding=emb,
                kps=_arr("kps"),
                landmark_2d_106=_arr("landmark_2d_106"),
                pose=_arr("pose"),
            )
        )
    return result
