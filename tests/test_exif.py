"""Tests for travelcull.indexer.exif."""
from __future__ import annotations

from pathlib import Path

import pytest

from travelcull.indexer.exif import ExifData, read_exif


class TestReadExifReturnsExifData:
    def test_returns_exifdata_type(self, fixtures_dir: Path) -> None:
        result = read_exif(fixtures_dir / "small.jpg")
        assert isinstance(result, ExifData)

    def test_missing_file_returns_empty_exifdata(self, tmp_path: Path) -> None:
        result = read_exif(tmp_path / "nonexistent.jpg")
        assert isinstance(result, ExifData)
        assert result.taken_at is None
        assert result.width is None
        assert result.height is None
        assert result.camera is None
        assert result.gps_lat is None
        assert result.gps_lon is None

    def test_small_jpg_width_height(self, fixtures_dir: Path) -> None:
        result = read_exif(fixtures_dir / "small.jpg")
        # small.jpg was created as 640x480 with Pillow
        assert result.width == 640
        assert result.height == 480

    def test_no_exif_fields_none_by_default(self, fixtures_dir: Path) -> None:
        # The minimal Pillow-generated JPEG has no DateTimeOriginal or GPS
        result = read_exif(fixtures_dir / "small.jpg")
        assert result.taken_at is None
        assert result.gps_lat is None
        assert result.gps_lon is None

    def test_corrupted_file_returns_empty_exifdata(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.jpg"
        bad.write_bytes(b"not a jpeg at all")
        result = read_exif(bad)
        assert isinstance(result, ExifData)
