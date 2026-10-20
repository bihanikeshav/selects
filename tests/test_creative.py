"""Creative curation contract, grouping, scale and original-file safety."""

from __future__ import annotations

import csv
import hashlib
import json
import math
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from click.testing import CliRunner
from PIL import Image
from pydantic import ValidationError

from selects.cli import main
from selects.config import FolderConfig
from selects.creative.candidates import generate_candidates, load_photos, make_batches
from selects.creative.reviews import import_reviews, report
from selects.creative.schema import CreativeReview
from selects.creative.selection import shortlist
from selects.db import init_db, session_scope
from selects.db.models import (
    AestheticScore,
    ClassicalScore,
    Embedding,
    Moment,
    MomentMember,
    Photo,
    PipelineState,
    Story,
    StoryItem,
    Swipe,
)
from selects.export import plan_xmp_write, preview_xmp_writes, write_xmp_ratings


def seed(tmp_path, scores=(0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8), suffix=".ARW"):
    cfg = FolderConfig(folder=tmp_path)
    Session = init_db(cfg.db_path)
    cfg.previews_dir.mkdir()
    ids = []
    with session_scope(Session) as s:
        for i, score in enumerate(scores):
            path = tmp_path / f"shot_{i}{suffix}"
            path.write_bytes(f"original-{i}".encode())
            sha = hashlib.sha256(path.read_bytes()).hexdigest()
            Image.new("RGB", (8, 6), (i, 0, 0)).save(cfg.previews_dir / f"{sha}.jpg")
            p = Photo(
                path=str(path),
                sha256=sha,
                format="RAW" if suffix == ".ARW" else "JPEG",
                preview_path=f"previews/{sha}.jpg",
                taken_at=datetime(2026, 1, 1) + timedelta(minutes=i),
            )
            s.add(p)
            s.flush()
            ids.append(p.id)
            s.add(
                PipelineState(photo_id=p.id, classical_done=True, embedding_done=score is not None)
            )
            s.add(ClassicalScore(photo_id=p.id, auto_reject=False))
            if score is not None:
                s.add(Embedding(photo_id=p.id, siglip=b"\x00" * 2304, aesthetic_iqa=score))
    return cfg, Session, ids


def add_moment(Session, ids, primary=None):
    with session_scope(Session) as s:
        moment = Moment(
            primary_photo_id=primary or ids[0],
            size=len(ids),
            started_at=datetime(2026, 1, 1),
            ended_at=datetime(2026, 1, 1),
        )
        s.add(moment)
        s.flush()
        for rank, pid in enumerate(ids):
            s.add(MomentMember(moment_id=moment.id, photo_id=pid, rank=rank))
        return moment.id


def review(pid, i=None, score=8, decision="grade", **overrides):
    return dict(
        {
            "photo_id": pid,
            "filename": f"shot_{pid - 1 if i is None else i}.ARW",
            "technical_score": 7,
            "composition": score,
            "light": score,
            "moment": score,
            "story_value": score,
            "uniqueness": score,
            "grading_potential": score,
            "decision": decision,
            "reason": "Layered scene and meaningful gesture.",
            "grade_direction": "Protect sky highlights; lift the subject selectively.",
        },
        **overrides,
    )


def import_batch(cfg, rows, envelope=False):
    path = cfg.state_dir / "batch.json"
    data = {"schema_version": 1, "library": str(cfg.folder), "reviews": rows} if envelope else rows
    path.write_text(json.dumps(data), encoding="utf-8")
    return import_reviews(cfg, path)


def ids_of(manifest):
    return {p["photo_id"] for p in manifest["candidates"]}


def test_default_keeps_top_half_and_unscored_without_decoding(tmp_path, monkeypatch):
    cfg, _, ids = seed(tmp_path, (0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, None))
    monkeypatch.setattr("selects.decode.decode", lambda *a: pytest.fail("must reuse previews"))
    manifest = generate_candidates(cfg)
    assert ids_of(manifest) == set(ids[4:])
    assert all(p["preview_path"].startswith(str(cfg.previews_dir)) for p in manifest["candidates"])
    assert manifest["options"]["percentile"] == 50


