"""HDBSCAN + VLM cluster naming pass.

Two-stage approach:
1. HDBSCAN on SigLIP embeddings finds natural visual groupings.
2. Qwen3-VL-2B names each cluster by looking at representative photos.

Usage (CLI): travelcull index <folder> --pass smart_tag
"""
from __future__ import annotations

import gc
import logging
import re
import unicodedata
from pathlib import Path
from typing import Callable

import numpy as np
import torch
from PIL import Image

from travelcull.config import FolderConfig
from travelcull.db import init_db, session_scope
from travelcull.db.models import Embedding, Photo, PhotoTag, PipelineState

log = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────── #
# HDBSCAN helpers                                                               #
# ──────────────────────────────────────────────────────────────────────────── #

def _run_hdbscan(embs_50: np.ndarray) -> np.ndarray:
    """Run HDBSCAN on PCA-reduced embeddings. Returns integer label array (no -1 outliers)."""
    import hdbscan  # installed via pip install hdbscan

    clusterer = hdbscan.HDBSCAN(
        min_cluster_size=5,
        min_samples=2,
        metric="euclidean",
        cluster_selection_method="eom",
    )
    labels = clusterer.fit_predict(embs_50)

    unique_clusters = [lbl for lbl in set(labels) if lbl != -1]
    if not unique_clusters:
        # Degenerate case — all outliers; return single cluster
        return np.zeros(len(labels), dtype=np.int32)

    # Assign outliers to nearest cluster centroid
    centroids = np.stack([embs_50[labels == lbl].mean(axis=0) for lbl in unique_clusters])
    outlier_mask = labels == -1
    if outlier_mask.any():
        outlier_embs = embs_50[outlier_mask]
        dists = np.linalg.norm(outlier_embs[:, None] - centroids[None, :], axis=2)
        nearest_idx = dists.argmin(axis=1)
        labels = labels.copy()
        labels[outlier_mask] = [unique_clusters[i] for i in nearest_idx]

    n_clusters = len(unique_clusters)
    log.info("HDBSCAN: %d clusters, 0 uncategorized after centroid-assignment", n_clusters)
    return labels


def _pick_representatives(embs_50: np.ndarray, cluster_mask: np.ndarray, n: int = 4) -> np.ndarray:
    """Return indices (into the global array) of the n photos closest to the cluster centroid."""
    centroid = embs_50[cluster_mask].mean(axis=0)
    dists = np.linalg.norm(embs_50[cluster_mask] - centroid, axis=1)
    top_local = np.argsort(dists)[:n]
    global_indices = np.where(cluster_mask)[0][top_local]
    return global_indices


# ──────────────────────────────────────────────────────────────────────────── #
# VLM helpers                                                                   #
# ──────────────────────────────────────────────────────────────────────────── #

_VLM_MODEL = None
_VLM_PROC = None
_VLM_NAME: str | None = None


def _load_vlm(model_name: str = "Qwen/Qwen3-VL-2B-Instruct"):
    global _VLM_MODEL, _VLM_PROC, _VLM_NAME
    if _VLM_MODEL is not None:
        return _VLM_MODEL, _VLM_PROC

    log.info("loading VLM %s", model_name)
    from transformers import Qwen3VLForConditionalGeneration, Qwen3VLProcessor

    _VLM_PROC = Qwen3VLProcessor.from_pretrained(model_name)
    _VLM_MODEL = Qwen3VLForConditionalGeneration.from_pretrained(
        model_name,
        torch_dtype=torch.float16,
        device_map="cuda",
    )
    _VLM_MODEL.eval()
    _VLM_NAME = model_name
    vram_gb = torch.cuda.memory_allocated(0) / 1024 ** 3
    log.info("VLM loaded (%.1f GB VRAM)", vram_gb)
    return _VLM_MODEL, _VLM_PROC


def _unload_vlm():
    global _VLM_MODEL, _VLM_PROC, _VLM_NAME
    if _VLM_MODEL is not None:
        del _VLM_MODEL
        del _VLM_PROC
        _VLM_MODEL = None
        _VLM_PROC = None
        _VLM_NAME = None
        gc.collect()
        torch.cuda.empty_cache()
        log.info("VLM unloaded")


