"""RAM++ multi-label tagging pass.

Uses Recognize Anything Plus Model (RAM++) to generate per-photo object/concept tags.
Writes top-N tags into photo_tags with source='ram'.

Usage:
    selects index <folder> --pass ram_tag
"""
from __future__ import annotations

import json
import logging
from typing import Callable

import numpy as np
from PIL import Image

from selects.config import FolderConfig
from selects.db import init_db, session_scope
from selects.db.models import Photo, PhotoTag, PipelineState
from selects.ml.onnx_rt import model_session, repo_file

log = logging.getLogger(__name__)

# RAM get_transform(384): Resize((384,384)) BILINEAR -> ToTensor (/255, CHW) ->
# Normalize(ImageNet). Resize runs on the PIL image, so a plain PIL resize matches.
_IMG_SIZE = 384
_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
_STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)

# Post-processing metadata (index-aligned to the 4585 logit classes).
_TAG_NAMES: list[str] | None = None
_THRESHOLD: np.ndarray | None = None
_DELETE: np.ndarray | None = None


# ──────────────────────────────────────────────────────────────────────────── #
# Model lifecycle                                                               #
# ──────────────────────────────────────────────────────────────────────────── #

def _load_ram(model_name: str = "ram_plus") -> None:
    """Warm the ORT session + tag metadata. ``model_name`` kept for call-site
    compatibility; the ONNX weights come from the shared HF repo."""
    global _TAG_NAMES, _THRESHOLD, _DELETE
    model_session("ram_plus")  # download + build (cached)
    if _TAG_NAMES is None:
        _TAG_NAMES = json.load(open(repo_file("ram_tags.json"), encoding="utf-8"))
        meta = np.load(repo_file("ram_meta.npz"))
        _THRESHOLD = meta["threshold"].astype(np.float32)
        _DELETE = meta["delete"].astype(np.int64)
    log.info("RAM++ ONNX ready (%d tags)", len(_TAG_NAMES))


def _unload_ram() -> None:
    # ORT sessions are cached in onnx_rt and shared; nothing per-stage to free.
    pass


# ──────────────────────────────────────────────────────────────────────────── #
# Inference                                                                     #
# ──────────────────────────────────────────────────────────────────────────── #

def _preprocess(image_path: str) -> np.ndarray:
    """PIL image file -> [1,3,384,384] float32, matching RAM get_transform."""
    img = Image.open(image_path).convert("RGB").resize(
        (_IMG_SIZE, _IMG_SIZE), Image.BILINEAR
    )
    arr = np.asarray(img, dtype=np.float32) / 255.0     # [384,384,3]
    arr = (arr - _MEAN) / _STD                          # ImageNet normalize
    return np.ascontiguousarray(arr.transpose(2, 0, 1)[None])  # [1,3,384,384]


def _infer_tags(image_path: str, top_n: int = 15) -> list[str]:
    """Run RAM++ on a single image file; return list of tag strings.

    Mirrors stock generate_tag: sigmoid(logits) > per-class threshold, drop the
    delete-list classes, map surviving indices to names (in index order, as the
    stock pipe-joined output is), lower-cased and truncated to *top_n*.
    """
    if _TAG_NAMES is None:
        _load_ram()
    sess = model_session("ram_plus")
    x = _preprocess(image_path)
    logits = sess.run(None, {"image": x})[0][0]              # [4585]
    probs = 1.0 / (1.0 + np.exp(-logits.astype(np.float64)))
    tag = probs > _THRESHOLD
    if _DELETE.size:
        tag[_DELETE] = False
    idx = np.where(tag)[0]
    names = [_TAG_NAMES[i].strip().lower() for i in idx]
    # dedupe preserving order (some names can repeat after lower-casing)
    seen: set[str] = set()
    unique = [n for n in names if not (n in seen or seen.add(n))]
    return unique[:top_n]


# ──────────────────────────────────────────────────────────────────────────── #
# Public entry point                                                            #
# ──────────────────────────────────────────────────────────────────────────── #