def test_manual_keeps_override_auto_reject_and_manual_reject_is_excluded(tmp_path):
    cfg, Session, ids = seed(tmp_path)
    with session_scope(Session) as s:
        for pid, decision in zip(ids[:3], ("keep", "silver", "reject")):
            s.get(ClassicalScore, pid).auto_reject = True
            s.add(Swipe(photo_id=pid, decision=decision))
        s.get(ClassicalScore, ids[-1]).auto_reject = True
    result = generate_candidates(cfg, include_maybe=True)
    assert set(ids[:2]) <= ids_of(result)
    assert ids[2] not in ids_of(result) and ids[-1] not in ids_of(result)


def test_moment_primary_is_rescued_and_members_can_be_compared(tmp_path):
    cfg, Session, ids = seed(tmp_path)
    mid = add_moment(Session, ids[:3], primary=ids[0])
    result = generate_candidates(cfg, percentile=100, include_story=False)
    assert ids[0] in ids_of(result)
    assert ids[1] not in ids_of(result)
    comparison = generate_candidates(cfg, moment_id=mid)
    assert ids_of(comparison) == set(ids[:3])
    assert len(comparison["batches"]) == 1


def test_rejected_primary_falls_back_to_best_eligible_member(tmp_path):
    cfg, Session, ids = seed(tmp_path)
    add_moment(Session, ids[:3])
    with session_scope(Session) as s:
        s.get(ClassicalScore, ids[0]).auto_reject = True
    result = generate_candidates(cfg, percentile=100)
    assert ids[0] not in ids_of(result) and ids[2] in ids_of(result)


def test_extreme_light_sharpness_and_hyperiqa_rescue_low_iqa(tmp_path):
    cfg, Session, ids = seed(tmp_path)
    with session_scope(Session) as s:
        s.get(ClassicalScore, ids[0]).luma_mean = 0.2
        s.get(ClassicalScore, ids[1]).blur = 2000
        s.add(AestheticScore(photo_id=ids[2], ap25_score=9.9))
    result = generate_candidates(cfg, percentile=100)
    assert set(ids[:3]) <= ids_of(result)
    p = next(p for p in result["candidates"] if p["photo_id"] == ids[2])
    assert p["aesthetic"] == pytest.approx(0.99)


def test_story_and_unique_scene_rescue_and_day_membership(tmp_path):
    cfg, Session, ids = seed(tmp_path)
    with session_scope(Session) as s:
        st = Story(day="place:station", title="Station", photo_count=1)
        day = Story(day="2026-01-01", title="Trip day", photo_count=8)
        s.add_all([st, day])
        s.flush()
        sid, day_id = st.id, day.id
        s.add(StoryItem(story_id=sid, photo_id=ids[0], rank=0, scene_label="transport"))
    manifest = generate_candidates(cfg, percentile=100)
    p = next(p for p in manifest["candidates"] if p["photo_id"] == ids[0])
    assert sid in p["story_ids"] and day_id in p["story_ids"]
    assert "scene_representative" in p["candidate_reasons"]
    assert ids[0] not in ids_of(generate_candidates(cfg, percentile=100, include_story=False))
    assert ids_of(generate_candidates(cfg, story_id=day_id, include_maybe=True)) == set(ids)


def test_cap_is_explicit_and_preserves_protected_photos(tmp_path):
    cfg, Session, ids = seed(tmp_path)
    add_moment(Session, ids[:2])
    with pytest.raises(ValueError, match="protected"):
        generate_candidates(cfg, max_candidates=1)
    manifest = generate_candidates(cfg, max_candidates=3, include_maybe=True)
    assert len(manifest["candidates"]) == 3
    assert ids[0] in ids_of(manifest) and ids[-1] in ids_of(manifest)
    assert manifest["summary"]["omitted_by_cap"] == 5


def test_exact_hashes_collapse_discretionary_candidates(tmp_path):
    cfg, Session, ids = seed(tmp_path, (0.1, 0.1, 0.8))
    with session_scope(Session) as s:
        s.get(Photo, ids[1]).sha256 = s.get(Photo, ids[0]).sha256
    result = generate_candidates(cfg, percentile=0)
    assert ids_of(result) == {ids[0], ids[2]}
    assert result["candidates"][0]["exact_duplicate_ids"] == ids[:2]


