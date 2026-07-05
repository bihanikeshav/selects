"""Cluster photos into Moments: same place + same people + close in time + visually similar."""
from __future__ import annotations

import logging
import math
from collections import defaultdict
from typing import Callable

import numpy as np

from travelcull.config import FolderConfig
from travelcull.db import init_db, session_scope
from travelcull.db.models import (
    ClassicalScore,
    Embedding,
    FaceEmbedding,
    Moment,
    MomentMember,
    Photo,
)

log = logging.getLogger(__name__)

# A "burst" is a tightly-shot rapid sequence — same scene, seconds apart,
# nearly-identical framing. Earlier defaults (60s window, 0.90 sim) folded in
# unrelated nearby shots. These are the real burst-camera-roll defaults.
TIME_GAP_S = 12               # was 60 — bursts happen within seconds, not a minute
GPS_THRESH_DEG = 0.0002       # ~22 metres
VISUAL_SIM_THRESH = 0.96      # tightened from 0.94 — only near-duplicate framings count as a burst
FACE_SIM_THRESH = 0.55


def _haversine_deg(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Crude small-distance approximation in degrees — good enough at <200 m."""
    return math.sqrt((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2)


def _matches(a: dict, b: dict) -> bool:
    """Return True iff photos a and b belong to the same Moment."""
    # 1) Time guard
    dt = abs((a["taken_at"] - b["taken_at"]).total_seconds())
    if dt > TIME_GAP_S:
        return False

    # 2) GPS (only applied when both have coordinates)
    if (
        a["lat"] is not None and a["lon"] is not None
        and b["lat"] is not None and b["lon"] is not None
    ):
        if _haversine_deg((a["lat"], a["lon"]), (b["lat"], b["lon"])) > GPS_THRESH_DEG:
            return False

    # 3) Visual similarity (SigLIP cosine; embeddings are pre-normalised)
    sim = float(np.dot(a["emb"], b["emb"]))
    if sim < VISUAL_SIM_THRESH:
        return False

    # 4) Face identity
    if not a["faces"] and not b["faces"]:
        # No faces in either — same scene by visual+GPS+time is sufficient
        return True
    if not a["faces"] or not b["faces"]:
        # One has faces, the other doesn't — different moments
        return False
    # Both have faces: require at least one shared identity
    for fa in a["faces"]:
        fa_n = fa / max(float(np.linalg.norm(fa)), 1e-6)
        for fb in b["faces"]:
            fb_n = fb / max(float(np.linalg.norm(fb)), 1e-6)
            if float(np.dot(fa_n, fb_n)) >= FACE_SIM_THRESH:
                return True
    return False


def run_moment_stage(
    cfg: FolderConfig,
    on_progress: Callable[[int, int, str], None] | None = None,
) -> int:
    """Build Moment clusters from photos with embeddings. Returns count of moments created."""
    Session = init_db(cfg.db_path)

    # ------------------------------------------------------------------ #
    # 1. Load data                                                        #
    # ------------------------------------------------------------------ #
    with session_scope(Session) as s:
        rows = (
            s.query(
                Photo.id,
                Photo.taken_at,
                Photo.gps_lat,
                Photo.gps_lon,
                Embedding.siglip,
                ClassicalScore.faces_count,
                ClassicalScore.blur,
                Embedding.aesthetic_iqa,
            )
            .join(Embedding, Embedding.photo_id == Photo.id)
            .outerjoin(ClassicalScore, ClassicalScore.photo_id == Photo.id)
            .filter(Photo.taken_at.is_not(None))
            .order_by(Photo.taken_at)
            .all()
        )
        face_rows = s.query(FaceEmbedding.photo_id, FaceEmbedding.embedding).all()

    faces_by_photo: dict[int, list[np.ndarray]] = defaultdict(list)
    for pid, blob in face_rows:
        faces_by_photo[pid].append(
            np.frombuffer(blob, dtype=np.float16).astype(np.float32)
        )

    # Pre-normalise SigLIP embeddings
    photo_records: list[dict] = []
    for r in rows:
        emb = np.frombuffer(r.siglip, dtype=np.float16).astype(np.float32)
        n = np.linalg.norm(emb)
        if n > 0:
            emb = emb / n
        photo_records.append(
            {
                "id": r.id,
                "taken_at": r.taken_at,
                "lat": r.gps_lat,
                "lon": r.gps_lon,
                "emb": emb,
                "faces": faces_by_photo.get(r.id, []),
                "blur": r.blur or 0.0,
                "iqa": r.aesthetic_iqa or 0.0,
            }
        )

    n_photos = len(photo_records)
    if n_photos == 0:
        log.info("moment: no photos with embeddings, nothing to do")
        return 0

    log.info("moment: clustering %d photos", n_photos)

    # ------------------------------------------------------------------ #
    # 2. Sliding-window union-find                                        #
    # ------------------------------------------------------------------ #
    parent = list(range(n_photos))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]  # path compression
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    start = 0
    for i in range(n_photos):
        # Advance start pointer past photos outside the 60 s window
        while start < i and (
            photo_records[i]["taken_at"] - photo_records[start]["taken_at"]
        ).total_seconds() > TIME_GAP_S:
            start += 1

        for j in range(start, i):
            if _matches(photo_records[i], photo_records[j]):
                union(j, i)

        if on_progress and i % 100 == 0:
            on_progress(i, n_photos, "linking")

    # ------------------------------------------------------------------ #
    # 3. Build moment groups                                              #
    # ------------------------------------------------------------------ #
    moments_by_root: dict[int, list[int]] = defaultdict(list)
    for idx in range(n_photos):
        moments_by_root[find(idx)].append(idx)

    # ------------------------------------------------------------------ #
    # 4. Persist (wipe + rebuild)                                        #
    # ------------------------------------------------------------------ #
    with session_scope(Session) as s:
        s.query(MomentMember).delete()
        s.query(Moment).delete()
        s.flush()

        n_moments = 0
        for root, members in moments_by_root.items():
            if len(members) < 2:
                continue  # singletons are not meaningful Moments

            recs = [photo_records[m] for m in members]
            # Best photo first: highest iqa, then sharpest, then earliest
            recs.sort(
                key=lambda r: (
                    -(0.6 * r["iqa"] + 0.4 * min(r["blur"] / 1000.0, 1.0)),
                    r["taken_at"],
                )
            )
            primary = recs[0]
            mom = Moment(
                primary_photo_id=primary["id"],
                started_at=min(r["taken_at"] for r in recs),
                ended_at=max(r["taken_at"] for r in recs),
                size=len(recs),
            )
            s.add(mom)
            s.flush()  # obtain mom.id

            for rank, r in enumerate(recs):
                s.add(
                    MomentMember(moment_id=mom.id, photo_id=r["id"], rank=rank)
                )
            n_moments += 1

    log.info("moment: created %d moments", n_moments)
    return n_moments
