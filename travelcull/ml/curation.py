"""Aesthetic-driven curation pipeline.

A single per-photo aesthetic = 0.6 * AP_V2.5 + 0.4 * NIMA, plus a library-wide
top-25% gate and burst-dedup via the existing Moment groups. Reused by both
Story rendering and Best-Of facet views — they only differ in what *scope*
they hand to `curate()`.

Configuration:
    AP_WEIGHT             : float in [0,1], default 0.6
    NIMA_WEIGHT           : float in [0,1], default 0.4
    AESTHETIC_PCT_FLOOR   : percentile threshold, default 75 (top 25%)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional

import numpy as np
from sqlalchemy.orm import Session as OrmSession

from travelcull.db.models import (
    AestheticScore,
    Moment,
    MomentMember,
    Photo,
)


AP_WEIGHT_DEFAULT = 0.6
NIMA_WEIGHT_DEFAULT = 0.4
AESTHETIC_PCT_FLOOR_DEFAULT = 75.0


@dataclass
class CuratedPhoto:
    photo_id: int
    sha256: str
    taken_at: Optional[str]
    combined: float
    ap25: float
    nima: float
    moment_id: Optional[int]
    moment_size: int = 1   # >1 means this photo is the surfaced member of a burst stack


def _combined(ap25: float, nima: float, *, ap_w: float, nima_w: float) -> float:
    return ap_w * ap25 + nima_w * nima


def compute_library_threshold(
    s: OrmSession,
    *,
    ap_w: float = AP_WEIGHT_DEFAULT,
    nima_w: float = NIMA_WEIGHT_DEFAULT,
    pct_floor: float = AESTHETIC_PCT_FLOOR_DEFAULT,
) -> Optional[float]:
    """Return the library-wide combined-aesthetic value at ``pct_floor``.

    Used by feature surfaces (BurstCull, ClusterDetail) that want to expose
    a single 0-100 filter slider whose semantics are library-relative, not
    scope-relative. Curation itself uses per-scope thresholds — see ``curate``.
    """
    rows = (
        s.query(AestheticScore.ap25_score, AestheticScore.nima_score)
        .filter(AestheticScore.ap25_score.isnot(None))
        .filter(AestheticScore.nima_score.isnot(None))
        .all()
    )
    if not rows:
        return None
    arr = np.array(
        [_combined(r[0], r[1], ap_w=ap_w, nima_w=nima_w) for r in rows],
        dtype=np.float64,
    )
    return float(np.percentile(arr, pct_floor))


def curate(
    s: OrmSession,
    photo_ids: Iterable[int],
    *,
    ap_w: float = AP_WEIGHT_DEFAULT,
    nima_w: float = NIMA_WEIGHT_DEFAULT,
    pct_floor: float = AESTHETIC_PCT_FLOOR_DEFAULT,
    library_pct_floor: Optional[float] = None,
    sort: str = "score",
    min_keep: int = 1,
) -> list[CuratedPhoto]:
    """Apply per-scope + library-wide curation to a set of photo IDs.

    Pipeline:
      1. Drop photos without both AP25 and NIMA.
      2. Library-wide floor (if ``library_pct_floor`` provided): drop
         anything below that library-wide percentile. A 'mediocre everywhere'
         photo doesn't get surfaced just because its scope happens to be thin.
      3. Per-scope gate: drop anything below the ``pct_floor`` percentile
         of the surviving scope (e.g. 75 keeps the top 25% of the scope).
      4. Burst-dedup: among survivors sharing a moment, keep only the
         highest combined. Photos with no moment pass through.
      5. Sort by combined desc (default) or by taken_at asc.

    ``min_keep`` guarantees at least N photos pass through if the scope has
    any AP+NIMA-scored photos at all.
    """
    ids = list(photo_ids)
    if not ids:
        return []

    rows = (
        s.query(
            Photo.id,
            Photo.sha256,
            Photo.taken_at,
            AestheticScore.ap25_score,
            AestheticScore.nima_score,
        )
        .join(AestheticScore, AestheticScore.photo_id == Photo.id)
        .filter(Photo.id.in_(ids))
        .filter(AestheticScore.ap25_score.isnot(None))
        .filter(AestheticScore.nima_score.isnot(None))
        .all()
    )
    if not rows:
        return []

    scope_combined = np.array(
        [_combined(r[3], r[4], ap_w=ap_w, nima_w=nima_w) for r in rows],
        dtype=np.float64,
    )

    # Library-wide absolute floor — applied BEFORE the per-scope gate so a
    # weak photo can't be promoted just because its scope is thin.
    library_floor_val: float = -float("inf")
    if library_pct_floor is not None:
        library_floor_val_opt = compute_library_threshold(
            s, ap_w=ap_w, nima_w=nima_w, pct_floor=library_pct_floor
        )
        if library_floor_val_opt is not None:
            library_floor_val = library_floor_val_opt

    if len(scope_combined) <= max(1, min_keep):
        # Tiny scope: skip the per-scope gate, keep everything that clears the
        # library floor.
        threshold = -float("inf")
    else:
        threshold = float(np.percentile(scope_combined, pct_floor))

    threshold = max(threshold, library_floor_val)

    candidates: list[CuratedPhoto] = []
    surviving_ids: list[int] = []
    for (pid, sha, taken, ap25, nima), combined in zip(rows, scope_combined):
        if combined < threshold:
            continue
        candidates.append(
            CuratedPhoto(
                photo_id=pid,
                sha256=sha,
                taken_at=taken.isoformat() if taken else None,
                combined=float(combined),
                ap25=ap25,
                nima=nima,
                moment_id=None,  # filled below
            )
        )
        surviving_ids.append(pid)

    # Guarantee at least min_keep photos pass through, even if the percentile
    # was so high it rejected everything (rounding edge case).
    if len(candidates) < min_keep and rows:
        idx_sorted = np.argsort(-scope_combined)
        candidates = []
        surviving_ids = []
        for idx in idx_sorted[: max(min_keep, 1)]:
            pid, sha, taken, ap25, nima = rows[idx]
            candidates.append(
                CuratedPhoto(
                    photo_id=pid,
                    sha256=sha,
                    taken_at=taken.isoformat() if taken else None,
                    combined=float(scope_combined[idx]),
                    ap25=ap25,
                    nima=nima,
                    moment_id=None,
                )
            )
            surviving_ids.append(pid)

    if not candidates:
        return []

    # Attach moment_id + moment_size (None if not in any moment)
    moment_rows = (
        s.query(MomentMember.photo_id, MomentMember.moment_id)
        .filter(MomentMember.photo_id.in_(surviving_ids))
        .all()
    )
    pid_to_moment = {pid: mid for pid, mid in moment_rows}

    moment_ids = list({mid for mid in pid_to_moment.values() if mid is not None})
    moment_meta = {
        m.id: m
        for m in (
            s.query(Moment).filter(Moment.id.in_(moment_ids)).all()
            if moment_ids else []
        )
    }
    moment_sizes = {mid: m.size for mid, m in moment_meta.items()}

    for c in candidates:
        c.moment_id = pid_to_moment.get(c.photo_id)
        if c.moment_id is not None:
            c.moment_size = moment_sizes.get(c.moment_id, 1)

    # Burst stack: keep only ONE member per moment (the user-chosen primary if
    # available, else the highest-combined). Other members stay in the DB and
    # are reached via the stack-cycle UI.
    by_moment: dict[int, CuratedPhoto] = {}
    stack_out: list[CuratedPhoto] = []
    for c in candidates:
        if c.moment_id is None:
            stack_out.append(c)
            continue
        existing = by_moment.get(c.moment_id)
        m = moment_meta.get(c.moment_id)
        primary_id = m.primary_photo_id if m else None
        if existing is None:
            by_moment[c.moment_id] = c
        elif c.photo_id == primary_id:
            # User has explicitly set this one as the top of stack — respect it
            by_moment[c.moment_id] = c
        elif existing.photo_id != primary_id and c.combined > existing.combined:
            by_moment[c.moment_id] = c
    stack_out.extend(by_moment.values())
    dedup_out = stack_out

    if sort == "chronological":
        dedup_out.sort(key=lambda c: c.taken_at or "")
    else:
        dedup_out.sort(key=lambda c: -c.combined)
    return dedup_out