def test_existing_taste_changes_machine_ranking_without_changing_aesthetic(tmp_path):
    import numpy as np
    from selects.ml.taste import TasteModel, save_model

    cfg, Session, ids = seed(tmp_path, (0.1, 0.5, 0.9))
    with session_scope(Session) as s:
        s.add(Swipe(photo_id=ids[0], decision="keep"))
    save_model(cfg.state_dir, TasteModel(np.zeros(1152), 0, 1000, 0.6, "2026-01-01"))
    result = generate_candidates(cfg)
    p = next(p for p in result["candidates"] if p["photo_id"] == ids[0])
    assert p["aesthetic"] == 0.1 and p["taste"] == 0.5
    assert p["selects_score"] != p["aesthetic"]


def test_missing_or_unsafe_preview_is_reported_without_original_fallback(tmp_path):
    cfg, Session, ids = seed(tmp_path, (None, None))
    with session_scope(Session) as s:
        s.get(Photo, ids[0]).preview_path = "../shot_0.ARW"
        s.get(Photo, ids[1]).preview_path = "previews/missing.jpg"
    result = generate_candidates(cfg)
    assert result["summary"]["missing_previews"] == 2
    assert all(p["preview_path"] is None for p in result["candidates"])


def test_batches_keep_normal_moments_together_and_split_giant_bursts(tmp_path):
    cfg, Session, ids = seed(tmp_path, [None] * 95)
    mid = add_moment(Session, ids[20:40])
    result = generate_candidates(cfg, batch_size=30)
    batches = result["batches"]
    assert all(len(b["photo_ids"]) <= 30 for b in batches)
    assert sum(set(ids[20:40]) <= set(b["photo_ids"]) for b in batches) == 1
    assert sorted(pid for b in batches for pid in b["photo_ids"]) == ids
    giant = [dict(p, moment_id=mid) for p in result["candidates"]]
    assert all(b["split_moment"] for b in make_batches(giant, 30))


@pytest.mark.parametrize("value", [-1, 10.1, math.nan, math.inf, True, "8"])
def test_schema_rejects_invalid_numeric_scores(value):
    with pytest.raises(ValidationError):
        CreativeReview.model_validate(review(1, grading_potential=value))


def test_weights_and_technical_gate_are_separate_from_aesthetic():
    parsed = CreativeReview.model_validate(review(1, composition=9, technical_score=2))
    assert parsed.creative_score == 8.22
    with pytest.raises(ValidationError, match="weighted"):
        CreativeReview.model_validate(review(1, creative_score=9))
    with pytest.raises(ValidationError, match="technical failure"):
        CreativeReview.model_validate(review(1, technical_failure=True))
    assert CreativeReview.model_validate(review(1, decision="reject", technical_failure=True))


def test_import_merges_batches_and_updates_by_identity(tmp_path):
    cfg, _, ids = seed(tmp_path)
    first = import_batch(cfg, [review(ids[0])], envelope=True)
    assert first["imported"] == 1
    created = json.loads(Path(first["path"]).read_text())["reviews"][0]["created_at"]
    second = import_batch(cfg, [review(ids[1], decision="story"), review(ids[0], decision="hero")])
    saved = json.loads(Path(second["path"]).read_text())["reviews"]
    assert len(saved) == 2 and saved[0]["decision"] == "hero"
    assert saved[0]["created_at"] == created
    assert saved[0]["sha256"] == hashlib.sha256(b"original-0").hexdigest()


@pytest.mark.parametrize(
    "bad",
    [
        review(999),
        review(1, filename="wrong.ARW"),
        review(1, sha256="wrong"),
        review(1, decision="keep"),
        review(1, unwanted=3),
    ],
)
def test_invalid_import_is_atomic(tmp_path, bad):
    cfg, _, ids = seed(tmp_path)
    result = import_batch(cfg, [review(ids[0])])
    before = Path(result["path"]).read_bytes()
    with pytest.raises(ValueError):
        import_batch(cfg, [review(ids[1]), bad])
    assert Path(result["path"]).read_bytes() == before


