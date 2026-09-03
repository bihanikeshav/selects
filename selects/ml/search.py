"""Free-text photo search using SigLIP image-text embedding similarity.

User types "monastery interior at dusk" → we encode the text with SigLIP, score
every photo's already-cached SigLIP embedding against it, return top-K.
"""
from __future__ import annotations

import logging

import numpy as np

from sqlalchemy import func

from selects.config import FolderConfig
from selects.db import init_db, session_scope
from selects.db.models import Embedding, Photo

log = logging.getLogger(__name__)

# (n_rows, matrix, ids, shas) keyed by db path. Invalidated when the row count changes.
_LIB_CACHE: dict[str, tuple[int, np.ndarray, list[int], list[str]]] = {}
_QUERY_CACHE: dict[str, np.ndarray] = {}
_QUERY_CACHE_MAX = 64


def search_photos(cfg: FolderConfig, query: str, k: int = 60) -> list[tuple[int, str, float]]:
    """Return [(photo_id, sha256, score), ...] sorted by relevance desc."""
    from selects.ml.embed import encode_text_prompts

    txt = encode_text_prompts([query])[0]        # [1152] float32, already L2-normalized

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
    key = query.strip().lower()
    hit = _QUERY_CACHE.get(key)
    if hit is not None:
        return hit
    from selects.ml.warmup import wait_for_warmup

    wait_for_warmup()
    hit = _QUERY_CACHE.get(key)
    if hit is not None:
        return hit
    from selects.ml.embed import encode_text_prompts

    vec = encode_text_prompts([query])[0]       # [1152] float32, already L2-normalized
    if len(_QUERY_CACHE) >= _QUERY_CACHE_MAX:
        _QUERY_CACHE.clear()
    _QUERY_CACHE[key] = vec
    return vec


def library_embedding_matrix(cfg: FolderConfig) -> tuple[np.ndarray, list[int], list[str]]:
    """Cached L2-normalised [N,1152] matrix plus parallel id/sha lists."""
    Session = init_db(cfg.db_path)
    key = str(cfg.db_path)
    with session_scope(Session) as s:
        n = (
            s.query(func.count(Photo.id))
            .join(Embedding, Embedding.photo_id == Photo.id)
            .scalar()
        ) or 0
        cached = _LIB_CACHE.get(key)
        if cached is not None and cached[0] == n:
            return cached[1], cached[2], cached[3]
        if n == 0:
            empty = np.zeros((0, 1152), dtype=np.float32)
            _LIB_CACHE[key] = (0, empty, [], [])
            return empty, [], []
        rows = (
            s.query(Photo.id, Photo.sha256, Embedding.siglip)
            .join(Embedding, Embedding.photo_id == Photo.id)
            .all()
        )
    ids = [r[0] for r in rows]
    shas = [r[1] for r in rows]
    mat = siglip_bytes_to_matrix([r[2] for r in rows])
    _LIB_CACHE[key] = (len(rows), mat, ids, shas)
    return mat, ids, shas


def siglip_bytes_to_matrix(blobs: list[bytes]) -> np.ndarray:
    """Stack raw fp16 SigLIP embedding blobs into an L2-normalized [N, 1152] float32 matrix."""
    embs = np.stack([np.frombuffer(b, dtype=np.float16).astype(np.float32) for b in blobs])
    embs /= np.linalg.norm(embs, axis=1, keepdims=True) + 1e-9
    return embs


def cosine_scores(embs: np.ndarray, query_vec: np.ndarray) -> np.ndarray:
    """Cosine similarity of each row in *embs* (already L2-normalized) against *query_vec*."""
    return embs @ query_vec
