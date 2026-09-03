"""Tests for selects.ml.categories."""
from __future__ import annotations

from selects.config import get_folder_config
from selects.db import init_db, session_scope
from selects.db.models import ClassicalScore, Photo, PhotoCategory, PhotoTag
from selects.ml.categories import assign_primary_category, run_category_stage


def test_assign_primary_category_rules():
    assert assign_primary_category(1, []) == "portrait"
    assert assign_primary_category(2, ["mountain"]) == "portrait"
    assert assign_primary_category(0, ["Lake", "car"]) == "landscape"
    assert assign_primary_category(None, ["sunset over ridge"]) == "landscape"
    assert assign_primary_category(0, ["car", "plate"]) == "object"
    assert assign_primary_category(0, []) == "unclassified"
    assert assign_primary_category(None, []) == "unclassified"


def test_run_category_stage_wipe_and_rewrite(tmp_path):
    cfg = get_folder_config(tmp_path)
    Session = init_db(cfg.db_path)
    with session_scope(Session) as s:
        portrait = Photo(path=str(tmp_path / "p.jpg"), sha256="a" * 64)
        landscape = Photo(path=str(tmp_path / "l.jpg"), sha256="b" * 64)
        obj = Photo(path=str(tmp_path / "o.jpg"), sha256="c" * 64)
        none = Photo(path=str(tmp_path / "n.jpg"), sha256="d" * 64)
        s.add_all([portrait, landscape, obj, none])
        s.flush()
        s.add(ClassicalScore(photo_id=portrait.id, faces_count=1))
        s.add(PhotoTag(photo_id=landscape.id, tag="mountain", score=0.9, source="ram"))
        s.add(PhotoTag(photo_id=obj.id, tag="car", score=0.8, source="ram"))
        ids = {
            "portrait": portrait.id,
            "landscape": landscape.id,
            "object": obj.id,
            "unclassified": none.id,
        }

    n = run_category_stage(cfg)
    assert n == 4

    with session_scope(Session) as s:
        cats = {row.photo_id: row.primary_category for row in s.query(PhotoCategory).all()}
    assert cats[ids["portrait"]] == "portrait"
    assert cats[ids["landscape"]] == "landscape"
    assert cats[ids["object"]] == "object"
    assert cats[ids["unclassified"]] == "unclassified"

    n2 = run_category_stage(cfg)
    assert n2 == 4
    with session_scope(Session) as s:
        assert s.query(PhotoCategory).count() == 4