def test_duplicate_ids_and_cross_library_envelope_fail(tmp_path):
    cfg, _, ids = seed(tmp_path)
    with pytest.raises(ValueError, match="duplicate photo_id"):
        import_batch(cfg, [review(ids[0]), review(ids[0])])
    path = cfg.state_dir / "foreign.json"
    path.write_text(json.dumps({"library": str(tmp_path / "other"), "reviews": [review(ids[0])]}))
    with pytest.raises(ValueError, match="another library"):
        import_reviews(cfg, path)
    assert not (cfg.state_dir / "creative" / "reviews.json").exists()


def test_moment_nominations_need_pair_justification_and_never_three(tmp_path):
    cfg, Session, ids = seed(tmp_path)
    add_moment(Session, ids[:3])
    import_batch(cfg, [review(ids[0])])
    with pytest.raises(ValueError, match="moment_exception"):
        import_batch(cfg, [review(ids[1])])
    import_batch(
        cfg,
        [
            review(
                ids[1],
                moment_exception="expression",
                moment_exception_reason="Different meaningful gestures, verified in previews.",
            )
        ],
    )
    with pytest.raises(ValueError, match="more than two"):
        import_batch(cfg, [review(ids[2])])


def test_exact_duplicate_nominations_are_refused(tmp_path):
    cfg, Session, ids = seed(tmp_path)
    with session_scope(Session) as s:
        s.get(Photo, ids[1]).sha256 = s.get(Photo, ids[0]).sha256
    with pytest.raises(ValueError, match="exact duplicates"):
        import_batch(cfg, [review(ids[0]), review(ids[1])])


def test_reports_preserve_machine_scores_and_unreviewed_status(tmp_path):
    cfg, _, ids = seed(tmp_path)
    before = {p: p.read_bytes() for p in tmp_path.glob("*.ARW")}
    import_batch(
        cfg,
        [
            review(ids[0], decision="hero"),
            review(ids[1], decision="story", reason="A, B\nC"),
            review(ids[2], decision="reject", reason="=FORMULA"),
        ],
    )
    result = report(cfg)
    assert result["summary"]["unreviewed"] == 5
    rows = json.loads((cfg.state_dir / "creative" / "creative_results.json").read_text())["reviews"]
    assert rows[0]["selects_score"] == 0.1 and rows[0]["creative_score"] == 8
    with (cfg.state_dir / "creative" / "creative_results.csv").open(newline="") as f:
        csv_rows = list(csv.DictReader(f))
    assert csv_rows[1]["reason"] == "A, B\nC"
    assert csv_rows[2]["reason"] == "'=FORMULA"
    md = (cfg.state_dir / "creative" / "selection.md").read_text()
    assert "## Hero" in md and "## Grade" in md and "## Story" in md
    assert all(p.read_bytes() == content for p, content in before.items())


def test_shortlist_uses_diversity_and_nested_caps_without_promoting_maybes(tmp_path):
    cfg, _, ids = seed(tmp_path, [0.5] * 5)
    rows = [
        review(
            pid,
            score=9.2,
            decision="hero",
            scene_type="landscape",
            location="same hill",
            composition_key="same wide",
        )
        for pid in ids[:3]
    ]
    rows += [
        review(ids[3], score=8.7, decision="hero", scene_type="human", location="station"),
        review(ids[4], score=10, decision="maybe"),
    ]
    import_batch(cfg, rows)
    result = shortlist(cfg, heroes=2, grades=3, story=4)
    assert (result["heroes"], result["grading_total"], result["story_total"]) == (2, 3, 4)
    report(cfg)
    effective = json.loads((cfg.state_dir / "creative" / "creative_results.json").read_text())[
        "reviews"
    ]
    heroes = {r["photo_id"] for r in effective if r["decision"] == "hero"}
    assert heroes == {ids[0], ids[3]}
    assert next(r for r in effective if r["photo_id"] == ids[4])["decision"] == "maybe"


