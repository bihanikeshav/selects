"""SigLIP zero-shot category classification for landscape/portrait/object.

Uses the existing SigLIP text encoder (no second model needed). For each
photo we already have SigLIP image embeddings in the embeddings table — we
just compute cosine similarity to each category text prompt and persist the
three sims + an argmax label.
"""
from __future__ import annotations

import logging
import sqlite3
import sys
import time
from pathlib import Path

import numpy as np

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("categories")

DB = Path("Z:/Ladakh/Photos/.selects/index.db")

# Three category prompts.
PROMPTS = {
    "landscape": "a landscape photograph of mountains, valleys, lakes, or open scenery",
    "portrait": "a portrait photograph of one or more people",
    "object": "a close-up photograph of an object, food, or still life",
}

# Minimum similarity gate for the argmax to count.
# SigLIP image-text cosine sims sit in roughly [-0.05, 0.12] for this content
# so 0.02 corresponds to "meaningfully above random". Photos that fall below
# all three thresholds are tagged "unclassified".
PRIMARY_MIN_SIM = 0.02


def main() -> int:
    from selects.ml.embed import encode_text_prompts

    log.info("encoding %d category prompts via SigLIP text head", len(PROMPTS))
    prompt_keys = list(PROMPTS.keys())
    text_feats = encode_text_prompts(list(PROMPTS.values()))  # [K, 1152] cuda float32
    text_feats = (text_feats / text_feats.norm(dim=-1, keepdim=True)).cpu().numpy().astype(np.float32)

    conn = sqlite3.connect(DB)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS photo_categories (
            photo_id INTEGER PRIMARY KEY,
            landscape_sim REAL,
            portrait_sim REAL,
            object_sim REAL,
            primary_category TEXT,
            FOREIGN KEY (photo_id) REFERENCES photos(id) ON DELETE CASCADE
        )
        """
    )
    conn.commit()

    rows = conn.execute(
        """
        SELECT e.photo_id, e.siglip
        FROM embeddings e
        LEFT JOIN photo_categories c ON c.photo_id = e.photo_id
        WHERE c.photo_id IS NULL OR c.primary_category IS NULL
        """
    ).fetchall()
    total = len(rows)
    if total == 0:
        log.info("all photos already categorized — exiting")
        return 0
    log.info("categorizing %d photos", total)

    t0 = time.time()
    BATCH = 256
    for start in range(0, total, BATCH):
        chunk = rows[start:start + BATCH]
        embs = np.stack([
            np.frombuffer(r[1], dtype=np.float16).astype(np.float32)
            for r in chunk
        ])
        embs = embs / (np.linalg.norm(embs, axis=1, keepdims=True) + 1e-9)
        sims = embs @ text_feats.T  # [B, K]

        for (pid, _), row_sims in zip(chunk, sims):
            sims_by_key = {k: float(row_sims[i]) for i, k in enumerate(prompt_keys)}
            top_key = max(sims_by_key, key=sims_by_key.get)
            primary = top_key if sims_by_key[top_key] >= PRIMARY_MIN_SIM else "unclassified"
            conn.execute(
                """
                INSERT INTO photo_categories
                    (photo_id, landscape_sim, portrait_sim, object_sim, primary_category)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(photo_id) DO UPDATE SET
                    landscape_sim = excluded.landscape_sim,
                    portrait_sim = excluded.portrait_sim,
                    object_sim = excluded.object_sim,
                    primary_category = excluded.primary_category
                """,
                (
                    pid,
                    sims_by_key["landscape"],
                    sims_by_key["portrait"],
                    sims_by_key["object"],
                    primary,
                ),
            )
        conn.commit()
        log.info("categorized %d/%d", min(start + BATCH, total), total)

    conn.close()
    log.info("done in %.1fs", time.time() - t0)

    # Print distribution
    conn = sqlite3.connect(DB)
    dist = conn.execute(
        "SELECT primary_category, COUNT(*) FROM photo_categories GROUP BY primary_category"
    ).fetchall()
    log.info("category distribution:")
    for cat, n in dist:
        log.info("  %s: %d", cat, n)
    return 0


if __name__ == "__main__":
    sys.exit(main())
