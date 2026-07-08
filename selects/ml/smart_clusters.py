"""HDBSCAN + VLM cluster naming pass — M2 v3.

Dual-resolution clustering with temporal/GPS pre-segmentation and
SigLIP zero-shot cluster naming (no VLM).

Two-stage approach:
1. Session-block pre-segmentation by time gaps (>90 min) or GPS jumps (>500m).
2. "Posting" pass: HDBSCAN(min_cluster_size=5) within each session block
   → tight visual groups for carousels.
3. "Lookback" pass: HDBSCAN(min_cluster_size=20) globally
   → broad themes for browsing the trip.
4. SigLIP zero-shot matches each cluster to a curated scene vocabulary.

Usage (CLI): selects index <folder> --pass smart_tag
"""
from __future__ import annotations

import logging
import math
from typing import Callable

import numpy as np

from selects.config import FolderConfig
from selects.db import init_db, session_scope
from selects.db.models import Embedding, Photo, PhotoTag, PipelineState

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


# ───────────────────────────────────────────────────────────────────────────────── #
# Zero-shot cluster naming (SigLIP text↔image — no VLM)                          #
# ───────────────────────────────────────────────────────────────────────────────── #

# Curated travel scene / subject vocabulary. Each cluster centroid is matched
# against these with SigLIP — the same embedding space we already compute for
# every photo — so naming needs no generative model and is deterministic.
_VOCAB_TEMPLATE = "a travel photo of {}"

TRAVEL_VOCAB: list[str] = [
    "beach", "tropical beach", "rocky coastline", "harbor", "seaside promenade",
    "old town streets", "narrow alley", "city skyline", "modern architecture",
    "historic building", "cathedral", "church interior", "temple", "mosque",
    "monastery", "castle", "palace", "ancient ruins", "monument", "statue",
    "museum", "art gallery", "library", "market", "street food stall",
    "food market", "restaurant meal", "cafe", "coffee shop", "bakery", "bar",
    "nightlife", "rooftop bar", "mountain landscape", "hiking trail", "forest",
    "jungle", "waterfall", "river", "lake", "reflection lake", "desert",
    "sand dunes", "canyon", "glacier", "volcano", "hot spring", "cave",
    "snow landscape", "ski slope", "countryside", "farmland", "vineyard",
    "tea plantation", "rice terraces", "garden", "botanical garden", "city park",
    "flower field", "wildlife", "birds", "safari", "marine life", "coral reef",
    "aquarium", "zoo", "sunset", "sunrise", "golden hour", "night lights",
    "city at night", "starry sky", "fireworks", "festival", "parade", "concert",
    "live music", "sports event", "stadium", "amusement park", "ferris wheel",
    "train station", "railway", "airport", "airplane window", "road trip",
    "highway", "boat", "sailboat", "cruise ship", "kayaking", "snorkeling",
    "diving", "surfing", "swimming pool", "beach resort", "hotel room", "spa",
    "shopping street", "bookstore", "bridge", "fountain", "plaza", "lighthouse",
    "windmill", "waterfront", "portrait", "group photo", "selfie",
    "kids playing", "street performer", "local people", "traditional costume",
    "pets", "dogs", "cats", "dessert", "breakfast", "seafood", "pizza", "pasta",
    "sushi", "noodles", "street snacks", "cocktails", "wine", "latte art",
    "autumn foliage", "cherry blossom", "christmas market", "rainy day",
    "foggy morning", "misty mountains",
]


def _cluster_centroids(
    labels_arr: np.ndarray, embs_n: np.ndarray, unique: list[int]
) -> tuple[dict[int, np.ndarray], dict[int, int]]:
    """Per-cluster L2-normalized mean embedding + member count."""
    centroids: dict[int, np.ndarray] = {}
    sizes: dict[int, int] = {}
    for lbl in unique:
        mask = labels_arr == lbl
        c = embs_n[mask].mean(axis=0)
        centroids[int(lbl)] = c / max(float(np.linalg.norm(c)), 1e-6)
        sizes[int(lbl)] = int(mask.sum())
    return centroids, sizes


def _name_clusters_zeroshot(
    centroids: dict[int, np.ndarray],
    sizes: dict[int, int],
    vocab_embs: np.ndarray,
    labels: list[str],
) -> dict[int, str]:
    """Give each cluster its best-matching vocabulary label, largest cluster
    first, skipping labels already taken so distinct clusters get distinct names.
    Deterministic — replaces the VLM's collision-suppression prompting."""
    names: dict[int, str] = {}
    used: set[str] = set()
    for cid in sorted(centroids, key=lambda c: -sizes.get(c, 0)):
        sims = vocab_embs @ centroids[cid]           # cosine — both normalized
        chosen: str | None = None
        for j in np.argsort(-sims):
            cand = labels[int(j)]
            if cand not in used:
                chosen = cand
                break
        if chosen is None:  # more clusters than vocab labels — disambiguate
            chosen = f"{labels[int(np.argmax(sims))]} {cid}"
        used.add(chosen)
        names[cid] = chosen
    return names


# ──────────────────────────────────────────────────────────────────────────── #
# Public entry point                                                            #
# ──────────────────────────────────────────────────────────────────────────── #

def run_smart_cluster_stage(
    cfg: FolderConfig,
    on_progress: Callable[[int, int, str], None] | None = None,
) -> int:
    """Dual-resolution HDBSCAN clustering + SigLIP zero-shot naming → populate photo_tags.

    Produces two sets of cluster tags per photo:
    - source='posting': tight session-block clusters (min_cluster_size=5)
    - source='lookback': broad global clusters (min_cluster_size=20)

    Returns the number of photos tagged.
    """
    # Schema (photo_tags.source column + PK) is guaranteed by init_db() via
    # Alembic migrations; no ad-hoc migration needed here.
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

    # ── 5. Zero-shot cluster naming (SigLIP text↔image, no VLM) ─────────── #
    from selects.ml import embed as _embed

    prompts = [_VOCAB_TEMPLATE.format(lbl) for lbl in TRAVEL_VOCAB]
    vocab_embs = _embed.encode_text_prompts(prompts).astype(np.float32)

    lb_centroids, lb_sizes = _cluster_centroids(lookback_labels, embs_n, unique_lookback)
    lookback_names = _name_clusters_zeroshot(lb_centroids, lb_sizes, vocab_embs, TRAVEL_VOCAB)
    log.info("named %d lookback clusters (zero-shot)", len(lookback_names))

    post_centroids, post_sizes = _cluster_centroids(posting_labels_global, embs_n, unique_posting)
    posting_names = _name_clusters_zeroshot(post_centroids, post_sizes, vocab_embs, TRAVEL_VOCAB)
    log.info("named %d posting clusters (zero-shot)", len(posting_names))

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
