"""Compute NIMA aesthetic scores for every photo in the index using pyiqa.

NIMA (Talebi & Milanfar 2018) is trained on AVA — it predicts a 1-10 mean
opinion score with a composition/aesthetic-oriented head. Different signal
shape than CLIP-IQA (technical) and AP V2.5 (LAION 'internet pretty').
"""
from __future__ import annotations

import logging
import sqlite3
import sys
import time
from pathlib import Path

import torch
from PIL import Image

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("nima")

DB = Path("Z:/Ladakh/Photos/.selects/index.db")
STATE = Path("Z:/Ladakh/Photos/.selects")


def main() -> int:
    import pyiqa

    log.info("loading NIMA model…")
    model = pyiqa.create_metric("nima", device="cuda")
    log.info("nima loaded; output range %s", model.score_range)

    conn = sqlite3.connect(DB)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS aesthetic_scores (
            photo_id INTEGER PRIMARY KEY,
            nima_score REAL,
            ap25_score REAL,
            personal_score REAL,
            FOREIGN KEY (photo_id) REFERENCES photos(id) ON DELETE CASCADE
        )
        """
    )
    conn.commit()

    rows = conn.execute(
        """
        SELECT p.id, p.preview_path
        FROM photos p
        LEFT JOIN aesthetic_scores a ON a.photo_id = p.id
        WHERE p.preview_path IS NOT NULL
          AND (a.nima_score IS NULL OR a.photo_id IS NULL)
        """
    ).fetchall()
    total = len(rows)
    if total == 0:
        log.info("nothing to score — exiting")
        return 0
    log.info("scoring %d photos", total)

    t0 = time.time()
    for i, (pid, preview_path) in enumerate(rows, 1):
        try:
            img_path = STATE / preview_path
            with torch.inference_mode():
                score = float(model(str(img_path)).item())
            conn.execute(
                "INSERT INTO aesthetic_scores (photo_id, nima_score) VALUES (?, ?) "
                "ON CONFLICT(photo_id) DO UPDATE SET nima_score = excluded.nima_score",
                (pid, score),
            )
            if i % 25 == 0:
                conn.commit()
                rate = i / (time.time() - t0)
                eta = (total - i) / rate
                log.info("nima %d/%d (%.1f/s, eta %.0fs)", i, total, rate, eta)
        except Exception as exc:
            log.warning("photo %s failed: %s", pid, exc)

    conn.commit()
    conn.close()
    log.info("nima done in %.1fs", time.time() - t0)
    return 0


if __name__ == "__main__":
    sys.exit(main())
