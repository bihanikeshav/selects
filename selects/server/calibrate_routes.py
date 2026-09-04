"""Aesthetic calibration: extremes, ratings, agreement, retrain, dashboard.

Live score is CLIP-IQA on Embedding.aesthetic_iqa. NIMA / AP-V2.5 may be
absent; they are shown when present but never required to list photos.
Ratings train a per-folder personal centroid on SigLIP embeddings.
"""
from __future__ import annotations

import logging

from fastapi import Body, FastAPI, HTTPException, Query

from selects.config import FolderConfig
from selects.db import init_db, session_scope
from selects.db.models import AestheticScore, Embedding, Photo, PhotoRating
from selects.util import utcnow

log = logging.getLogger(__name__)


def _combined_percentile_pairs(s) -> list[tuple]:
    """Return list of (photo_id, sha256, taken_at, iqa, nima, ap25, personal,
    combined_pct) for every photo with an IQA score, sorted by IQA
    percentile ascending.

    combined_pct is the IQA percentile rank in this library (0–100), used
    only to pick worst/best batches. Display ``combined`` uses AP-V2.5 +
    NIMA (or AP25, or IQA×10) via :func:`ensemble_score`.
    """
    rows = (
        s.query(
            Photo.id, Photo.sha256, Photo.taken_at,
            Embedding.aesthetic_iqa,
            AestheticScore.nima_score, AestheticScore.ap25_score,
            AestheticScore.personal_score,
        )
        .join(Embedding, Embedding.photo_id == Photo.id)
        .outerjoin(AestheticScore, AestheticScore.photo_id == Photo.id)
        .filter(Embedding.aesthetic_iqa.isnot(None))
        .all()
    )
    if not rows:
        return []
    n = len(rows)
    iqa_sorted = sorted(range(n), key=lambda i: rows[i][3])
    iqa_pct = [0.0] * n
    for rank, idx in enumerate(iqa_sorted):
        iqa_pct[idx] = (rank / max(1, n - 1)) * 100
    out = []
    for i, r in enumerate(rows):
        out.append((*r, iqa_pct[i]))
    out.sort(key=lambda t: t[7])  # ascending by IQA percentile
    return out


