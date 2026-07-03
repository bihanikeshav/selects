"""RAM++ multi-label tagging pass.

Uses Recognize Anything Plus Model (RAM++) to generate per-photo object/concept tags.
Writes top-N tags into photo_tags with source='ram'.

Usage:
    travelcull index <folder> --pass ram_tag
"""
from __future__ import annotations

import gc
import logging
import sqlite3
from pathlib import Path
from typing import Callable

import torch
from PIL import Image

from travelcull.config import FolderConfig
from travelcull.db import init_db, session_scope
from travelcull.db.models import Photo, PhotoTag, PipelineState

log = logging.getLogger(__name__)

_RAM_MODEL = None
_RAM_TRANSFORM = None


# ──────────────────────────────────────────────────────────────────────────── #
# Schema migration                                                              #
# ──────────────────────────────────────────────────────────────────────────── #

def _migrate_add_source_column(db_path: Path) -> None:
    """Migrate photo_tags to support per-source tags.

    Two phases:
    1. If the 'source' column is missing, add it (ALTER TABLE).
    2. If the primary key is still (photo_id, tag) — i.e. doesn't include source —
       recreate the table with PK (photo_id, tag, source) so that the same tag
       name can exist under different sources (e.g. 'mountain' from RAM++ and
       'mountain' as a legacy SigLIP zero-shot tag).

    Fully idempotent — safe to call on every startup.
    """
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=OFF")

        # ── Phase 1: add source column if missing ─────────────────────────
        cursor = conn.execute("PRAGMA table_info(photo_tags)")
        col_info = {row[1]: row for row in cursor.fetchall()}
        if "source" not in col_info:
            log.info("migrating photo_tags: adding 'source' column")
            conn.execute("ALTER TABLE photo_tags ADD COLUMN source TEXT")
            conn.commit()
            log.info("source column added")

        # ── Phase 2: recreate table with new PK if needed ─────────────────
        # Check whether the current PK includes 'source' by inspecting the
        # CREATE TABLE statement (pk rank is stored in PRAGMA table_info col 5).
        cursor = conn.execute("PRAGMA table_info(photo_tags)")
        pk_cols = [row[1] for row in cursor.fetchall() if row[5] > 0]
        # pk_cols will be ['photo_id', 'tag'] on old tables, or
        # ['photo_id', 'tag', 'source'] after migration.

        if "source" not in pk_cols:
            log.info(
                "migrating photo_tags: recreating table with PK (photo_id, tag, source). "
                "Existing NULL-source rows will be preserved."
            )
            conn.executescript("""
                BEGIN;
                CREATE TABLE photo_tags_new (
                    photo_id INTEGER NOT NULL,
                    tag VARCHAR(128) NOT NULL,
                    score FLOAT NOT NULL,
                    source TEXT,
                    PRIMARY KEY (photo_id, tag, source),
                    FOREIGN KEY(photo_id) REFERENCES photos (id) ON DELETE CASCADE
                );
                INSERT OR IGNORE INTO photo_tags_new (photo_id, tag, score, source)
                    SELECT photo_id, tag, score, source FROM photo_tags;
                DROP TABLE photo_tags;
                ALTER TABLE photo_tags_new RENAME TO photo_tags;
                CREATE INDEX IF NOT EXISTS ix_photo_tags_tag ON photo_tags (tag);
                CREATE INDEX IF NOT EXISTS ix_photo_tags_source ON photo_tags (source);
                COMMIT;
            """)
            log.info("photo_tags table recreated with new PK")
        else:
            log.debug("photo_tags schema is up to date (PK includes source)")

        conn.execute("PRAGMA foreign_keys=ON")
    finally:
        conn.close()


# ──────────────────────────────────────────────────────────────────────────── #
# Model lifecycle                                                               #
# ──────────────────────────────────────────────────────────────────────────── #