def test_shortlist_does_not_fill_weak_nominations_and_stale_selection_is_detected(tmp_path):
    cfg, _, ids = seed(tmp_path)
    import_batch(cfg, [review(ids[0], decision="hero"), review(ids[1], decision="maybe")])
    assert shortlist(cfg)["story_total"] == 1
    import_batch(cfg, [review(ids[2], decision="grade")])
    assert report(cfg)["warnings"]
    with pytest.raises(ValueError, match="caps"):
        shortlist(cfg, heroes=12, grades=10)


def test_reindexed_source_invalidates_old_review(tmp_path):
    cfg, Session, ids = seed(tmp_path)
    import_batch(cfg, [review(ids[0])])
    with session_scope(Session) as s:
        s.get(Photo, ids[0]).sha256 = "new content hash"
    with pytest.raises(ValueError, match="changed since review"):
        report(cfg)
    import_batch(cfg, [review(ids[0], sha256="new content hash")])
    assert not report(cfg)["warnings"]


def test_replace_import_can_retire_reviews_for_pruned_photos(tmp_path):
    cfg, Session, ids = seed(tmp_path)
    import_batch(cfg, [review(ids[0])])
    with session_scope(Session) as s:
        s.delete(s.get(Photo, ids[0]))
    path = cfg.state_dir / "complete.json"
    path.write_text(json.dumps([review(ids[1])]))
    assert import_reviews(cfg, path, replace=True)["total_reviewed"] == 1
    assert report(cfg)["summary"]["grade"] == 1


def test_index_pass_repairs_missing_previews_using_existing_decoder(tmp_path):
    from selects.indexer.orchestrator import index_folder

    original = tmp_path / "photo.jpg"
    Image.new("RGB", (40, 30)).save(original)
    before = original.read_bytes()
    cfg = FolderConfig(folder=tmp_path)
    assert index_folder(cfg) == 1
    cached = next(cfg.previews_dir.glob("*.jpg"))
    cached.unlink()
    assert index_folder(cfg) == 0
    assert cached.is_file() and original.read_bytes() == before


def test_cli_end_to_end_batches_import_report_and_dry_run(tmp_path):
    cfg, _, ids = seed(tmp_path, [None] * 65)
    runner = CliRunner()
    result = runner.invoke(
        main, ["creative", "candidates", str(tmp_path), "--batch-size", "30", "--batch", "2"]
    )
    assert result.exit_code == 0, result.output
    manifest = json.loads(result.stdout)
    assert len(manifest["candidates"]) == 30
    saved = json.loads((cfg.state_dir / "creative" / "candidates.json").read_text())
    assert len(saved["candidates"]) == 65
    batch = cfg.state_dir / "batch.json"
    batch.write_text(json.dumps([review(ids[0], decision="hero")]))
    for command in (
        ["import", str(tmp_path), str(batch)],
        ["report", str(tmp_path)],
        ["xmp", str(tmp_path)],
    ):
        result = runner.invoke(main, ["creative", *command])
        assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["dry_run"] is True
    assert not (tmp_path / "shot_0.xmp").exists()
    assert (tmp_path / "shot_0.ARW").read_bytes() == b"original-0"
    applied = runner.invoke(main, ["creative", "xmp", str(tmp_path), "--apply"])
    assert applied.exit_code == 0, applied.output
    assert "<xmp:Rating>5</xmp:Rating>" in (tmp_path / "shot_0.xmp").read_text()


def test_cli_missing_index_and_invalid_batch_have_no_outputs(tmp_path):
    runner = CliRunner()
    status = runner.invoke(main, ["creative", "status", str(tmp_path)])
    assert json.loads(status.stdout)["indexed"] is False
    result = runner.invoke(main, ["creative", "candidates", str(tmp_path)])
    assert result.exit_code != 0 and "not indexed" in result.output
    assert not (tmp_path / ".selects").exists()
    cfg, _, _ = seed(tmp_path)
    result = runner.invoke(main, ["creative", "candidates", str(tmp_path), "--batch", "99"])
    assert result.exit_code != 0
    assert not (cfg.state_dir / "creative").exists()