def _sanitize_label(raw: str) -> str:
    """Lowercase, ASCII-only, max 4 words, no punctuation."""
    # Strip leading/trailing whitespace and common artifacts
    text = raw.strip().strip('"\'.,;:!?')
    # Normalize unicode to ASCII-compatible
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode("ascii")
    # Keep only alphanumeric + spaces + underscores
    text = re.sub(r"[^a-zA-Z0-9 _]", " ", text)
    text = text.lower().strip()
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text)
    # Max 4 words
    words = text.split()[:4]
    label = " ".join(words) if words else "uncategorized"
    return label


def _name_cluster(images: list[Image.Image], model, proc) -> str:
    """Run the VLM on a list of representative images and return a 2-4 word cluster name."""
    from qwen_vl_utils import process_vision_info

    content: list[dict] = []
    for img in images:
        content.append({"type": "image", "image": img})
    content.append(
        {
            "type": "text",
            "text": (
                "These photos share a visual theme. "
                "Describe the common theme in 2-4 words (lowercase, no punctuation). "
                "Output only the label, nothing else."
            ),
        }
    )

    messages = [{"role": "user", "content": content}]
    text = proc.apply_chat_template(
        messages,
        tokenize=False,
        add_generation_prompt=True,
        add_thinking=False,
    )
    image_inputs, video_inputs = process_vision_info(messages)
    inputs = proc(
        text=[text],
        images=image_inputs,
        videos=video_inputs,
        padding=True,
        return_tensors="pt",
        processor_kwargs={},
    ).to("cuda")

    with torch.no_grad():
        out = model.generate(**inputs, max_new_tokens=24, do_sample=False)
    decoded = proc.decode(out[0][inputs["input_ids"].shape[1]:], skip_special_tokens=True)
    return _sanitize_label(decoded)


# ──────────────────────────────────────────────────────────────────────────── #
# Public entry point                                                            #
# ──────────────────────────────────────────────────────────────────────────── #

