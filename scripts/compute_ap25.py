"""Compute LAION Aesthetic Predictor V2.5 scores for every photo in the index.

AP V2.5 (discus0434) is a small MLP on top of SigLIP-SO400M-patch14-384, the
same backbone we already use. We re-encode from the preview JPEG to get a
matching feature path — slightly redundant with what's in Embedding.siglip
but keeps the dependency direction clean and produces 1.0–10.0 scores.
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
log = logging.getLogger("ap25")

DB = Path("Z:/Ladakh/Photos/.travelcull/index.db")
STATE = Path("Z:/Ladakh/Photos/.travelcull")


def main() -> int:
    from aesthetic_predictor_v2_5 import convert_v2_5_from_siglip

    log.info("loading AP V2.5 (downloads on first run)…")
    model, preprocessor = convert_v2_5_from_siglip(
        low_cpu_mem_usage=True,
        trust_remote_code=True,
    )
    model = model.to(torch.bfloat16).cuda().eval()
    log.info("model loaded on cuda")

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
          AND (a.ap25_score IS NULL OR a.photo_id IS NULL)
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
            img = Image.open(img_path).convert("RGB")
            pixel_values = preprocessor(images=img, return_tensors="pt").pixel_values
            pixel_values = pixel_values.to(torch.bfloat16).cuda()
            with torch.inference_mode():
                score = model(pixel_values).logits.squeeze().float().cpu().item()
            conn.execute(
                "INSERT INTO aesthetic_scores (photo_id, ap25_score) VALUES (?, ?) "
                "ON CONFLICT(photo_id) DO UPDATE SET ap25_score = excluded.ap25_score",
                (pid, score),
            )
            if i % 25 == 0:
                conn.commit()
                rate = i / (time.time() - t0)
                eta = (total - i) / rate
                log.info("ap25 %d/%d (%.1f/s, eta %.0fs)", i, total, rate, eta)
        except Exception as exc:
            log.warning("photo %s failed: %s", pid, exc)

    conn.commit()
    conn.close()
    log.info("ap25 done in %.1fs", time.time() - t0)
    return 0


if __name__ == "__main__":
    sys.exit(main())
