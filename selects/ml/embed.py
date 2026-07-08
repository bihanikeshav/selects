"""SigLIP-SO400M image embedding + IQA pass — ONNX Runtime (no torch). Batched.

Image and text towers are served from siglip_vision.onnx / siglip_text.onnx
(fp16) via onnxruntime; text is tokenized by the ported SiglipTokenizer. All
returns are L2-normalized float32 numpy arrays.
"""
from __future__ import annotations

import logging
from typing import Callable

import numpy as np
from PIL import Image

from selects.config import FolderConfig
from selects.db import init_db, session_scope
from selects.db.models import Embedding, PipelineState, Photo
from selects.ml.onnx_rt import model_session
from selects.ml.siglip_tokenizer import get_tokenizer

log = logging.getLogger(__name__)

_IQA_TEXT_FEATS: np.ndarray | None = None

# SigLIP image preprocessing (from the HF image_processor config): resize to
# 384x384 BICUBIC, rescale 1/255, normalize mean/std 0.5 -> pixel = x/127.5 - 1.
_IMG_SIZE = 384

# SigLIP sigmoid-training constants (google/siglip-so400m-patch14-384), used to
# turn image<->IQA-prompt similarity into a probability. The bias cancels in the
# 2-way softmax but is kept for fidelity with the original torch path.
_LOGIT_SCALE = 112.33287048339844   # = logit_scale.exp()
_LOGIT_BIAS = -16.54642105102539

# CLIP-IQA-style antonym pair prompts (Wang et al. 2023).
# Softmax over the pair gives prob(positive) = aesthetic IQA in [0, 1].
IQA_POS = "a high-quality photograph"
IQA_NEG = "a low-quality photograph"


def _l2norm(a: np.ndarray) -> np.ndarray:
    return a / (np.linalg.norm(a, axis=-1, keepdims=True) + 1e-12)


def _preprocess_images(images: list[Image.Image]) -> np.ndarray:
    """PIL images -> [B,3,384,384] float32, matching the SigLIP image processor."""
    out = np.empty((len(images), 3, _IMG_SIZE, _IMG_SIZE), dtype=np.float32)
    for i, im in enumerate(images):
        im = im.convert("RGB").resize((_IMG_SIZE, _IMG_SIZE), Image.BICUBIC)
        arr = np.asarray(im, dtype=np.float32) / 127.5 - 1.0   # [-1, 1]
        out[i] = arr.transpose(2, 0, 1)
    return out


def encode_text_prompts(prompts: list[str]) -> np.ndarray:
    """Return L2-normalized [N, 1152] float32 text embeddings."""
    sess = model_session("siglip_text")
    ids = get_tokenizer()(prompts)                          # [N, 64] int64
    embeds = sess.run(None, {"input_ids": ids})[0]          # [N, 1152]
    return _l2norm(embeds.astype(np.float32))


def encode_image_batch(images: list[Image.Image]) -> tuple[np.ndarray, np.ndarray]:
    """Return (image_feats_normed [B,1152] float32, iqa_scores [B] float32 in [0,1]).

    IQA compares each image feature against the pos/neg IQA text prompts via a
    softmax over the antonym pair.
    """
    global _IQA_TEXT_FEATS
    if _IQA_TEXT_FEATS is None:
        _IQA_TEXT_FEATS = encode_text_prompts([IQA_POS, IQA_NEG])  # [2, 1152], once

    sess = model_session("siglip_vision")
    pixel_values = _preprocess_images(images)               # [B,3,384,384]
    embeds = sess.run(None, {"pixel_values": pixel_values})[0]
    feats = _l2norm(embeds.astype(np.float32))              # [B, 1152]

    sim = feats @ _IQA_TEXT_FEATS.T                         # [B, 2]
    logits = sim * _LOGIT_SCALE + _LOGIT_BIAS
    logits -= logits.max(axis=-1, keepdims=True)            # stable softmax
    e = np.exp(logits)
    probs = e / e.sum(axis=-1, keepdims=True)
    iqa = probs[:, 0].astype(np.float32)                    # prob(high-quality)

    return feats, iqa


def run_embedding_stage(
    cfg: FolderConfig,
    on_progress: Callable[[int, int, str], None] | None = None,
    batch_size: int = 16,
) -> int:
    """Compute SigLIP embeddings + IQA for every photo with embedding_done=False.

    Returns the number of photos processed.
    """
    Session = init_db(cfg.db_path)

    with session_scope(Session) as s:
        pending = (
            s.query(Photo, PipelineState)
            .join(PipelineState, Photo.id == PipelineState.photo_id)
            .filter(PipelineState.embedding_done.is_(False))
            .all()
        )
        pending_ids = [(p.id, p.preview_path) for p, _ in pending]

    if not pending_ids:
        log.info("no photos pending embedding")
        return 0

    total = len(pending_ids)
    log.info("embedding %d photos", total)
    processed = 0

    for start in range(0, total, batch_size):
        chunk = pending_ids[start:start + batch_size]
        try:
            images = []
            valid_chunk = []
            for pid, preview_path in chunk:
                if not preview_path:
                    log.warning("photo %s has no preview_path, skipping", pid)
                    continue
                preview_abs = cfg.state_dir / preview_path
                try:
                    img = Image.open(preview_abs).convert("RGB")
                    images.append(img)
                    valid_chunk.append((pid, preview_path))
                except Exception as exc:
                    log.warning("could not load preview for photo %s: %s", pid, exc)

            if not images:
                continue

            feats, iqa = encode_image_batch(images)

            with session_scope(Session) as s:
                for (pid, _), feat_row, iqa_score in zip(valid_chunk, feats, iqa):
                    blob = feat_row.astype(np.float16).tobytes()
                    emb = s.get(Embedding, pid) or Embedding(photo_id=pid)
                    emb.siglip = blob
                    emb.aesthetic_iqa = float(iqa_score)
                    s.add(emb)
                    ps = s.get(PipelineState, pid)
                    if ps:
                        ps.embedding_done = True
                        s.add(ps)

            processed += len(valid_chunk)
            if on_progress:
                on_progress(processed, total, f"batch {start // batch_size + 1}")

        except Exception as exc:
            log.exception("embed batch failed at start=%d: %s", start, exc)

    log.info("embedding done: %d photos", processed)
    return processed