def _load_ram(model_name: str = "xinyu1205/recognize-anything-plus-model") -> None:
    global _RAM_MODEL, _RAM_TRANSFORM

    if _RAM_MODEL is not None:
        return

    log.info("loading RAM++ from %s", model_name)

    try:
        from ram import get_transform
        from ram.models import ram_plus
    except ImportError as e:
        raise ImportError(
            "RAM++ not installed. Run: pip install git+https://github.com/xinyu1205/recognize-anything.git"
        ) from e

    device = "cuda" if torch.cuda.is_available() else "cpu"
    image_size = 384

    _RAM_TRANSFORM = get_transform(image_size=image_size)

    model = ram_plus(pretrained=model_name, image_size=image_size, vit="swin_l")
    model = model.to(device)
    if device == "cuda":
        model = model.half()  # fp16 to save VRAM (~2GB vs ~4GB fp32)
    model.eval()

    _RAM_MODEL = model

    if device == "cuda":
        vram_gb = torch.cuda.memory_allocated(0) / 1024 ** 3
        log.info("RAM++ loaded on %s (%.1f GB VRAM used)", device, vram_gb)
    else:
        log.info("RAM++ loaded on CPU")


def _unload_ram() -> None:
    global _RAM_MODEL, _RAM_TRANSFORM
    if _RAM_MODEL is not None:
        del _RAM_MODEL
        del _RAM_TRANSFORM
        _RAM_MODEL = None
        _RAM_TRANSFORM = None
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        log.info("RAM++ unloaded")


# ──────────────────────────────────────────────────────────────────────────── #
# Inference                                                                     #
# ──────────────────────────────────────────────────────────────────────────── #

def _infer_tags(image_path: str, top_n: int = 15) -> list[str]:
    """Run RAM++ on a single image file; return list of tag strings."""
    if _RAM_MODEL is None or _RAM_TRANSFORM is None:
        raise RuntimeError("RAM++ not loaded — call _load_ram() first")

    device = next(_RAM_MODEL.parameters()).device
    img = Image.open(image_path).convert("RGB")
    img_tensor = _RAM_TRANSFORM(img).unsqueeze(0).to(device)
    if device.type == "cuda":
        img_tensor = img_tensor.half()

    with torch.no_grad():
        tags, _ = _RAM_MODEL.generate_tag(img_tensor)

    # RAM++ returns pipe-separated tags like "mountain | snow | sky | ..."
    if isinstance(tags, (list, tuple)):
        raw = tags[0] if tags else ""
    else:
        raw = str(tags)

    parts = [t.strip().lower() for t in raw.split("|") if t.strip()]
    # Deduplicate while preserving order
    seen: set[str] = set()
    unique: list[str] = []
    for p in parts:
        if p not in seen:
            seen.add(p)
            unique.append(p)
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
    # 1. Migrate schema first (add source column if missing)
    _migrate_add_source_column(cfg.db_path)

    Session = init_db(cfg.db_path)

    # 2. Find photos that need RAM tagging
    with session_scope(Session) as s:
        if rerun:
            # Delete existing RAM tags so we re-run everything
            s.query(PhotoTag).filter(PhotoTag.source == "ram").delete()
            s.flush()

        # Photos with at least one embedding (means they're indexed)
        from travelcull.db.models import Embedding
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

    # 3. Free VRAM before loading RAM++
    if torch.cuda.is_available():
        free_vram = torch.cuda.mem_get_info(0)[0] / 1024 ** 3
        log.info("free VRAM before RAM++ load: %.1f GB", free_vram)

        if free_vram < 2.5:
            log.info("insufficient VRAM, unloading other models first")
            # Unload SigLIP if loaded
            try:
                from travelcull.ml import embed as _embed_mod
                if hasattr(_embed_mod, "_MODEL") and _embed_mod._MODEL is not None:
                    del _embed_mod._MODEL
                    del _embed_mod._PROC
                    _embed_mod._MODEL = None
                    _embed_mod._PROC = None
                    gc.collect()
                    torch.cuda.empty_cache()
            except Exception as e:
                log.warning("could not unload SigLIP: %s", e)
            # Unload VLM if loaded
            try:
                from travelcull.ml import smart_clusters as _sc_mod
                _sc_mod._unload_vlm()
            except Exception as e:
                log.warning("could not unload VLM: %s", e)

    # 4. Load RAM++
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
