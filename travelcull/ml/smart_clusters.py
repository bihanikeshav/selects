"""HDBSCAN + VLM cluster naming pass — M2 v3.

Dual-resolution clustering with temporal/GPS pre-segmentation and
collision-suppressed VLM naming.

Two-stage approach:
1. Session-block pre-segmentation by time gaps (>90 min) or GPS jumps (>500m).
2. "Posting" pass: HDBSCAN(min_cluster_size=5) within each session block
   → tight visual groups for carousels.
3. "Lookback" pass: HDBSCAN(min_cluster_size=20) globally
   → broad themes for browsing the trip.
4. VLM (Qwen3-VL-2B) names each cluster using collision suppression.

Usage (CLI): travelcull index <folder> --pass smart_tag
"""
from __future__ import annotations

import gc
import logging
import math
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
# Session block pre-segmentation                                                #
# ──────────────────────────────────────────────────────────────────────────── #

_SESSION_GAP_MINUTES = 90      # time gap threshold
_GPS_JUMP_METERS = 500.0       # GPS distance threshold


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Return distance in metres between two lat/lon points."""
    R = 6_371_000.0  # Earth radius in metres
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def session_blocks(photos: list[dict]) -> list[list[int]]:
    """Split photos into contiguous session blocks.

    A new block starts when:
      - time gap > 90 minutes from previous photo, OR
      - GPS distance > 500 m from previous photo (if both have GPS)

    photos: list of dicts with keys 'idx', 'taken_at' (datetime|None),
            'gps_lat' (float|None), 'gps_lon' (float|None)

    Returns list of [photo_index, ...] lists (indices into original array).
    """
    if not photos:
        return []

    blocks: list[list[int]] = [[photos[0]["idx"]]]

    for i in range(1, len(photos)):
        prev = photos[i - 1]
        curr = photos[i]
        new_block = False

        # Time gap check
        if prev["taken_at"] is not None and curr["taken_at"] is not None:
            gap_min = (curr["taken_at"] - prev["taken_at"]).total_seconds() / 60.0
            if gap_min > _SESSION_GAP_MINUTES:
                new_block = True

        # GPS jump check (only if both have GPS, and we didn't already split)
        if (
            not new_block
            and prev["gps_lat"] is not None
            and curr["gps_lat"] is not None
        ):
            dist_m = _haversine_m(
                prev["gps_lat"], prev["gps_lon"],
                curr["gps_lat"], curr["gps_lon"],
            )
            if dist_m > _GPS_JUMP_METERS:
                new_block = True

        if new_block:
            blocks.append([curr["idx"]])
        else:
            blocks[-1].append(curr["idx"])

    return blocks


# ──────────────────────────────────────────────────────────────────────────── #
# HDBSCAN helpers                                                               #
# ──────────────────────────────────────────────────────────────────────────── #

def _run_hdbscan_on(embs: np.ndarray, min_cluster_size: int) -> np.ndarray:
    """Run UMAP + HDBSCAN on embeddings. Returns integer label array (no -1 outliers)."""
    import hdbscan
    import umap  # umap-learn

    n = len(embs)
    if n < max(min_cluster_size, 4):
        # too small to cluster meaningfully — one cluster
        return np.zeros(n, dtype=np.int32)

    # UMAP reduction for better clustering geometry
    n_components = min(10, n - 1)
    n_neighbors = max(2, min(15, n - 1))

    log.debug("UMAP: n=%d, n_components=%d, n_neighbors=%d", n, n_components, n_neighbors)
    reducer = umap.UMAP(
        n_components=n_components,
        n_neighbors=n_neighbors,
        metric="cosine",
        random_state=42,
        low_memory=True,
    )
    reduced = reducer.fit_transform(embs)

    clusterer = hdbscan.HDBSCAN(
        min_cluster_size=min(min_cluster_size, max(2, n // 2)),
        min_samples=2,
        metric="euclidean",
        cluster_selection_method="eom",
    )
    labels = clusterer.fit_predict(reduced)

    unique_clusters = [lbl for lbl in set(labels) if lbl != -1]
    if not unique_clusters:
        # Degenerate: all outliers → single cluster
        return np.zeros(n, dtype=np.int32)

    # Assign outliers to nearest centroid
    centroids = np.stack([reduced[labels == lbl].mean(axis=0) for lbl in unique_clusters])
    outlier_mask = labels == -1
    if outlier_mask.any():
        outlier_embs = reduced[outlier_mask]
        dists = np.linalg.norm(outlier_embs[:, None] - centroids[None, :], axis=2)
        nearest_idx = dists.argmin(axis=1)
        labels = labels.copy()
        labels[outlier_mask] = [unique_clusters[i] for i in nearest_idx]

    return labels.astype(np.int32)


def _pick_representatives(embs: np.ndarray, cluster_mask: np.ndarray, n: int = 4) -> np.ndarray:
    """Return global indices of the n photos closest to their cluster centroid."""
    centroid = embs[cluster_mask].mean(axis=0)
    dists = np.linalg.norm(embs[cluster_mask] - centroid, axis=1)
    top_local = np.argsort(dists)[:n]
    return np.where(cluster_mask)[0][top_local]


# ──────────────────────────────────────────────────────────────────────────── #
# VLM helpers                                                                   #
# ──────────────────────────────────────────────────────────────────────────── #

_VLM_MODEL = None
_VLM_PROC = None
_VLM_NAME: str | None = None
_VLM_DEVICE: str | None = None


def _load_vlm(model_name: str = "Qwen/Qwen3-VL-2B-Instruct"):
    global _VLM_MODEL, _VLM_PROC, _VLM_NAME, _VLM_DEVICE
    if _VLM_MODEL is not None:
        return _VLM_MODEL, _VLM_PROC

    _VLM_DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
    dtype = torch.float16 if _VLM_DEVICE == "cuda" else torch.float32
    log.info("loading VLM %s (device=%s)", model_name, _VLM_DEVICE)
    from transformers import Qwen3VLForConditionalGeneration, Qwen3VLProcessor

    _VLM_PROC = Qwen3VLProcessor.from_pretrained(model_name)
    _VLM_MODEL = Qwen3VLForConditionalGeneration.from_pretrained(
        model_name,
        torch_dtype=dtype,
        device_map=_VLM_DEVICE,
    )
    _VLM_MODEL.eval()
    _VLM_NAME = model_name
    if _VLM_DEVICE == "cuda":
        vram_gb = torch.cuda.memory_allocated(0) / 1024 ** 3
        log.info("VLM loaded (%.1f GB VRAM)", vram_gb)
    else:
        log.info("VLM loaded (cpu)")
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
    text = raw.strip().strip('"\'.,;:!?')
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^a-zA-Z0-9 _]", " ", text)
    text = text.lower().strip()
    text = re.sub(r"\s+", " ", text)
    words = text.split()[:4]
    return " ".join(words) if words else "uncategorized"


def _call_vlm(images: list[Image.Image], prompt: str, model, proc) -> str:
    """Run the VLM with given images and prompt; return sanitized label."""
    from qwen_vl_utils import process_vision_info

    content: list[dict] = []
    for img in images:
        content.append({"type": "image", "image": img})
    content.append({"type": "text", "text": prompt})

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
    ).to(_VLM_DEVICE)

    with torch.no_grad():
        out = model.generate(**inputs, max_new_tokens=24, do_sample=False)
    decoded = proc.decode(out[0][inputs["input_ids"].shape[1]:], skip_special_tokens=True)
    return _sanitize_label(decoded)


def _name_clusters_iteratively(
    cluster_representatives: dict[int, list[Image.Image]],
    model,
    proc,
) -> dict[int, str]:
    """Name clusters with collision suppression.

    Sorts clusters by size descending so the largest get the most "generic"
    naming budget first. Each subsequent cluster is told not to reuse labels
    already assigned.
    """
    used_labels: set[str] = set()
    names: dict[int, str] = {}

    # Sort by number of representative images (proxy for cluster size)
    sorted_clusters = sorted(cluster_representatives.items(), key=lambda x: -len(x[1]))

    for cluster_id, images in sorted_clusters:
        used_list = sorted(used_labels)
        used_str = ", ".join(f'"{l}"' for l in used_list[-20:])  # cap to 20 for prompt length

        base_prompt = (
            "These photos share a visual theme. "
            "Describe the common theme in 2-4 words, lowercase, no punctuation. "
            "Be very specific (e.g., 'monastery courtyard' beats 'buddhist temple', "
            "'high altitude desert road' beats 'mountain road'). "
        )
        if used_str:
            base_prompt += (
                f"Labels already used for other clusters: {used_str}. "
                "Do NOT reuse or closely rephrase any of these — pick something distinct. "
            )
        base_prompt += "Output only the label, nothing else."

        try:
            label = _call_vlm(images, base_prompt, model, proc)
        except Exception as exc:
            log.warning("VLM naming failed for cluster %d: %s", cluster_id, exc)
            label = f"cluster {cluster_id}"

        # If still a duplicate, retry once with stronger instruction
        if label in used_labels:
            retry_prompt = base_prompt + (
                f" Your previous attempt produced '{label}' which is already used. "
                "Try a completely different, more specific framing."
            )
            try:
                label = _call_vlm(images, retry_prompt, model, proc)
            except Exception:
                pass
            # If still a duplicate after retry, append cluster_id to disambiguate
            if label in used_labels:
                label = f"{label} {cluster_id}"

        used_labels.add(label)
        names[cluster_id] = label
        log.info("cluster %d → %r", cluster_id, label)

    return names


# ──────────────────────────────────────────────────────────────────────────── #
# Public entry point                                                            #
# ──────────────────────────────────────────────────────────────────────────── #

def run_smart_cluster_stage(
    cfg: FolderConfig,
    on_progress: Callable[[int, int, str], None] | None = None,
    n_reps: int = 4,
    vlm_model: str = "Qwen/Qwen3-VL-2B-Instruct",
) -> int:
    """Dual-resolution HDBSCAN clustering + VLM cluster naming → populate photo_tags.

    Produces two sets of cluster tags per photo:
    - source='posting': tight session-block clusters (min_cluster_size=5)
    - source='lookback': broad global clusters (min_cluster_size=20)

    Returns the number of photos tagged.
    """
    # Ensure source column exists
    from travelcull.ml.ram_tags import _migrate_add_source_column
    _migrate_add_source_column(cfg.db_path)

    Session = init_db(cfg.db_path)

    # ── 1. Load all embeddings + photo metadata ─────────────────────────── #
    with session_scope(Session) as s:
        rows = (
            s.query(
                Embedding.photo_id,
                Embedding.siglip,
                Photo.preview_path,
                Photo.taken_at,
                Photo.gps_lat,
                Photo.gps_lon,
            )
            .join(Photo, Embedding.photo_id == Photo.id)
            .join(PipelineState, Embedding.photo_id == PipelineState.photo_id)
            .filter(PipelineState.embedding_done.is_(True))
            .all()
        )

    if not rows:
        log.info("no embeddings found; skipping smart cluster stage")
        return 0

    # Sort by taken_at for session block segmentation
    rows_sorted = sorted(rows, key=lambda r: (r[3] is None, r[3]))

    ids = np.array([r[0] for r in rows_sorted])
    preview_paths = [r[2] for r in rows_sorted]
    embs_raw = np.stack([
        np.frombuffer(r[1], dtype=np.float16).copy().astype(np.float32)
        for r in rows_sorted
    ])
    log.info("loaded %d embeddings (shape %s)", len(ids), embs_raw.shape)

    # Normalize embeddings
    norms = np.linalg.norm(embs_raw, axis=1, keepdims=True)
    embs_n = embs_raw / np.maximum(norms, 1e-6)

    # ── 2. Session block pre-segmentation ──────────────────────────────── #
    photo_meta = [
        {
            "idx": i,
            "taken_at": rows_sorted[i][3],
            "gps_lat": rows_sorted[i][4],
            "gps_lon": rows_sorted[i][5],
        }
        for i in range(len(rows_sorted))
    ]
    blocks = session_blocks(photo_meta)
    log.info("session pre-segmentation: %d blocks from %d photos", len(blocks), len(ids))
    if on_progress:
        on_progress(0, 2, f"segmented into {len(blocks)} session blocks")

    # ── 3. "Posting" pass: HDBSCAN per session block ───────────────────── #
    # Global label counter to keep posting cluster IDs unique across blocks
    posting_labels_global = np.full(len(ids), -1, dtype=np.int32)
    global_cluster_counter = 0

    for block_indices in blocks:
        if len(block_indices) < 2:
            # Single photo block → its own cluster
            posting_labels_global[block_indices[0]] = global_cluster_counter
            global_cluster_counter += 1
            continue

        block_embs = embs_n[block_indices]
        local_labels = _run_hdbscan_on(block_embs, min_cluster_size=5)
        unique_local = sorted(set(local_labels))

        for local_lbl in unique_local:
            for i, global_idx in enumerate(block_indices):
                if local_labels[i] == local_lbl:
                    posting_labels_global[global_idx] = global_cluster_counter + local_lbl

        global_cluster_counter += len(unique_local)

    unique_posting = sorted(set(posting_labels_global.tolist()))
    log.info("posting pass: %d clusters across %d session blocks", len(unique_posting), len(blocks))

    # ── 4. "Lookback" pass: HDBSCAN globally ───────────────────────────── #
    lookback_labels = _run_hdbscan_on(embs_n, min_cluster_size=20)
    unique_lookback = sorted(set(lookback_labels.tolist()))
    log.info("lookback pass: %d global clusters", len(unique_lookback))
    if on_progress:
        on_progress(1, 2, f"clustered: {len(unique_posting)} posting, {len(unique_lookback)} lookback")

    # ── 5. Free VRAM before loading VLM ────────────────────────────────── #
    free_vram = torch.cuda.mem_get_info(0)[0] / 1024 ** 3
    log.info("free VRAM before VLM load: %.1f GB", free_vram)

    if free_vram < 4.5:
        log.info("not enough free VRAM; attempting to unload other models first")
        try:
            from travelcull.ml import embed as _embed_mod
            if hasattr(_embed_mod, "_MODEL") and _embed_mod._MODEL is not None:
                del _embed_mod._MODEL
                del _embed_mod._PROC
                _embed_mod._MODEL = None
                _embed_mod._PROC = None
                gc.collect()
                torch.cuda.empty_cache()
                log.info("SigLIP unloaded; free VRAM now %.1f GB", torch.cuda.mem_get_info(0)[0] / 1024 ** 3)
        except Exception as e:
            log.warning("could not unload SigLIP: %s", e)
        # Also unload RAM++ if present
        try:
            from travelcull.ml import ram_tags as _ram_mod
            _ram_mod._unload_ram()
        except Exception as e:
            log.warning("could not unload RAM++: %s", e)

    vlm, vlm_proc = _load_vlm(vlm_model)

    # ── 6. Name lookback clusters (collision-suppressed) ────────────────── #
    lookback_reps: dict[int, list[Image.Image]] = {}
    for lbl in unique_lookback:
        cluster_mask = lookback_labels == lbl
        cluster_size = int(cluster_mask.sum())
        rep_indices = _pick_representatives(embs_n, cluster_mask, n=min(n_reps, cluster_size))
        images: list[Image.Image] = []
        for idx in rep_indices:
            ppath = preview_paths[idx]
            if ppath:
                abs_path = cfg.state_dir / ppath
                try:
                    img = Image.open(abs_path).convert("RGB")
                    img.thumbnail((336, 336), Image.LANCZOS)
                    images.append(img)
                except Exception as exc:
                    log.warning("could not open preview %s: %s", ppath, exc)
        if images:
            lookback_reps[lbl] = images

    log.info("naming %d lookback clusters (with collision suppression)", len(lookback_reps))
    lookback_names = _name_clusters_iteratively(lookback_reps, vlm, vlm_proc)

    # ── 7. Name posting clusters (collision-suppressed, separate set) ─── #
    posting_reps: dict[int, list[Image.Image]] = {}
    for lbl in unique_posting:
        cluster_mask = posting_labels_global == lbl
        cluster_size = int(cluster_mask.sum())
        rep_indices = _pick_representatives(embs_n, cluster_mask, n=min(n_reps, cluster_size))
        images = []
        for idx in rep_indices:
            ppath = preview_paths[idx]
            if ppath:
                abs_path = cfg.state_dir / ppath
                try:
                    img = Image.open(abs_path).convert("RGB")
                    img.thumbnail((336, 336), Image.LANCZOS)
                    images.append(img)
                except Exception as exc:
                    log.warning("could not open preview %s: %s", ppath, exc)
        if images:
            posting_reps[lbl] = images

    log.info("naming %d posting clusters (with collision suppression)", len(posting_reps))
    posting_names = _name_clusters_iteratively(posting_reps, vlm, vlm_proc)

    # ── 8. Unload VLM ──────────────────────────────────────────────────── #
    _unload_vlm()

    # ── 9. Write photo_tags ─────────────────────────────────────────────── #
    photo_ids_list = ids.tolist()
    with session_scope(Session) as s:
        # Delete old cluster tags (posting + lookback) — keep RAM tags
        for pid in photo_ids_list:
            s.query(PhotoTag).filter(
                PhotoTag.photo_id == pid,
                PhotoTag.source.in_(["posting", "lookback"]),
            ).delete(synchronize_session=False)
        s.flush()

        for i, pid in enumerate(photo_ids_list):
            # Lookback tag
            lb_lbl = int(lookback_labels[i])
            lb_name = lookback_names.get(lb_lbl, f"cluster {lb_lbl}")
            s.add(PhotoTag(photo_id=pid, tag=lb_name, score=1.0, source="lookback"))

            # Posting tag
            post_lbl = int(posting_labels_global[i])
            post_name = posting_names.get(post_lbl, f"group {post_lbl}")
            s.add(PhotoTag(photo_id=pid, tag=post_name, score=1.0, source="posting"))

        # Mark pipeline state as vl_done
        for pid in photo_ids_list:
            ps = s.get(PipelineState, pid)
            if ps:
                ps.vl_done = True
                s.add(ps)

    if on_progress:
        on_progress(2, 2, "done")

    log.info(
        "smart cluster stage done: %d photos → %d lookback clusters, %d posting clusters",
        len(photo_ids_list),
        len(unique_lookback),
        len(unique_posting),
    )
    return len(photo_ids_list)
