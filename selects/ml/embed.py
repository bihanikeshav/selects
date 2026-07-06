"""SigLIP-SO400M image embedding pass. fp16 on CUDA. Batched."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

import numpy as np
import torch
from PIL import Image

from selects.config import FolderConfig
from selects.db import init_db, session_scope
from selects.db.models import Embedding, PipelineState, Photo

log = logging.getLogger(__name__)

_MODEL = None
_PROC = None
_IQA_TEXT_FEATS: torch.Tensor | None = None

_DEVICE = "cuda" if torch.cuda.is_available() else "cpu"

# CLIP-IQA-style antonym pair prompts (Wang et al. 2023).
# Softmax over the pair gives prob(positive) = aesthetic IQA in [0, 1].
IQA_POS = "a high-quality photograph"
IQA_NEG = "a low-quality photograph"


def _load():
    global _MODEL, _PROC
    if _MODEL is not None:
        return _MODEL, _PROC
    from transformers import AutoModel, AutoProcessor

    name = "google/siglip-so400m-patch14-384"
    log.info("loading SigLIP model %s (device=%s)", name, _DEVICE)
    dtype = torch.float16 if _DEVICE == "cuda" else torch.float32
    _MODEL = AutoModel.from_pretrained(name, dtype=dtype).to(_DEVICE).eval()
    _PROC = AutoProcessor.from_pretrained(name)
    log.info("SigLIP loaded")
    return _MODEL, _PROC


def _extract_tensor(output) -> torch.Tensor:
    """Extract tensor from model output — handles both raw Tensor and ModelOutput objects.

    In transformers 5.x, get_text_features / get_image_features return a
    BaseModelOutputWithPooling whose pooler_output is the CLS-pooled embedding.
    """
    if isinstance(output, torch.Tensor):
        return output
    # ModelOutput / BaseModelOutputWithPooling
    if hasattr(output, "pooler_output") and output.pooler_output is not None:
        return output.pooler_output
    if hasattr(output, "last_hidden_state"):
        return output.last_hidden_state[:, 0]  # CLS token
    raise ValueError(f"Cannot extract tensor from {type(output)}")


def encode_text_prompts(prompts: list[str]) -> torch.Tensor:
    """Return L2-normalized [N, 1152] text embeddings on cuda."""
    model, proc = _load()
    with torch.no_grad():
        inp = proc(text=prompts, return_tensors="pt", padding=True, truncation=True).to(_DEVICE)
        raw = model.get_text_features(**inp)
        feats = _extract_tensor(raw)
        feats = torch.nn.functional.normalize(feats.float(), dim=-1)
    return feats


def encode_image_batch(images: list[Image.Image]) -> tuple[torch.Tensor, np.ndarray]:
    """Return (image_feats_normed [B,1152] cpu float32, iqa_scores [B] cpu float32 in [0,1]).

    IQA is computed from the same forward pass — image features are compared against
    the pos/neg IQA text prompts via softmax over the antonym pair.
    """
    global _IQA_TEXT_FEATS
    model, proc = _load()
    if _IQA_TEXT_FEATS is None:
        _IQA_TEXT_FEATS = encode_text_prompts([IQA_POS, IQA_NEG])  # [2, 1152] float32, computed once
    iqa_text = _IQA_TEXT_FEATS

    with torch.no_grad():
        img_dtype = torch.float16 if _DEVICE == "cuda" else torch.float32
        inp = proc(images=images, return_tensors="pt").to(_DEVICE, img_dtype)
        raw = model.get_image_features(**inp)
        feats = _extract_tensor(raw)                        # [B, 1152] fp16 or float32
        feats = torch.nn.functional.normalize(feats.float(), dim=-1)  # [B, 1152] float32

        sim = feats @ iqa_text.T                            # [B, 2]
        # Use model's logit_scale if available (matches SigLIP's sigmoid training)
        scale = model.logit_scale.exp().item() if hasattr(model, "logit_scale") else 10.0
        bias = model.logit_bias.item() if hasattr(model, "logit_bias") else 0.0
        probs = torch.softmax(sim * scale + bias, dim=-1)
        iqa = probs[:, 0].cpu().numpy()                     # prob(high-quality)

    return feats.cpu(), iqa


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
                    blob = feat_row.numpy().astype(np.float16).tobytes()
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