@pytest.mark.parametrize("which", ["state", "creative", "output", "preview"])
def test_symlinked_state_outputs_and_previews_are_safe(tmp_path, which):
    cfg, _, _ = seed(tmp_path)
    original = tmp_path / "shot_0.ARW"
    if which == "state":
        other = tmp_path / "elsewhere"
        other.mkdir()
        cfg = FolderConfig(folder=other)
        cfg.state_dir.symlink_to(tmp_path / ".selects", target_is_directory=True)
    elif which == "creative":
        (cfg.state_dir / "creative").symlink_to(tmp_path, target_is_directory=True)
    elif which == "output":
        (cfg.state_dir / "creative").mkdir()
        (cfg.state_dir / "creative" / "selection.md").symlink_to(original)
    else:
        preview = next(cfg.previews_dir.glob("*.jpg"))
        preview.unlink()
        preview.symlink_to(original)
        result = generate_candidates(cfg, include_maybe=True)
        assert result["summary"]["missing_previews"] == 1
        assert original.read_bytes() == b"original-0"
        return
    with pytest.raises(ValueError, match="unsafe"):
        report(cfg)
    assert original.read_bytes() == b"original-0"


@pytest.mark.parametrize(
    "decision,rating", [("hero", 5), ("grade", 4), ("story", 3), ("maybe", 2), ("reject", 1)]
)
def test_creative_xmp_maps_every_decision_without_changing_originals(tmp_path, decision, rating):
    raw = tmp_path / "photo.ARW"
    raw.write_bytes(b"RAW bytes")
    jpeg = tmp_path / "jpeg.jpg"
    Image.new("RGB", (10, 10)).save(jpeg)
    before = jpeg.read_bytes()
    plans = preview_xmp_writes(
        [(1, raw, decision), (2, jpeg, decision)], sidecar_only=True, library_root=tmp_path
    )
    assert all(p.new_rating == rating and p.is_sidecar for p in plans)
    assert not (tmp_path / "photo.xmp").exists()
    results = write_xmp_ratings(
        [(1, raw, decision), (2, jpeg, decision)], sidecar_only=True, library_root=tmp_path
    )
    assert all(p.action == "write" for p in results)
    assert raw.read_bytes() == b"RAW bytes" and jpeg.read_bytes() == before


def test_xmp_preserves_higher_rating_and_existing_metadata(tmp_path):
    raw = tmp_path / "photo.ARW"
    raw.write_bytes(b"RAW bytes")
    write_xmp_ratings([(1, raw, "hero")], sidecar_only=True)
    sidecar = tmp_path / "photo.xmp"
    import pyexiv2

    image = pyexiv2.Image(str(sidecar))
    image.modify_xmp({"Xmp.dc.description": "Keep my edit metadata"})
    image.close()
    assert write_xmp_ratings([(1, raw, "reject")], sidecar_only=True)[0].action == "skip_lower"
    assert (
        write_xmp_ratings([(1, raw, "grade")], sidecar_only=True, force=True)[0].action == "write"
    )
    image = pyexiv2.Image(str(sidecar))
    metadata = image.read_xmp()
    image.close()
    assert metadata["Xmp.xmp.Rating"] == "4"
    assert "Keep my edit metadata" in str(metadata["Xmp.dc.description"])
    assert raw.read_bytes() == b"RAW bytes"


@pytest.mark.parametrize("hardlink", [False, True])
def test_xmp_never_follows_sidecar_links_to_originals(tmp_path, hardlink):
    raw = tmp_path / "photo.ARW"
    raw.write_bytes(b"RAW bytes")
    target = tmp_path / "photo.xmp"
    target.hardlink_to(raw) if hardlink else target.symlink_to(raw)
    result = write_xmp_ratings([(1, raw, "hero")], sidecar_only=True, library_root=tmp_path)
    assert result[0].action == "no_op"
    assert raw.read_bytes() == b"RAW bytes"


