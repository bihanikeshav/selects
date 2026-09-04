"""Aesthetic-driven curation pipeline.

A single per-photo aesthetic = CLIP-IQA on ``Embedding.aesthetic_iqa`` in
[0, 1], plus a library-wide percentile gate and burst-dedup via the existing
Moment groups. Reused by both Story rendering and Best-Of facet views — they
only differ in what *scope* they hand to `curate()`.

Missing IQA is a non-gate: an unscored scope is returned (burst-dedup still
applies). AP25/NIMA are not aliased to IQA.

Configuration:
    AESTHETIC_PCT_FLOOR   : percentile threshold, default 75 (top 25%)
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

import numpy as np
from sqlalchemy.orm import Session as OrmSession

from selects.db.models import (
    AestheticScore,
    Embedding,
    Moment,
    MomentMember,
    Photo,
)
from selects.ml.aesthetic import rank_score


AP_WEIGHT_DEFAULT = 0.6
NIMA_WEIGHT_DEFAULT = 0.4
AESTHETIC_PCT_FLOOR_DEFAULT = 75.0


@dataclass
class CuratedPhoto:
    photo_id: int
    sha256: str
    taken_at: Optional[str]
    combined: Optional[float]  # rank_score in [0, 1] (AP25/10 or IQA); None when unscored
    ap25: Optional[float]
    nima: Optional[float]
    moment_id: Optional[int]
    moment_size: int = 1   # >1 means this photo is the surfaced member of a burst stack
    taste: Optional[float] = None   # personalized taste score in [0,1], if a taste model exists
    final: Optional[float] = None   # blended ranking score in [0,1]: (1-w)*aesthetic + w*taste
    iqa: Optional[float] = None     # same 0–1 CLIP-IQA as combined; None when missing


def _make_curated(
    pid: int,
    sha: str,
    taken,
    iqa: Optional[float],
    ap25: Optional[float] = None,
    nima: Optional[float] = None,
    *,
    ap_w: float = AP_WEIGHT_DEFAULT,
    nima_w: float = NIMA_WEIGHT_DEFAULT,
) -> CuratedPhoto:
    iqa_f = float(iqa) if iqa is not None else None
    ap_f = float(ap25) if ap25 is not None else None
    nima_f = float(nima) if nima is not None else None
    return CuratedPhoto(
        photo_id=pid,
        sha256=sha,
        taken_at=taken.isoformat() if taken else None,
        combined=rank_score(ap_f, nima_f, iqa_f, ap_w=ap_w, nima_w=nima_w),
        ap25=ap_f,
        nima=nima_f,
        moment_id=None,
        iqa=iqa_f,
    )


def compute_library_threshold(
    s: OrmSession,
    *,
    ap_w: float = AP_WEIGHT_DEFAULT,
    nima_w: float = NIMA_WEIGHT_DEFAULT,
    pct_floor: float = AESTHETIC_PCT_FLOOR_DEFAULT,
) -> Optional[float]:
    """Return the library-wide CLIP-IQA value at ``pct_floor``.

    Used by quality-bucket filters (underexposed/blurry_keepers) whose SQL
    compares against ``Embedding.aesthetic_iqa``. Story/Best-Of curation uses
    :func:`compute_rank_threshold` so AP-V2.5 participates in gating.
    """
    del ap_w, nima_w
    rows = (
        s.query(Embedding.aesthetic_iqa)
        .filter(Embedding.aesthetic_iqa.isnot(None))
        .all()
    )
    if not rows:
        return None
    arr = np.array([r[0] for r in rows], dtype=np.float64)
    return float(np.percentile(arr, pct_floor))


# Library-wide thresholds are scanned over every scored photo, and a single
# /api/stories request used to recompute one per story. Cache them per
# (database, database state, parameters); any write to the library changes the
# stamp and drops the entry.
_THRESHOLD_CACHE: dict[tuple, Optional[float]] = {}


def clear_threshold_cache() -> None:
    """Drop every memoized library threshold (tests, and library switches)."""
    _THRESHOLD_CACHE.clear()


def _db_stamp(s: OrmSession) -> Optional[tuple]:
    """Return a cache key component that changes whenever the library does.

    ``None`` for a session with no file behind it (in-memory SQLite), which
    disables caching rather than risking a stale answer. SQLite runs in WAL
    mode, so a committed write lands in the ``-wal`` sidecar and may leave the
    main file's mtime untouched until a checkpoint — the sidecar's mtime and
    size are part of the stamp for that reason.
    """
    try:
        bind = s.get_bind()
        db_file = getattr(getattr(bind, "url", None), "database", None)
        if not db_file or db_file == ":memory:":
            return None
        db_path = Path(db_file)
        db_stat = db_path.stat()
        wal = db_path.with_name(db_path.name + "-wal")
        try:
            wal_stat = wal.stat()
            wal_part = (wal_stat.st_mtime_ns, wal_stat.st_size)
        except OSError:
            wal_part = (0, 0)
        return (str(db_path), db_stat.st_mtime_ns, db_stat.st_size, *wal_part)
    except Exception:
        return None


def compute_rank_threshold(
    s: OrmSession,
    *,
    ap_w: float = AP_WEIGHT_DEFAULT,
    nima_w: float = NIMA_WEIGHT_DEFAULT,
    pct_floor: float = AESTHETIC_PCT_FLOOR_DEFAULT,
) -> Optional[float]:
    """Library-wide percentile of :func:`rank_score` (AP25/10, else IQA).

    Memoized per (database state, ap_w, nima_w, pct_floor).
    """
    stamp = _db_stamp(s)
    key = (stamp, ap_w, nima_w, pct_floor) if stamp is not None else None
    if key is not None and key in _THRESHOLD_CACHE:
        return _THRESHOLD_CACHE[key]

    value = _compute_rank_threshold_uncached(s, ap_w=ap_w, nima_w=nima_w, pct_floor=pct_floor)
    if key is not None:
        # Only the current database state is worth keeping; an older stamp can
        # never be asked for again.
        for stale in [k for k in _THRESHOLD_CACHE if k[0] != stamp]:
            del _THRESHOLD_CACHE[stale]
        _THRESHOLD_CACHE[key] = value
    return value


def _compute_rank_threshold_uncached(
    s: OrmSession,
    *,
    ap_w: float,
    nima_w: float,
    pct_floor: float,
) -> Optional[float]:
    rows = (
        s.query(
            Embedding.aesthetic_iqa,
            AestheticScore.ap25_score,
            AestheticScore.nima_score,
        )
        .outerjoin(AestheticScore, AestheticScore.photo_id == Embedding.photo_id)
        .all()
    )
    scores = [
        rank_score(ap25, nima, iqa, ap_w=ap_w, nima_w=nima_w)
        for iqa, ap25, nima in rows
    ]
    scores = [v for v in scores if v is not None]
    if not scores:
        return None
    return float(np.percentile(np.array(scores, dtype=np.float64), pct_floor))


def curate(
    s: OrmSession,
    photo_ids: Iterable[int],
    *,
    ap_w: float = AP_WEIGHT_DEFAULT,
    nima_w: float = NIMA_WEIGHT_DEFAULT,
    pct_floor: float = AESTHETIC_PCT_FLOOR_DEFAULT,
    library_pct_floor: Optional[float] = None,
    library_threshold: Optional[float] = None,
    sort: str = "score",
    min_keep: int = 1,
) -> list[CuratedPhoto]:
    """Apply per-scope + library-wide aesthetic curation to a set of photo IDs.

    Ranking score is AP-V2.5 (scaled 0–1) when present, else CLIP-IQA.
    Missing scores are a non-gate: an unscored scope is returned (burst-dedup
    still applies).

    *library_threshold* is the already-resolved value of the library gate that
    *library_pct_floor* asks for; pass it when curating many scopes in one
    request so the library-wide scan happens once.
    """
    ids = list(photo_ids)
    if not ids:
        return []

    rows = (
        s.query(
            Photo.id,
            Photo.sha256,
            Photo.taken_at,
            Embedding.aesthetic_iqa,
            AestheticScore.ap25_score,
            AestheticScore.nima_score,
        )
        .outerjoin(Embedding, Embedding.photo_id == Photo.id)
        .outerjoin(AestheticScore, AestheticScore.photo_id == Photo.id)
        .filter(Photo.id.in_(ids))
        .all()
    )
    if not rows:
        return []

    def _curated(row) -> CuratedPhoto:
        return _make_curated(
            row[0], row[1], row[2], row[3], row[4], row[5],
            ap_w=ap_w, nima_w=nima_w,
        )

    scored = [(row, c) for row in rows if (c := _curated(row)).combined is not None]
    if not scored:
        return _dedup_and_rank(s, [_curated(r) for r in rows], sort)

    scored_rows = [row for row, _ in scored]
    scope_combined = np.array([c.combined for _, c in scored], dtype=np.float64)

    library_floor_val: float = -float("inf")
    if library_pct_floor is not None:
        library_floor_val_opt = library_threshold
        if library_floor_val_opt is None:
            library_floor_val_opt = compute_rank_threshold(
                s, ap_w=ap_w, nima_w=nima_w, pct_floor=library_pct_floor
            )
        if library_floor_val_opt is not None:
            library_floor_val = library_floor_val_opt

    tiny = len(ids) <= min_keep or len(scored_rows) <= max(1, min_keep)
    if tiny:
        threshold = library_floor_val
    else:
        threshold = float(np.percentile(scope_combined, pct_floor))
        threshold = max(threshold, library_floor_val)

    candidates: list[CuratedPhoto] = []
    for row, curated in scored:
        if curated.combined is not None and curated.combined < threshold:
            continue
        candidates.append(curated)

    if len(candidates) < min_keep and scored:
        idx_sorted = np.argsort(-scope_combined)
        candidates = [scored[int(idx)][1] for idx in idx_sorted[: max(min_keep, 1)]]

    return _dedup_and_rank(s, candidates, sort)


def _dedup_and_rank(
    s: OrmSession, candidates: list[CuratedPhoto], sort: str
) -> list[CuratedPhoto]:
    if not candidates:
        return []

    surviving_ids = [c.photo_id for c in candidates]

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
    #
    # Face-quality blend: within a stack the aesthetic score is adjusted by a
    # BOUNDED penalty (closed eyes, contextual on group size / frontality —
    # see face_quality_penalty). The cap guarantees this only flips
    # near-equal candidates and never overrides a big aesthetic gap. It does
    # not affect gating, output scores or sort order.
    from selects.ml.face_attributes import stack_face_penalties

    in_moment_ids = [c.photo_id for c in candidates if c.moment_id is not None]
    face_penalty = stack_face_penalties(s, in_moment_ids) if in_moment_ids else {}

    def _stack_score(c: CuratedPhoto) -> float:
        base = c.combined if c.combined is not None else 0.0
        return base - face_penalty.get(c.photo_id, 0.0)

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
        elif existing.photo_id != primary_id and _stack_score(c) > _stack_score(existing):
            by_moment[c.moment_id] = c
    stack_out.extend(by_moment.values())
    dedup_out = stack_out

    # Taste personalization: if a trained taste model exists next to this
    # library's DB, blend it into the ranking as
    #   final = (1-w)*aesthetic + w*taste
    # where aesthetic is the percentile-rank of `combined` within the surfaced
    # set (so both terms live in [0,1]) and w ramps 0 → 0.4 with the number of
    # swipe decisions the model was trained on. Gating and stack selection
    # above are untouched — taste only reorders what already survived.
    _apply_taste_blend(s, dedup_out)

    if sort == "chronological":
        dedup_out.sort(key=lambda c: c.taken_at or "")
    else:
        def _rank_key(c: CuratedPhoto) -> float:
            if c.final is not None:
                return -c.final
            if c.combined is not None:
                return -c.combined
            return float("inf")

        dedup_out.sort(key=_rank_key)
    return dedup_out


def _apply_taste_blend(s: OrmSession, photos: list[CuratedPhoto]) -> None:
    """Fill ``taste`` and ``final`` on *photos* in place, when a model exists.

    No-op (fields stay None) when no taste model has been trained for this
    library, when the session is not file-backed, or on any load error — the
    ranking then falls back to pure aesthetic order.
    """
    if not photos:
        return
    from selects.ml import taste as taste_mod

    state_dir = taste_mod.state_dir_from_session(s)
    if state_dir is None:
        return
    model = taste_mod.load_model(state_dir)
    if model is None:
        return
    w = model.weight
    if w <= 0.0:
        return

    scores = taste_mod.taste_scores_by_photo_id(
        s, model, [c.photo_id for c in photos]
    )
    if not scores:
        return

    # Percentile-rank of combined within the surfaced set → aesthetic in [0,1].
    # Ties get their AVERAGE rank so photos with identical aesthetic scores
    # share the same aesthetic term (their order is then decided by taste).
    combined = np.array(
        [c.combined if c.combined is not None else 0.0 for c in photos],
        dtype=np.float64,
    )
    order = np.argsort(combined, kind="mergesort")
    ranks = np.empty(len(photos), dtype=np.float64)
    i = 0
    srt = combined[order]
    while i < len(photos):
        j = i
        while j + 1 < len(photos) and srt[j + 1] == srt[i]:
            j += 1
        ranks[order[i : j + 1]] = 0.5 * (i + j)
        i = j + 1
    denom = max(len(photos) - 1, 1)
    for c, rank in zip(photos, ranks):
        aes = float(rank) / denom if len(photos) > 1 else 1.0
        t = scores.get(c.photo_id)
        if t is None:
            # No embedding: neutral taste so the photo is neither boosted nor
            # penalized relative to its aesthetic rank.
            t = 0.5
        c.taste = float(t) if c.photo_id in scores else None
        c.final = (1.0 - w) * aes + w * float(t)
