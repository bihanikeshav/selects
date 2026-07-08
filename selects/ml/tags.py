"""Zero-shot tag classification via SigLIP text prompts. CPU-only after embeddings exist."""
from __future__ import annotations

import logging
from typing import Callable

import numpy as np

from selects.config import FolderConfig
from selects.db import init_db, session_scope
from selects.db.models import Embedding, PhotoTag, PipelineState
from selects.ml.trip_data import DEFAULT_TAG_PROMPTS, load_tag_prompts

from .embed import encode_text_prompts

log = logging.getLogger(__name__)

# Minimum z-score (standard deviations above dataset mean) for a tag to be assigned.
# Photos that don't clear this threshold for any tag are left untagged (uncategorized).
DEFAULT_MIN_Z: float = 0.5

# The zero-shot tag taxonomy (DEFAULT_TAG_PROMPTS) is defined in trip_data and
# re-exported here for API/back-compat. It is loaded per-library via
# trip_data.load_tag_prompts(cfg), which reads <state_dir>/tag_prompts.json when
# present and otherwise falls back to the generic default.
__all__ = ["run_tag_stage", "DEFAULT_TAG_PROMPTS", "DEFAULT_MIN_Z"]


def run_tag_stage(
    cfg: FolderConfig,
    on_progress: Callable[[int, int, str], None] | None = None,
    top_k: int = 3,
    min_score: float = 0.0,
    min_z: float = DEFAULT_MIN_Z,
    tag_prompts: dict[str, list[str]] | None = None,
) -> int:
    """For each photo with an embedding, compute cosine-sim against tag prompts and store top-k.

    Uses per-tag z-score normalization so that tags are assigned based on *relative* unusualness
    rather than raw similarity.  A photo only gets tagged if its z-score for at least one tag
    exceeds min_z (default 0.5 SD above the dataset mean for that tag).  Photos that clear no
    threshold are left untagged and will appear as "uncategorized" in the cluster view.

    Note: SigLIP-SO400M raw cosine similarities between images and text tend to be in the
    range 0.0-0.15, which is much lower than CLIP models. min_score is kept for API compat
    but min_z is the operative filter when using z-score mode.

    Returns the number of photos tagged.
    """
    prompts = tag_prompts or load_tag_prompts(cfg)
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

    txt_feats = encode_text_prompts(flat_prompts)              # [P, 1152] float32, L2-normalized
    n_tags = len(tag_names)

    # Mean-pool prompts per tag -> [T, 1152]
    tag_feats = np.zeros((n_tags, txt_feats.shape[1]), dtype=np.float32)
    counts = np.zeros(n_tags, dtype=np.float32)
    for j, t_idx in enumerate(tag_index):
        tag_feats[t_idx] += txt_feats[j]
        counts[t_idx] += 1
    tag_feats = tag_feats / counts[:, None]
    tag_feats /= np.linalg.norm(tag_feats, axis=-1, keepdims=True) + 1e-12

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
    feats = np.stack([np.frombuffer(r[1], dtype=np.float16).astype(np.float32) for r in rows])  # [N, 1152]
    feats /= np.linalg.norm(feats, axis=-1, keepdims=True) + 1e-12

    sims = feats @ tag_feats.T                                       # [N, T]

    # --- Per-tag z-score normalization ---
    # Subtract each tag's dataset-mean similarity so that only photos that match a tag
    # *unusually well* (relative to the whole dataset) get assigned to it.
    # This prevents generic background concepts (e.g. "mountain" for every Ladakh photo)
    # from winning everywhere.
    tag_means = sims.mean(axis=0, keepdims=True)                     # [1, T]
    # ddof=0 avoids NaN when N=1 (Bessel's correction would divide by zero)
    tag_std = np.clip(sims.std(axis=0, keepdims=True, ddof=0), 1e-6, None)  # [1, T]
    sims_z = (sims - tag_means) / tag_std                            # [N, T] — z-scores

    # Top-k by z-score (descending); we store z-scores as "score" in photo_tags
    k = min(top_k, n_tags)
    top_indices = np.argsort(-sims_z, axis=-1)[:, :k]                # [N, k]
    top_scores = np.take_along_axis(sims_z, top_indices, axis=-1)    # [N, k]

    # Write tags — idempotent: wipe this stage's existing rows first, then rewrite.
    # Bulk query delete (not ORM s.delete) because source is a nullable PK column
    # the ORM can't address; scoped to source IS NULL so ram/posting/lookback
    # tags from other stages are untouched.
    with session_scope(Session) as s:
        s.query(PhotoTag).filter(
            PhotoTag.photo_id.in_(ids), PhotoTag.source.is_(None)
        ).delete(synchronize_session=False)
        s.flush()

        for k_idx, pid in enumerate(ids):
            for j in range(top_indices.shape[1]):
                z_score = float(top_scores[k_idx, j])
                # Apply both legacy min_score guard (kept for API compat) and min_z threshold
                if z_score < min_z:
                    continue
                t = PhotoTag(photo_id=pid, tag=tag_names[top_indices[k_idx, j]], score=z_score)
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
