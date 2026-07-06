"""Tests for travelcull.export: file export (copy/zip) + XMP rating write-back."""
from __future__ import annotations

import zipfile
from pathlib import Path

import pytest
from PIL import Image

from travelcull.export import (
    ExportItem,
    export_photos,
    plan_xmp_write,
    preview_xmp_writes,
    write_xmp_ratings,
)


def _make_jpeg(path: Path, size=(64, 48)) -> None:
    Image.new("RGB", size, color=(120, 80, 40)).save(path, "JPEG")


class TestExportPhotosCopy:
    def test_copies_files_flat(self, tmp_path: Path) -> None:
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        p1 = src_dir / "a.jpg"
        p2 = src_dir / "b.jpg"
        _make_jpeg(p1)
        _make_jpeg(p2)

        out = tmp_path / "out"
        result = export_photos(
            [ExportItem(photo_id=1, path=p1), ExportItem(photo_id=2, path=p2)],
            out,
            mode="copy",
            structure="flat",
        )

        assert result.count == 2
        assert result.bytes > 0
        assert not result.skipped
        assert (out / "a.jpg").is_file()
        assert (out / "b.jpg").is_file()

    def test_by_day_structure_groups_into_subfolders(self, tmp_path: Path) -> None:
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        p1 = src_dir / "a.jpg"
        p2 = src_dir / "b.jpg"
        _make_jpeg(p1)
        _make_jpeg(p2)

        out = tmp_path / "out"
        result = export_photos(
            [
                ExportItem(photo_id=1, path=p1, day="2025-06-01"),
                ExportItem(photo_id=2, path=p2, day="2025-06-02"),
            ],
            out,
            mode="copy",
            structure="by-day",
        )

        assert result.count == 2
        assert (out / "2025-06-01" / "a.jpg").is_file()
        assert (out / "2025-06-02" / "b.jpg").is_file()

    def test_rank_prefixes_filename(self, tmp_path: Path) -> None:
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        p1 = src_dir / "photo.jpg"
        _make_jpeg(p1)

        out = tmp_path / "out"
        export_photos([ExportItem(photo_id=1, path=p1, rank=3)], out, mode="copy")

        assert (out / "003_photo.jpg").is_file()

    def test_missing_source_is_skipped_not_fatal(self, tmp_path: Path) -> None:
        out = tmp_path / "out"
        missing = tmp_path / "nope.jpg"
        result = export_photos([ExportItem(photo_id=1, path=missing)], out, mode="copy")

        assert result.count == 0
        assert len(result.skipped) == 1
        assert result.skipped[0]["photo_id"] == 1
        assert result.skipped[0]["reason"] == "missing"


class TestExportPhotosZip:
    def test_zip_mode_creates_archive_with_entries(self, tmp_path: Path) -> None:
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        p1 = src_dir / "a.jpg"
        _make_jpeg(p1)

        zip_target = tmp_path / "bundle.zip"
        result = export_photos([ExportItem(photo_id=1, path=p1)], zip_target, mode="zip")

        assert result.count == 1
        assert Path(result.path) == zip_target
        with zipfile.ZipFile(zip_target) as zf:
            assert "a.jpg" in zf.namelist()

    def test_zip_mode_into_directory_uses_default_name(self, tmp_path: Path) -> None:
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        p1 = src_dir / "a.jpg"
        _make_jpeg(p1)

        out_dir = tmp_path / "out"
        result = export_photos(
            [ExportItem(photo_id=1, path=p1)], out_dir, mode="zip", zip_name="mine.zip",
        )

        assert Path(result.path) == out_dir / "mine.zip"
        assert (out_dir / "mine.zip").is_file()