def run_smart_cluster_stage(
    cfg: FolderConfig,
    on_progress: Callable[[int, int, str], None] | None = None,
    n_reps: int = 4,
    pca_dims: int = 50,
    vlm_model: str = "Qwen/Qwen3-VL-2B-Instruct",
) -> int:
    """HDBSCAN clustering + VLM cluster naming → populate photo_tags.

    Returns the number of photos tagged.
    """
    Session = init_db(cfg.db_path)

    # ── 1. Load all embeddings ──────────────────────────────────────────── #
    with session_scope(Session) as s:
        rows = (
            s.query(Embedding.photo_id, Embedding.siglip, Photo.preview_path)
            .join(Photo, Embedding.photo_id == Photo.id)
            .join(PipelineState, Embedding.photo_id == PipelineState.photo_id)
            .filter(PipelineState.embedding_done.is_(True))
            .all()
        )

    if not rows:
        log.info("no embeddings found; skipping smart cluster stage")
        return 0

    ids = np.array([r[0] for r in rows])
    preview_paths = [r[2] for r in rows]
    embs_raw = np.stack([np.frombuffer(r[1], dtype=np.float16).copy().astype(np.float32) for r in rows])
    log.info("loaded %d embeddings (shape %s)", len(ids), embs_raw.shape)

    # ── 2. PCA + HDBSCAN ───────────────────────────────────────────────── #
    norms = np.linalg.norm(embs_raw, axis=1, keepdims=True)
    embs_n = embs_raw / np.maximum(norms, 1e-6)

    from sklearn.decomposition import PCA

    effective_dims = min(pca_dims, embs_n.shape[1], embs_n.shape[0] - 1)
    pca = PCA(n_components=effective_dims, random_state=42)
    embs_50 = pca.fit_transform(embs_n)
    log.info("PCA to %d dims, variance explained: %.1f%%", effective_dims, 100 * pca.explained_variance_ratio_.sum())

    labels = _run_hdbscan(embs_50)
    unique_labels = sorted(set(labels))
    n_clusters = len(unique_labels)
    log.info("clustering complete: %d clusters", n_clusters)

    # ── 3. Load VLM (may need to unload SigLIP first) ──────────────────── #
    # Check free VRAM; SigLIP is ~3.5GB, Qwen3-VL-2B is ~4GB
    free_vram = torch.cuda.mem_get_info(0)[0] / 1024 ** 3
    log.info("free VRAM before VLM load: %.1f GB", free_vram)

    if free_vram < 4.5:
        log.info("not enough free VRAM; attempting to unload SigLIP first")
        try:
            from travelcull.ml import embed as _embed_mod
            if _embed_mod._MODEL is not None:
                del _embed_mod._MODEL
                del _embed_mod._PROC
                _embed_mod._MODEL = None
                _embed_mod._PROC = None
                gc.collect()
                torch.cuda.empty_cache()
                log.info("SigLIP unloaded; free VRAM now %.1f GB", torch.cuda.mem_get_info(0)[0] / 1024 ** 3)
        except Exception as e:
            log.warning("could not unload SigLIP: %s", e)

    vlm, vlm_proc = _load_vlm(vlm_model)

    # ── 4. Name each cluster ────────────────────────────────────────────── #
    cluster_names: dict[int, str] = {}
    for cluster_idx, cluster_label in enumerate(unique_labels):
        cluster_mask = labels == cluster_label
        cluster_size = cluster_mask.sum()

        rep_indices = _pick_representatives(embs_50, cluster_mask, n=min(n_reps, cluster_size))
        images: list[Image.Image] = []
        for idx in rep_indices:
            ppath = preview_paths[idx]
            if ppath:
                abs_path = cfg.state_dir / ppath
                try:
                    img = Image.open(abs_path).convert("RGB")
                    # Resize to 336x336 max to keep VRAM low
                    img.thumbnail((336, 336), Image.LANCZOS)
                    images.append(img)
                except Exception as exc:
                    log.warning("could not open preview %s: %s", ppath, exc)

        if not images:
            cluster_names[cluster_label] = "uncategorized"
            log.warning("cluster %d: no loadable images, falling back to 'uncategorized'", cluster_label)
            continue

        try:
            name = _name_cluster(images, vlm, vlm_proc)
        except Exception as exc:
            log.warning("VLM naming failed for cluster %d: %s", cluster_label, exc)
            name = f"cluster {cluster_label}"

        cluster_names[cluster_label] = name
        log.info("cluster %d (%d photos) → %r", cluster_label, cluster_size, name)

        if on_progress:
            on_progress(cluster_idx + 1, n_clusters, f"named: {name!r}")

    # ── 5. Resolve naming conflicts (two clusters with same name) ─────── #
    from collections import Counter

    name_counts = Counter(cluster_names.values())
    # Add disambiguation suffix for duplicates
    seen: dict[str, int] = {}
    final_names: dict[int, str] = {}
    for lbl, name in cluster_names.items():
        if name_counts[name] > 1:
            seen[name] = seen.get(name, 0) + 1
            disambig = f"{name} {seen[name]}"
        else:
            disambig = name
        final_names[lbl] = disambig

    # ── 6. Unload VLM ──────────────────────────────────────────────────── #
    _unload_vlm()

    # ── 7. Write photo_tags ─────────────────────────────────────────────── #
    photo_ids_list = ids.tolist()
    with session_scope(Session) as s:
        # Wipe existing tags for all affected photos
        for pid in photo_ids_list:
            for old in s.query(PhotoTag).filter(PhotoTag.photo_id == pid).all():
                s.delete(old)
        s.flush()

        for i, pid in enumerate(photo_ids_list):
            cluster_label = int(labels[i])
            tag_name = final_names[cluster_label]
            t = PhotoTag(photo_id=pid, tag=tag_name, score=1.0)
            s.add(t)

        # Mark pipeline state as vl_done
        for pid in photo_ids_list:
            ps = s.get(PipelineState, pid)
            if ps:
                ps.vl_done = True
                s.add(ps)

    log.info(
        "smart cluster stage done: %d photos → %d clusters",
        len(photo_ids_list),
        n_clusters,
    )
    return len(photo_ids_list)
