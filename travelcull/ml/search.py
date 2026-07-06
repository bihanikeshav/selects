"""Free-text photo search using SigLIP image-text embedding similarity.

User types "monastery interior at dusk" → we encode the text with SigLIP, score
every photo's already-cached SigLIP embedding against it, return top-K.
"""
from __future__ import annotations

import logging

import numpy as np

from travelcull.config import FolderConfig
from travelcull.db import init_db, session_scope
from travelcull.db.models import Embedding, Photo

log = logging.getLogger(__name__)


def search_photos(cfg: FolderConfig, query: str, k: int = 60) -> list[tuple[int, str, float]]:
    """Return [(photo_id, sha256, score), ...] sorted by relevance desc."""
    from travelcull.ml.embed import encode_text_prompts

    txt = encode_text_prompts([query])           # [1, 1152] cuda
    txt = (txt / txt.norm(dim=-1, keepdim=True)).cpu().float().numpy().squeeze(0)

    Session = init_db(cfg.db_path)
    with session_scope(Session) as s:
        rows = s.query(Photo.id, Photo.sha256, Embedding.siglip).join(
            Embedding, Embedding.photo_id == Photo.id
        ).all()

    if not rows:
        return []

    ids = [r[0] for r in rows]
    shas = [r[1] for r in rows]
    embs = np.stack([np.frombuffer(r[2], dtype=np.float16).astype(np.float32) for r in rows])
    embs /= np.linalg.norm(embs, axis=1, keepdims=True) + 1e-9

    sims = embs @ txt
    top = np.argsort(-sims)[:k]
    return [(ids[i], shas[i], float(sims[i])) for i in top]


def embed_query(query: str) -> np.ndarray:
    """Encode *query* text with SigLIP and L2-normalize. Returns a [1152] float32 vector.

    Split out from :func:`search_photos` so callers (e.g. the hybrid search
    endpoint) can score an arbitrary photo subset against the same query
    embedding without re-running text encoding per candidate set.
    """
    from travelcull.ml.embed import encode_text_prompts

    txt = encode_text_prompts([query])
    txt = (txt / txt.norm(dim=-1, keepdim=True)).cpu().float().numpy().squeeze(0)
    return txt


def siglip_bytes_to_matrix(blobs: list[bytes]) -> np.ndarray:
    """Stack raw fp16 SigLIP embedding blobs into an L2-normalized [N, 1152] float32 matrix."""
    embs = np.stack([np.frombuffer(b, dtype=np.float16).astype(np.float32) for b in blobs])
    embs /= np.linalg.norm(embs, axis=1, keepdims=True) + 1e-9
    return embs


def cosine_scores(embs: np.ndarray, query_vec: np.ndarray) -> np.ndarray:
    """Cosine similarity of each row in *embs* (already L2-normalized) against *query_vec*."""
    return embs @ query_vec
