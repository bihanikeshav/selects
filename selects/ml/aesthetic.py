"""In-the-wild photo quality via HyperIQA (KonIQ-10k), not AVA art scores.

AP-V2.5 was a linear head on SigLIP-1 embeddings trained on aesthetic-predictor
data that prefers "pretty/processed" stills over a sharp travel snapshot.
HyperIQA is a ResNet-50 quality model trained on authentic Flickr-style photos
(KonIQ-10k). The graph is ONNX, ~105 MB, and runs on CUDA/DirectML/CoreML.

Scores are stored on ``AestheticScore.ap25_score`` on the historical 1–10
scale (raw 0–1 × 10) so ranking/calibrate keep working. Prompt-IQA from
SigLIP 2 stays on ``Embedding.aesthetic_iqa``.
"""
from __future__ import annotations

import logging
from typing import Callable

import numpy as np
from PIL import Image

from selects.config import FolderConfig
from selects.db import init_db, session_scope
from selects.db.models import AestheticScore, Photo

log = logging.getLogger(__name__)

_IMG_SIZE = 224
_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
_STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)


def ensemble_score(
    ap25: float | None,
    nima: float | None,
    iqa: float | None,
    *,
    ap_w: float = 0.6,
    nima_w: float = 0.4,
) -> float | None:
    """Blend HyperIQA (1–10, column name ap25) + NIMA; else HyperIQA; else IQA×10."""
    if ap25 is not None and nima is not None:
        return float(ap_w * ap25 + nima_w * nima)
    if ap25 is not None:
        return float(ap25)
    if iqa is not None:
        return float(iqa) * 10.0
    return None


def rank_score(
    ap25: float | None,
    nima: float | None,
    iqa: float | None,
    *,
    ap_w: float = 0.6,
    nima_w: float = 0.4,
) -> float | None:
    """0–1 ranking score so HyperIQA (1–10) and CLIP-IQA (0–1) share a scale."""
    ens = ensemble_score(ap25, nima, iqa, ap_w=ap_w, nima_w=nima_w)
    if ens is None:
        return None
    return float(ens) / 10.0


def _preprocess(images: list[Image.Image]) -> np.ndarray:
    out = np.empty((len(images), 3, _IMG_SIZE, _IMG_SIZE), dtype=np.float32)
    for i, im in enumerate(images):
        arr = np.asarray(
            im.convert("RGB").resize((_IMG_SIZE, _IMG_SIZE), Image.BICUBIC),
            dtype=np.float32,
        ) / 255.0
        out[i] = ((arr - _MEAN) / _STD).transpose(2, 0, 1)
    return out


def score_images(images: list[Image.Image]) -> np.ndarray:
    """HyperIQA 1–10 scores for a batch of PIL images."""
    from selects.ml.onnx_rt import model_session

    if not images:
        return np.zeros((0,), dtype=np.float32)
    sess = model_session("hyperiqa")
    x = _preprocess(images)
    name = sess.get_inputs()[0].name
    raw = sess.run(None, {name: x})[0]
    vals = np.asarray(raw, dtype=np.float32).reshape(-1)
    return np.clip(vals, 0.0, 1.0) * 10.0


def run_aesthetic_stage(
    cfg: FolderConfig,
    on_progress: Callable[[int, int, str], None] | None = None,
) -> int:
    """Write HyperIQA scores into ``AestheticScore.ap25_score``. Returns count."""
    Session = init_db(cfg.db_path)
    with session_scope(Session) as s:
        rows = s.query(Photo.id, Photo.preview_path).all()
    if not rows:
        return 0
    try:
        from selects.ml.onnx_rt import model_session

        model_session("hyperiqa")
    except Exception as exc:
        log.warning("HyperIQA unavailable (%s); skipping aesthetic stage", exc)
        if on_progress:
            on_progress(0, 0, "HyperIQA weights unavailable")
        return 0

    total = len(rows)
    processed = 0
    batch: list[tuple[int, Image.Image]] = []

    def flush() -> None:
        nonlocal processed
        if not batch:
            return
        scores = score_images([im for _, im in batch])
        with session_scope(Session) as s:
            for (pid, _), sc in zip(batch, scores):
                row = s.get(AestheticScore, pid) or AestheticScore(photo_id=pid)
                row.ap25_score = float(sc)
                s.add(row)
        processed += len(batch)
        batch.clear()
        if on_progress:
            on_progress(processed, total, "hyperiqa")

    for pid, preview_path in rows:
        if not preview_path:
            continue
        path = cfg.state_dir / preview_path
        try:
            img = Image.open(path).convert("RGB")
        except Exception as exc:
            log.warning("hyperiqa: could not load preview for %s: %s", pid, exc)
            continue
        batch.append((pid, img))
        if len(batch) >= 8:
            flush()
    flush()
    log.info("aesthetic stage: scored %d photos", processed)
    return processed