def test_xmp_outside_library_and_colliding_stems_are_refused(tmp_path):
    cfg, Session, ids = seed(tmp_path, [None, None])
    raw = tmp_path / "shot_0.ARW"
    assert (
        plan_xmp_write(1, raw, "hero", library_root=tmp_path / "other", sidecar_only=True).action
        == "no_op"
    )
    with session_scope(Session) as s:
        s.get(Photo, ids[1]).path = str(tmp_path / "shot_0.NEF")
    import_batch(cfg, [review(ids[0]), review(ids[1], filename="shot_0.NEF")])
    result = CliRunner().invoke(main, ["creative", "xmp", str(tmp_path), "--apply"])
    assert result.exit_code != 0 and "collision" in result.output
    assert not (tmp_path / "shot_0.xmp").exists()


def test_raw_jpeg_pair_keeps_separate_creative_ratings(tmp_path):
    cfg, Session, ids = seed(tmp_path, [None, None])
    jpeg = tmp_path / "shot_0.jpg"
    Image.new("RGB", (8, 6)).save(jpeg)
    before = jpeg.read_bytes()
    with session_scope(Session) as s:
        s.get(Photo, ids[1]).path = str(jpeg)
        s.get(Photo, ids[1]).sha256 = hashlib.sha256(before).hexdigest()
    import_batch(
        cfg,
        [review(ids[0], decision="hero"), review(ids[1], filename=jpeg.name, decision="reject")],
    )
    result = CliRunner().invoke(main, ["creative", "xmp", str(tmp_path), "--apply"])
    assert result.exit_code == 0, result.output
    assert "<xmp:Rating>5</xmp:Rating>" in (tmp_path / "shot_0.xmp").read_text()
    assert "<xmp:Rating>1</xmp:Rating>" in (tmp_path / "shot_0.jpg.xmp").read_text()
    assert jpeg.read_bytes() == before
    assert (tmp_path / "shot_0.ARW").read_bytes() == b"original-0"


def test_stale_shortlist_cannot_apply_xmp_until_refreshed(tmp_path):
    cfg, _, ids = seed(tmp_path)
    import_batch(cfg, [review(ids[0], decision="hero")])
    shortlist(cfg)
    import_batch(cfg, [review(ids[1], decision="grade")])
    result = CliRunner().invoke(main, ["creative", "xmp", str(tmp_path), "--apply"])
    assert result.exit_code != 0 and "stale" in result.output
    assert not list(tmp_path.glob("*.xmp"))


def test_ten_thousand_photo_candidate_pass_with_sqlite_999_limit(tmp_path):
    from tests.conftest import engine_for, sqlite_999_variables

    cfg = FolderConfig(folder=tmp_path)
    Session = init_db(cfg.db_path)
    with session_scope(Session) as s:
        s.add_all(Photo(path=str(tmp_path / f"{i}.ARW"), sha256=f"{i:064x}") for i in range(10050))
    with sqlite_999_variables(engine_for(cfg.db_path)):
        result = generate_candidates(cfg, max_candidates=80)
        assert result["summary"]["indexed"] == 10050
        assert len(result["candidates"]) == 80
        assert len(result["batches"]) == 2
        assert all(p["aesthetic"] is None for p in result["candidates"])


def test_context_does_not_write_creative_reviews_to_database(tmp_path):
    from sqlalchemy import inspect

    cfg, Session, _ = seed(tmp_path)
    assert len(load_photos(cfg)) == 8
    with Session() as s:
        assert "creative_reviews" not in inspect(s.bind).get_table_names()