def register_calibrate_routes(app: FastAPI, cfg: FolderConfig) -> None:
    def Session():
        return init_db(cfg.db_path)()

    @app.get("/api/calibrate/extremes")
    def calibrate_extremes(bucket: str = Query("worst"), n: int = Query(30, ge=1, le=200)):
        """Return N unrated photos at the worst (bottom) or best (top) of the
        CLIP-IQA percentile ranking.

        bucket='worst' → bottom N, presented as candidates for rescue (default
        rating -1, user flips ones they like to +1).
        bucket='best'  → top N, presented as candidates for demotion (default
        rating +1, user flips ones they don't like to -1).
        """
        if bucket not in ("worst", "best"):
            raise HTTPException(400, detail="bucket must be 'worst' or 'best'")

        from selects.ml.aesthetic import ensemble_score

        with session_scope(Session) as s:
            rated_ids = {r[0] for r in s.query(PhotoRating.photo_id).all()}
            ranked = _combined_percentile_pairs(s)
        if not ranked:
            return {"photos": [], "total_indexed": 0, "rated_count": len(rated_ids)}

        unrated = [r for r in ranked if r[0] not in rated_ids]
        chosen = unrated[:n] if bucket == "worst" else list(reversed(unrated[-n:]))

        return {
            "photos": [
                {
                    "photo_id": r[0],
                    "sha256": r[1],
                    "taken_at": r[2].isoformat() if r[2] else None,
                    "thumb_url": f"/api/thumb/{r[1]}",
                    "preview_url": f"/api/preview/{r[1]}",
                    "scores": {
                        "iqa": r[3],
                        "nima": r[4],
                        "ap25": r[5],
                        "personal": r[6],
                        "combined": ensemble_score(r[5], r[4], r[3]),
                    },
                    "default_rating": -1 if bucket == "worst" else 1,
                }
                for r in chosen
            ],
            "bucket": bucket,
            "total_indexed": len(ranked),
            "rated_count": len(rated_ids),
        }

    @app.post("/api/calibrate/rate_batch")
    def calibrate_rate_batch(payload: dict = Body(...)):
        """Persist many ratings at once.
        Body: {ratings: [{photo_id: int, rating: -1|0|1}, ...]}.
        """
        ratings = payload.get("ratings") or []
        n_written = 0
        with session_scope(Session) as s:
            for item in ratings:
                pid = item.get("photo_id") if isinstance(item, dict) else None
                rating = item.get("rating") if isinstance(item, dict) else None
                if not isinstance(pid, int) or rating not in (-1, 0, 1):
                    continue
                existing = s.get(PhotoRating, pid)
                if existing:
                    existing.rating = rating
                    existing.rated_at = utcnow()
                else:
                    s.add(PhotoRating(photo_id=pid, rating=rating, rated_at=utcnow()))
                n_written += 1
        return {"ok": True, "n": n_written}

    @app.post("/api/calibrate/rate")
    def calibrate_rate(payload: dict = Body(...)):
        """Persist a single rating. Body: {photo_id: int, rating: -1|0|1}."""
        photo_id = payload.get("photo_id")
        rating = payload.get("rating")
        if not isinstance(photo_id, int):
            raise HTTPException(400, detail="photo_id must be an int")
        if rating not in (-1, 0, 1):
            raise HTTPException(400, detail="rating must be -1, 0, or 1")
        with session_scope(Session) as s:
            existing = s.get(PhotoRating, photo_id)
            if existing:
                existing.rating = rating
                existing.rated_at = utcnow()
            else:
                s.add(PhotoRating(photo_id=photo_id, rating=rating, rated_at=utcnow()))
        return {"ok": True}

    @app.get("/api/calibrate/agreement")
    def calibrate_agreement():
        """For each model, report the median percentile rank of the user's
        upvoted photos within the model's full-library ranking.

        Under the new semantics, only rating=+1 is a positive signal.
        A rating of -1 means "user reviewed and agreed with the ensemble's
        placement", carrying no training signal.

        Per-model output:
          - median_upvote_percentile: median percentile rank (0-100) of
            upvoted photos under this model. 50 = upvotes scattered evenly;
            90 = upvotes consistently near the top (good agreement).
          - n_scored_upvotes: how many upvoted photos this model has scored
        """
        import numpy as np

        with session_scope(Session) as s:
            upvoted_ids = {
                r[0]
                for r in s.query(PhotoRating.photo_id)
                .filter(PhotoRating.rating == 1).all()
            }
            rows = (
                s.query(
                    Photo.id,
                    Embedding.aesthetic_iqa,
                    AestheticScore.nima_score,
                    AestheticScore.ap25_score,
                    AestheticScore.personal_score,
                )
                .join(Embedding, Embedding.photo_id == Photo.id)
                .outerjoin(AestheticScore, AestheticScore.photo_id == Photo.id)
                .all()
            )

        if not upvoted_ids:
            return {"models": {}, "n_upvotes": 0, "message": "Upvote at least 1 photo."}

        photo_ids = np.array([r[0] for r in rows])
        model_cols = {"iqa": 1, "nima": 2, "ap25": 3, "personal": 4}
        out: dict[str, dict] = {}
        for name, col in model_cols.items():
            vals = np.array([
                r[col] if r[col] is not None else np.nan for r in rows
            ], dtype=float)
            mask = ~np.isnan(vals)
            if mask.sum() < 2:
                out[name] = {"median_upvote_percentile": None, "n_scored_upvotes": 0}
                continue
            v = vals[mask]
            ids_sub = photo_ids[mask]
            order = np.argsort(v)
            pct = np.empty_like(v)
            pct[order] = np.arange(len(v)) / max(1, len(v) - 1) * 100.0
            id_to_pct = dict(zip(ids_sub.tolist(), pct.tolist()))
            upvote_pcts = [id_to_pct[uid] for uid in upvoted_ids if uid in id_to_pct]
            out[name] = {
                "median_upvote_percentile": (
                    float(np.median(upvote_pcts)) if upvote_pcts else None
                ),
                "n_scored_upvotes": len(upvote_pcts),
            }

        from selects.ml.aesthetic import ensemble_score

        ens = np.array([
            v if v is not None else np.nan
            for v in (
                ensemble_score(r[model_cols["ap25"]], r[model_cols["nima"]], r[model_cols["iqa"]])
                for r in rows
            )
        ], dtype=float)
        m_combined = ~np.isnan(ens)
        if m_combined.sum() >= 2:
            ids_c = photo_ids[m_combined]
            v = ens[m_combined]
            order = np.argsort(v)
            c_pct = np.empty_like(v)
            c_pct[order] = np.arange(len(v)) / max(1, len(v) - 1) * 100.0
            id_to_c = dict(zip(ids_c.tolist(), c_pct.tolist()))
            c_upvote_pcts = [id_to_c[uid] for uid in upvoted_ids if uid in id_to_c]
            out["combined"] = {
                "median_upvote_percentile": (
                    float(np.median(c_upvote_pcts)) if c_upvote_pcts else None
                ),
                "n_scored_upvotes": len(c_upvote_pcts),
            }

        return {"models": out, "n_upvotes": len(upvoted_ids)}

    @app.post("/api/calibrate/retrain")
    def calibrate_retrain():
        """Train a personalized aesthetic signal from upvotes only.

        Method: unit-vector centroid of the SigLIP embeddings of all upvoted
        photos. personal_score for any photo = cosine similarity to that
        centroid. This is a one-class learner — it captures "what your
        upvotes look like" without treating non-upvotes as negative examples.

        Final story curation uses personal_score as a correction on top of
        NIMA+AP combined, not as a replacement.
        """
        import numpy as np

        with session_scope(Session) as s:
            upvoted = (
                s.query(PhotoRating.photo_id, Embedding.siglip)
                .join(Embedding, Embedding.photo_id == PhotoRating.photo_id)
                .filter(PhotoRating.rating == 1)
                .all()
            )
            if len(upvoted) < 3:
                raise HTTPException(
                    400,
                    detail=f"need at least 3 upvotes; have {len(upvoted)}.",
                )

            X_pos = np.stack([
                np.frombuffer(r[1], dtype=np.float16).astype(np.float32)
                for r in upvoted
            ])
            X_pos = X_pos / (np.linalg.norm(X_pos, axis=1, keepdims=True) + 1e-9)
            centroid = X_pos.mean(axis=0)
            centroid = centroid / (np.linalg.norm(centroid) + 1e-9)

            all_rows = s.query(Embedding.photo_id, Embedding.siglip).all()
            X_all = np.stack([
                np.frombuffer(r[1], dtype=np.float16).astype(np.float32)
                for r in all_rows
            ])
            X_all = X_all / (np.linalg.norm(X_all, axis=1, keepdims=True) + 1e-9)
            sims = X_all @ centroid  # [N], range [-1, 1]

            for (pid, _), sim in zip(all_rows, sims):
                existing = s.get(AestheticScore, pid)
                if existing:
                    existing.personal_score = float(sim)
                else:
                    s.add(AestheticScore(photo_id=pid, personal_score=float(sim)))

            mean_pos_sim = float((X_pos @ centroid).mean())

        return {
            "ok": True,
            "method": "centroid_cosine",
            "n_positive": len(upvoted),
            "n_scored": len(all_rows),
            "mean_positive_similarity": mean_pos_sim,
        }

    @app.get("/api/calibrate/dashboard")
    def calibrate_dashboard():
        """Return all photos with all 4 scores + user rating (if any) for the dashboard grid."""
        with session_scope(Session) as s:
            rows = (
                s.query(
                    Photo.id, Photo.sha256, Photo.taken_at,
                    Embedding.aesthetic_iqa,
                    AestheticScore.nima_score, AestheticScore.ap25_score,
                    AestheticScore.personal_score,
                    PhotoRating.rating,
                )
                .join(Embedding, Embedding.photo_id == Photo.id)
                .outerjoin(AestheticScore, AestheticScore.photo_id == Photo.id)
                .outerjoin(PhotoRating, PhotoRating.photo_id == Photo.id)
                .all()
            )
        return {
            "photos": [
                {
                    "photo_id": r[0],
                    "sha256": r[1],
                    "taken_at": r[2].isoformat() if r[2] else None,
                    "thumb_url": f"/api/thumb/{r[1]}",
                    "preview_url": f"/api/preview/{r[1]}",
                    "scores": {
                        "iqa": r[3],
                        "nima": r[4],
                        "ap25": r[5],
                        "personal": r[6],
                    },
                    "rating": r[7],
                }
                for r in rows
            ]
        }
