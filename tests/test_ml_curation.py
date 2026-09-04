"""CLIP-IQA curation: rank, non-gate when missing, top-quartile percentile."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from selects.config import get_folder_config
from selects.db import init_db, session_scope
from selects.db.models import AestheticScore, Embedding, Photo
from selects.ml.curation import curate


_SIGLIP = b"\x00" * 2304


def _seed(tmp_path: Path, iqas: list[float | None]):
    cfg = get_folder_config(tmp_path)
    Session = init_db(cfg.db_path)
    ids: list[int] = []
    with session_scope(Session) as s:
        for i, iqa in enumerate(iqas):
            p = Photo(path=str(tmp_path / f"{i}.jpg"), sha256=f"{i:064x}")
            s.add(p)
            s.flush()
            s.add(Embedding(photo_id=p.id, siglip=_SIGLIP, aesthetic_iqa=iqa))
            ids.append(p.id)
    return Session, ids


def test_curate_ranks_by_iqa(tmp_path: Path) -> None:
    Session, ids = _seed(tmp_path, [0.2, 0.9, 0.5])
    with session_scope(Session) as s:
        out = curate(s, ids, pct_floor=0.0)
    assert [c.iqa for c in out] == [0.9, 0.5, 0.2]
    assert [c.combined for c in out] == [0.9, 0.5, 0.2]
    assert all(c.ap25 is None and c.nima is None for c in out)


def test_curate_no_iqa_is_nongate_not_empty(tmp_path: Path) -> None:
    Session, ids = _seed(tmp_path, [None, None, None])
    with session_scope(Session) as s:
        out = curate(s, ids)
    assert len(out) == 3
    assert {c.photo_id for c in out} == set(ids)
    assert all(c.iqa is None and c.combined is None for c in out)


def test_curate_prefers_ap25_over_iqa(tmp_path: Path) -> None:
    Session, ids = _seed(tmp_path, [0.9, 0.2])
    with session_scope(Session) as s:
        s.add(AestheticScore(photo_id=ids[0], ap25_score=2.0))
        s.add(AestheticScore(photo_id=ids[1], ap25_score=8.0))
        s.flush()
        out = curate(s, ids, pct_floor=0.0)
    assert [c.photo_id for c in out] == [ids[1], ids[0]]
    assert out[0].ap25 == pytest.approx(8.0)
    assert out[0].combined == pytest.approx(0.8)


def test_curate_percentile_75_keeps_top_quartile(tmp_path: Path) -> None:
    iqas = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8]
    Session, ids = _seed(tmp_path, iqas)
    floor = float(np.percentile(iqas, 75))
    with session_scope(Session) as s:
        out = curate(s, ids, pct_floor=75.0)
    kept = sorted(c.iqa for c in out)
    expected = sorted(v for v in iqas if v >= floor)
    assert kept == expected
    assert kept == [0.7, 0.8]


def test_compute_rank_threshold_caches_per_db_state(tmp_path: Path, monkeypatch) -> None:
    from selects.ml import curation

    Session, ids = _seed(tmp_path, [0.1, 0.5, 0.9])
    curation.clear_threshold_cache()

    calls: list[int] = []
    real_percentile = np.percentile

    def counting_percentile(*args, **kwargs):
        calls.append(1)
        return real_percentile(*args, **kwargs)

    monkeypatch.setattr(curation.np, "percentile", counting_percentile)

    with session_scope(Session) as s:
        first = curation.compute_rank_threshold(s, pct_floor=50.0)
        second = curation.compute_rank_threshold(s, pct_floor=50.0)
    assert first == second
    assert len(calls) == 1

    # A different percentile is a different cache key.
    with session_scope(Session) as s:
        curation.compute_rank_threshold(s, pct_floor=90.0)
    assert len(calls) == 2


def test_compute_rank_threshold_cache_invalidated_by_a_write(tmp_path: Path) -> None:
    from selects.ml import curation

    Session, ids = _seed(tmp_path, [0.1, 0.5, 0.9])
    curation.clear_threshold_cache()

    with session_scope(Session) as s:
        before = curation.compute_rank_threshold(s, pct_floor=50.0)

    with session_scope(Session) as s:
        p = Photo(path=str(tmp_path / "extra.jpg"), sha256=f"{99:064x}")
        s.add(p)
        s.flush()
        s.add(Embedding(photo_id=p.id, siglip=_SIGLIP, aesthetic_iqa=1.0))

    with session_scope(Session) as s:
        after = curation.compute_rank_threshold(s, pct_floor=50.0)

    assert after != before
    assert after == pytest.approx(0.7)


def test_curate_accepts_a_precomputed_library_threshold(tmp_path: Path, monkeypatch) -> None:
    from selects.ml import curation

    Session, ids = _seed(tmp_path, [0.1, 0.5, 0.9])
    curation.clear_threshold_cache()

    def boom(*args, **kwargs):  # pragma: no cover - must not run
        raise AssertionError("compute_rank_threshold should be bypassed")

    monkeypatch.setattr(curation, "compute_rank_threshold", boom)

    with session_scope(Session) as s:
        out = curate(
            s, ids, pct_floor=0.0, library_pct_floor=50.0, library_threshold=0.6,
        )
    assert sorted(c.iqa for c in out) == [0.9]


def test_compute_rank_threshold_is_thread_safe_across_a_stamp_change(
    tmp_path: Path, monkeypatch
) -> None:
    """Concurrent misses must not corrupt the cache while it evicts stale keys.

    `/api/stories` is a sync endpoint served from the threadpool, so several
    requests can miss at once. Two threads evicting the same stale keys while a
    third iterates the dict is a RuntimeError or a KeyError, i.e. a 500.

    The race is widened deliberately: a large batch of stale entries makes the
    eviction scan long enough to be preempted, a slow compute puts every thread
    into the critical section together, and a tiny switch interval forces the
    interpreter to interleave them.
    """
    import sys
    import threading
    import time

    from selects.ml import curation

    Session, ids = _seed(tmp_path, [0.1, 0.5, 0.9])
    curation.clear_threshold_cache()

    # Entries left behind by an earlier state of the library: every one of them
    # is evicted on the first miss below.
    for i in range(20_000):
        curation._THRESHOLD_CACHE[(("stale-stamp",), float(i), 0.4, 50.0)] = 0.0

    real_compute = curation._compute_rank_threshold_uncached

    def slow_compute(*args, **kwargs):
        time.sleep(0.02)
        return real_compute(*args, **kwargs)

    monkeypatch.setattr(curation, "_compute_rank_threshold_uncached", slow_compute)

    old_interval = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)

    results: list[float | None] = []
    errors: list[BaseException] = []
    barrier = threading.Barrier(8)

    def worker(pct: float) -> None:
        try:
            barrier.wait(timeout=10)
            with session_scope(Session) as s:
                value = curation.compute_rank_threshold(s, pct_floor=pct)
            if pct == 50.0:
                results.append(value)
        except BaseException as exc:  # noqa: BLE001 - the assertion is "none raised"
            errors.append(exc)

    threads = [
        threading.Thread(target=worker, args=(50.0 if i % 2 == 0 else 90.0,))
        for i in range(8)
    ]
    try:
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
    finally:
        sys.setswitchinterval(old_interval)

    assert errors == []
    assert results == [pytest.approx(0.5)] * 4
    # The stale generation is gone and only the fresh one remains.
    assert all(k[0] != ("stale-stamp",) for k in curation._THRESHOLD_CACHE)
    curation.clear_threshold_cache()


def test_compute_rank_threshold_does_not_hold_the_lock_across_the_scan(
    tmp_path: Path, monkeypatch
) -> None:
    """The lock guards the cache dict only, never the library-wide DB scan.

    Holding it across the scan serialised every concurrent request behind one
    full pass over every scored photo; double-checked locking keeps the dict
    safe without making the scan a global critical section.
    """
    from selects.ml import curation

    Session, ids = _seed(tmp_path, [0.1, 0.5, 0.9])
    curation.clear_threshold_cache()

    real_compute = curation._compute_rank_threshold_uncached
    held: list[bool] = []

    def checking_compute(*args, **kwargs):
        held.append(curation._THRESHOLD_LOCK.locked())
        return real_compute(*args, **kwargs)

    monkeypatch.setattr(curation, "_compute_rank_threshold_uncached", checking_compute)

    with session_scope(Session) as s:
        value = curation.compute_rank_threshold(s, pct_floor=50.0)

    assert held == [False]
    assert value == pytest.approx(0.5)
    assert not curation._THRESHOLD_LOCK.locked()

    # The answer was still stored, so a second call is a cache hit.
    with session_scope(Session) as s:
        assert curation.compute_rank_threshold(s, pct_floor=50.0) == pytest.approx(0.5)
    assert held == [False]
    curation.clear_threshold_cache()


def test_curate_tie_in_a_burst_surfaces_the_lower_id_whatever_the_input_order(
    tmp_path: Path,
) -> None:
    """Two photos in one moment with identical scores must always collapse to
    the same one. Callers pass ids in their own order (stories pass taken_at
    order) and the fetch is chunked, so the tie-break has to be canonical:
    curate() sorts the ids, and the lower id is the one that surfaces.

    The moment's primary is a third photo, so no explicit user pick short-cuts
    the comparison being tested.
    """
    from datetime import datetime

    from selects.db.models import Moment, MomentMember

    Session, ids = _seed(tmp_path, [0.6, 0.6, 0.1])
    lo, hi, other = ids[0], ids[1], ids[2]
    with session_scope(Session) as s:
        m = Moment(
            primary_photo_id=other,
            started_at=datetime(2024, 1, 1),
            ended_at=datetime(2024, 1, 1, 0, 1),
            size=2,
        )
        s.add(m)
        s.flush()
        s.add(MomentMember(moment_id=m.id, photo_id=lo, rank=0))
        s.add(MomentMember(moment_id=m.id, photo_id=hi, rank=1))

    for order in ([lo, hi], [hi, lo]):
        with session_scope(Session) as s:
            out = curate(s, order, pct_floor=0.0)
        assert [c.photo_id for c in out] == [lo], f"input order {order}"


def test_curate_min_keep_fallback_is_stable_across_input_orders(tmp_path: Path) -> None:
    """The min_keep fallback ranks by score; a block of equal scores must fall
    back to photo-id order, not to whatever permutation an unstable sort left."""
    Session, ids = _seed(tmp_path, [0.9, 0.5, 0.5, 0.5])
    picked = []
    for order in (list(ids), list(reversed(ids))):
        with session_scope(Session) as s:
            # pct_floor=100 gates everything but the 0.9, so the fallback runs.
            out = curate(s, order, sort="best", pct_floor=100.0, min_keep=3)
        picked.append(sorted(c.photo_id for c in out))
    assert picked[0] == picked[1]
    # The 0.9 plus the two lowest ids of the tied 0.5 block.
    assert picked[0] == [ids[0], ids[1], ids[2]]