@pytest.mark.parametrize("directory", ["семья", "旅行", "photos_📷"])
@pytest.mark.parametrize("suffix", [".ARW", ".jpg"])
def test_repeat_unicode_xmp_exports_preserve_ratings_and_metadata(tmp_path, monkeypatch, directory, suffix):
    import pyexiv2
    from selects.export import rating_sidecar_path

    folder = tmp_path / directory
    folder.mkdir()
    source = folder / f"photo{suffix}"
    source.write_bytes(b"synthetic original")
    before = source.read_bytes()
    target = rating_sidecar_path(source)

    def narrow_path_api(*args, **kwargs):
        pytest.fail("Sidecars must not depend on Exiv2's narrow filename API")

    monkeypatch.setattr(pyexiv2, "Image", narrow_path_api)
    assert write_xmp_ratings([(1, source, "hero")], sidecar_only=True)[0].action == "write"
    with pyexiv2.ImageData(target.read_bytes()) as image:
        image.modify_xmp({"Xmp.dc.description": "Сохранить edits"})
        target.write_bytes(image.get_bytes())
    assert plan_xmp_write(1, source, "story", sidecar_only=True).existing_rating == 5
    assert write_xmp_ratings([(1, source, "story")], sidecar_only=True)[0].action == "skip_lower"
    assert write_xmp_ratings([(1, source, "grade")], sidecar_only=True, force=True)[0].action == "write"
    with pyexiv2.ImageData(target.read_bytes()) as image:
        metadata = image.read_xmp()
    assert metadata["Xmp.xmp.Rating"] == "4"
    assert "Сохранить edits" in str(metadata["Xmp.dc.description"])
    assert write_xmp_ratings([(1, source, "grade")], sidecar_only=True)[0].action == "skip_same"
    assert write_xmp_ratings([(1, source, "hero")], sidecar_only=True)[0].action == "write"
    assert source.read_bytes() == before


@pytest.mark.parametrize("readonly", [False, True])
def test_sidecar_update_failure_keeps_original_sidecar(tmp_path, monkeypatch, readonly):
    raw = tmp_path / "photo.ARW"
    raw.write_bytes(b"synthetic original")
    write_xmp_ratings([(1, raw, "hero")], sidecar_only=True)
    target = tmp_path / "photo.xmp"
    before = target.read_bytes()
    if readonly:
        target.chmod(0o444)

    def failed_replace(*args):
        raise OSError("synthetic replacement failure")

    monkeypatch.setattr("selects.export.os.replace", failed_replace)
    try:
        result = write_xmp_ratings([(1, raw, "grade")], sidecar_only=True, force=True)
        assert result[0].action == "no_op"
        assert target.read_bytes() == before
        assert not list(tmp_path.glob(".rating-*"))
    finally:
        target.chmod(0o600)


@pytest.mark.parametrize("change", ["same_size_and_mtime", "different_size", "missing"])
@pytest.mark.parametrize("force", [False, True])
def test_changed_original_blocks_all_sidecars_before_writing(tmp_path, change, force):
    import os

    cfg, _, ids = seed(tmp_path, [0.8, 0.9])
    import_batch(cfg, [review(pid, decision="hero") for pid in ids])
    shortlist(cfg)
    path = tmp_path / "shot_1.ARW"
    original_stat = path.stat()
    if change == "missing":
        path.unlink()
    else:
        path.write_bytes(b"different1" if change == "same_size_and_mtime" else b"replacement")
        if change == "same_size_and_mtime":
            assert path.stat().st_size == original_stat.st_size
            os.utime(path, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
    args = ["creative", "xmp", str(tmp_path), "--apply"] + (["--force"] if force else [])
    result = CliRunner().invoke(main, args)
    assert result.exit_code != 0
    assert "reindex" in result.output
    assert not list(tmp_path.glob("*.xmp"))


def test_source_hashing_is_limited_to_explicit_apply(tmp_path, monkeypatch):
    from selects.indexer.walker import sha256_of

    cfg, _, ids = seed(tmp_path, [0.8, 0.9])
    import_batch(cfg, [review(ids[0], decision="hero")])
    shortlist(cfg)
    hashed = []

    def record_hash(path):
        hashed.append(path)
        return sha256_of(path)

    monkeypatch.setattr("selects.creative.reviews.sha256_of", record_hash)
    runner = CliRunner()
    for args in (["status", str(tmp_path)], ["report", str(tmp_path)], ["xmp", str(tmp_path)]):
        result = runner.invoke(main, ["creative", *args])
        assert result.exit_code == 0, result.output
    assert hashed == []
    (tmp_path / "shot_1.ARW").write_bytes(b"unreviewed source changed")
    result = runner.invoke(main, ["creative", "xmp", str(tmp_path), "--apply"])
    assert result.exit_code == 0, result.output
    assert hashed == [tmp_path / "shot_0.ARW"]
