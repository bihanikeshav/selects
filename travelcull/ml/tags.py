"""Zero-shot tag classification via SigLIP text prompts. CPU-only after embeddings exist."""
from __future__ import annotations

import logging
from typing import Callable

import numpy as np
import torch

from travelcull.config import FolderConfig
from travelcull.db import init_db, session_scope
from travelcull.db.models import Embedding, PhotoTag, PipelineState

from .embed import encode_text_prompts

log = logging.getLogger(__name__)

# Default taxonomy for travel photos. Each tag has a list of prompts —
# we mean-pool across prompts for richer concept coverage.
DEFAULT_TAG_PROMPTS: dict[str, list[str]] = {
    "landscape":    ["a scenic landscape photograph", "mountains and valleys", "a panoramic view of nature"],
    "mountain":     ["snow-capped mountains", "a high mountain peak", "a rugged mountain range"],
    "sky":          ["a sky full of clouds", "a vivid sunset", "stars in the night sky"],
    "monastery":    ["a Buddhist monastery", "a temple with prayer flags", "religious architecture"],
    "architecture": ["a building or structure", "traditional architecture", "an arched doorway"],
    "portrait":     ["a portrait of a person", "a person's face", "a close-up of someone"],
    "people":       ["a group of people", "people interacting", "candid people on a trip"],
    "food":         ["a plate of food", "a meal on a table", "local cuisine"],
    "transit":      ["a road through mountains", "a vehicle on a journey", "travel in transit"],
    "interior":     ["the inside of a room", "an interior space", "indoor lighting"],
    "water":        ["a river or lake", "flowing water", "a reflection on water"],
    "night":        ["a photograph taken at night", "low-light scene", "city lights at night"],
    "animal":       ["an animal", "wildlife in nature", "a domesticated animal"],
    "abstract":     ["an abstract pattern", "a close-up texture", "a minimalist composition"],
    "documents":    ["a document or sign", "text on a page", "a screenshot or receipt"],
}


def run_tag_stage(
    cfg: FolderConfig,
    on_progress: Callable[[int, int, str], None] | None = None,
    top_k: int = 3,
    min_score: float = 0.0,
    tag_prompts: dict[str, list[str]] | None = None,
) -> int:
    """For each photo with an embedding, compute cosine-sim against tag prompts and store top-k.

    Note: SigLIP-SO400M raw cosine similarities between images and text tend to be in the
    range 0.0-0.15, which is much lower than CLIP models. min_score defaults to 0.0 to store
    all top-k tags; downstream consumers can filter by rank rather than absolute score.

    Returns the number of photos tagged.
    """
    prompts = tag_prompts or DEFAULT_TAG_PROMPTS
    Session = init_db(cfg.db_path)

    # Build prompt -> tag index and encode text features on GPU once
    tag_names: list[str] = []
    flat_prompts: list[str] = []
    tag_index: list[int] = []
    for i, (tag, prompt_list) in enumerate(prompts.items()):
        tag_names.append(tag)
        for p in prompt_list:
            flat_prompts.append(p)
            tag_index.append(i)

    txt_feats = encode_text_prompts(flat_prompts)              # [P, 1152] float32 (on whatever device encode uses)
    device = txt_feats.device
    n_tags = len(tag_names)

    # Mean-pool prompts per tag -> [T, 1152]
    tag_feats = torch.zeros((n_tags, txt_feats.shape[1]), device=device, dtype=txt_feats.dtype)
    counts = torch.zeros(n_tags, device=device, dtype=txt_feats.dtype)
    for j, t_idx in enumerate(tag_index):
        tag_feats[t_idx] += txt_feats[j]
        counts[t_idx] += 1
    tag_feats = torch.nn.functional.normalize(tag_feats / counts[:, None], dim=-1)

    # Load all embeddings that have embedding_done=True
    with session_scope(Session) as s:
        rows = (
            s.query(Embedding.photo_id, Embedding.siglip)
            .join(PipelineState, Embedding.photo_id == PipelineState.photo_id)
            .filter(PipelineState.embedding_done.is_(True))
            .all()
        )

    if not rows:
        log.info("no embeddings found, skipping tag stage")
        return 0

    total = len(rows)
    log.info("tagging %d photos", total)
    ids = [r[0] for r in rows]
    feats_np = np.stack([np.frombuffer(r[1], dtype=np.float16).copy() for r in rows])  # [N, 1152]
    feats = torch.from_numpy(feats_np).float().to(device)   # same device as text features
    feats = torch.nn.functional.normalize(feats, dim=-1)

    # Batch photo-vs-tag similarity to avoid OOM on large folders
    chunk = 1024
    all_top_indices: list[np.ndarray] = []
    all_top_scores: list[np.ndarray] = []
    for start in range(0, total, chunk):
        sub = feats[start:start + chunk]
        sims = sub @ tag_feats.T                               # [chunk, T]
        top_v, top_i = torch.topk(sims, k=min(top_k, n_tags), dim=-1)
        all_top_indices.append(top_i.cpu().numpy())
        all_top_scores.append(top_v.cpu().numpy())

    top_indices = np.concatenate(all_top_indices, axis=0)     # [N, k]
    top_scores  = np.concatenate(all_top_scores,  axis=0)     # [N, k]

    # Write tags — idempotent: wipe existing rows first, then rewrite
    with session_scope(Session) as s:
        for pid in ids:
            for old in s.query(PhotoTag).filter(PhotoTag.photo_id == pid).all():
                s.delete(old)
        s.flush()

        for k, pid in enumerate(ids):
            for j in range(top_indices.shape[1]):
                score = float(top_scores[k, j])
                if score < min_score:
                    continue
                t = PhotoTag(photo_id=pid, tag=tag_names[top_indices[k, j]], score=score)
                s.add(t)

        # Mark pipeline state as vl_done
        for pid in ids:
            ps = s.get(PipelineState, pid)
            if ps:
                ps.vl_done = True
                s.add(ps)

    if on_progress:
        on_progress(total, total, "tags written")

    log.info("tagging done: %d photos", total)
    return total