class TestXmpPlanning:
    def test_liked_verdict_maps_to_rating_5(self, tmp_path: Path) -> None:
        p = tmp_path / "a.jpg"
        _make_jpeg(p)
        plan = plan_xmp_write(1, p, "liked")
        assert plan.new_rating == 5
        assert plan.action == "write"
        assert plan.is_sidecar is False
        assert plan.target == str(p)

    def test_curated_verdict_maps_to_rating_4(self, tmp_path: Path) -> None:
        p = tmp_path / "a.jpg"
        _make_jpeg(p)
        plan = plan_xmp_write(1, p, "curated")
        assert plan.new_rating == 4

    def test_rejected_verdict_maps_to_rating_1(self, tmp_path: Path) -> None:
        p = tmp_path / "a.jpg"
        _make_jpeg(p)
        plan = plan_xmp_write(1, p, "rejected")
        assert plan.new_rating == 1

    def test_unknown_verdict_is_no_op(self, tmp_path: Path) -> None:
        p = tmp_path / "a.jpg"
        _make_jpeg(p)
        plan = plan_xmp_write(1, p, "skip")
        assert plan.action == "no_op"
        assert plan.reason is not None

    def test_raw_file_targets_sidecar_not_original(self, tmp_path: Path) -> None:
        raw = tmp_path / "IMG_0001.CR2"
        raw.write_bytes(b"fake raw bytes")
        plan = plan_xmp_write(1, raw, "liked")
        assert plan.is_sidecar is True
        assert plan.target == str(tmp_path / "IMG_0001.CR2.xmp")

    def test_missing_source_is_no_op(self, tmp_path: Path) -> None:
        p = tmp_path / "missing.jpg"
        plan = plan_xmp_write(1, p, "liked")
        assert plan.action == "no_op"
        assert plan.reason == "source file missing"


class TestXmpWriteBack:
    def test_writes_rating_into_jpeg_in_place(self, tmp_path: Path) -> None:
        p = tmp_path / "a.jpg"
        _make_jpeg(p)

        results = write_xmp_ratings([(1, p, "liked")])
        assert len(results) == 1
        assert results[0].action == "write"

        import pyexiv2

        img = pyexiv2.Image(str(p))
        try:
            xmp = img.read_xmp()
        finally:
            img.close()
        assert int(xmp["Xmp.xmp.Rating"]) == 5

    def test_raw_gets_sidecar_file_not_touched_in_place(self, tmp_path: Path) -> None:
        raw = tmp_path / "IMG_0002.NEF"
        original_bytes = b"totally fake raw content"
        raw.write_bytes(original_bytes)

        results = write_xmp_ratings([(1, raw, "curated")])
        assert results[0].action == "write"
        assert results[0].is_sidecar is True

        sidecar = tmp_path / "IMG_0002.NEF.xmp"
        assert sidecar.is_file()
        # The RAW itself must be untouched.
        assert raw.read_bytes() == original_bytes
        content = sidecar.read_text(encoding="utf-8")
        assert "<xmp:Rating>4</xmp:Rating>" in content

    def test_does_not_downgrade_existing_higher_rating_without_force(self, tmp_path: Path) -> None:
        p = tmp_path / "a.jpg"
        _make_jpeg(p)
        # Seed an existing rating of 5.
        write_xmp_ratings([(1, p, "liked")])

        # Now try to write "rejected" (rating 1) without force — should be skipped.
        results = write_xmp_ratings([(1, p, "rejected")])
        assert results[0].action == "skip_lower"

        import pyexiv2

        img = pyexiv2.Image(str(p))
        try:
            xmp = img.read_xmp()
        finally:
            img.close()
        assert int(xmp["Xmp.xmp.Rating"]) == 5

    def test_force_overrides_existing_higher_rating(self, tmp_path: Path) -> None:
        p = tmp_path / "a.jpg"
        _make_jpeg(p)
        write_xmp_ratings([(1, p, "liked")])

        results = write_xmp_ratings([(1, p, "rejected")], force=True)
        assert results[0].action == "write"

        import pyexiv2

        img = pyexiv2.Image(str(p))
        try:
            xmp = img.read_xmp()
        finally:
            img.close()
        assert int(xmp["Xmp.xmp.Rating"]) == 1

    def test_same_rating_is_skipped(self, tmp_path: Path) -> None:
        p = tmp_path / "a.jpg"
        _make_jpeg(p)
        write_xmp_ratings([(1, p, "liked")])

        results = write_xmp_ratings([(1, p, "liked")])
        assert results[0].action == "skip_same"


class TestPreviewXmpWrites:
    def test_preview_does_not_modify_files(self, tmp_path: Path) -> None:
        p = tmp_path / "a.jpg"
        _make_jpeg(p)

        plans = preview_xmp_writes([(1, p, "liked")])
        assert plans[0].action == "write"

        import pyexiv2

        img = pyexiv2.Image(str(p))
        try:
            xmp = img.read_xmp()
        finally:
            img.close()
        # No rating written yet — preview is a dry run.
        assert "Xmp.xmp.Rating" not in xmp