def run_ram_tagging_stage(
    cfg: FolderConfig,
    on_progress: Callable[[int, int, str], None] | None = None,
    top_n: int = 15,
    ram_model: str = "xinyu1205/recognize-anything-plus-model",
    rerun: bool = False,
) -> int:
    """Run RAM++ on all photos without RAM tags; write results to photo_tags.

    Returns number of photos processed.
    """
    # Schema (including the source column + PK on photo_tags) is guaranteed by
    # init_db() via Alembic migrations; no ad-hoc migration needed here.
    Session = init_db(cfg.db_path)

    # 2. Find photos that need RAM tagging
    with session_scope(Session) as s:
        if rerun:
            # Delete existing RAM tags so we re-run everything
            s.query(PhotoTag).filter(PhotoTag.source == "ram").delete()
            s.flush()

        # Photos with at least one embedding (means they're indexed)
        from selects.db.models import Embedding
        indexed_ids: set[int] = {
            r[0] for r in s.query(Embedding.photo_id).all()
        }

        # Photos that already have RAM tags
        already_done: set[int] = {
            r[0] for r in s.query(PhotoTag.photo_id).filter(PhotoTag.source == "ram").all()
        }

        todo_ids = sorted(indexed_ids - already_done)

        # Load photo path + preview_path for todo photos
        photo_rows = (
            s.query(Photo.id, Photo.path, Photo.preview_path)
            .filter(Photo.id.in_(todo_ids))
            .all()
        )

    if not photo_rows:
        log.info("all photos already have RAM tags; nothing to do")
        return 0

    log.info("RAM++ tagging: %d photos to process", len(photo_rows))

    # 4. Warm the RAM++ ONNX session (ORT manages its own device memory; the old
    #    torch VRAM-juggling that unloaded SigLIP/VLM here is no longer needed).
    _load_ram(ram_model)

    # 5. Infer tags per photo
    processed = 0
    batch_size = 50  # Write to DB in batches to avoid holding huge transactions

    photo_rows_list = list(photo_rows)
    total = len(photo_rows_list)

    for batch_start in range(0, total, batch_size):
        batch = photo_rows_list[batch_start:batch_start + batch_size]
        tag_rows: list[tuple[int, str]] = []  # (photo_id, tag)

        for photo_id, photo_path, preview_path in batch:
            # Prefer preview (smaller) for inference; fall back to original
            if preview_path:
                abs_path = str(cfg.state_dir / preview_path)
            else:
                abs_path = photo_path

            try:
                tags = _infer_tags(abs_path, top_n=top_n)
            except Exception as exc:
                log.warning("RAM++ failed on photo %d (%s): %s", photo_id, abs_path, exc)
                tags = []

            for tag in tags:
                tag_rows.append((photo_id, tag))

            processed += 1
            if on_progress:
                on_progress(
                    batch_start + (batch.index((photo_id, photo_path, preview_path)) + 1),
                    total,
                    f"ram: {photo_id}",
                )

        # Write batch to DB
        with session_scope(Session) as s:
            for photo_id, tag in tag_rows:
                # Use merge/upsert pattern: delete first then insert
                existing = (
                    s.query(PhotoTag)
                    .filter(
                        PhotoTag.photo_id == photo_id,
                        PhotoTag.tag == tag,
                        PhotoTag.source == "ram",
                    )
                    .first()
                )
                if existing is None:
                    s.add(PhotoTag(photo_id=photo_id, tag=tag, score=1.0, source="ram"))

        log.info(
            "RAM++ batch %d-%d done (%d/%d photos)",
            batch_start + 1,
            min(batch_start + batch_size, total),
            min(batch_start + batch_size, total),
            total,
        )

    # 6. Unload RAM++ to free VRAM for next stage
    _unload_ram()

    log.info("RAM++ tagging complete: %d photos, ~%d tags/photo", processed, top_n)
    return processed
