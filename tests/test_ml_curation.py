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
