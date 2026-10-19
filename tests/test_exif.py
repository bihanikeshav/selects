"""Tests for selects.indexer.exif."""
from __future__ import annotations

from pathlib import Path
from datetime import datetime
from unittest.mock import Mock

import pyexiv2
import pytest

from selects.indexer.exif import ExifData, read_exif


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
        assert result.error == "FileNotFoundError"

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
        assert result.error is None

    def test_corrupted_file_returns_empty_exifdata(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.jpg"
        bad.write_bytes(b"not a jpeg at all")
        result = read_exif(bad)
        assert isinstance(result, ExifData)
        assert result.error is not None


@pytest.fixture()
def tagged_jpeg(fixtures_dir: Path) -> bytes:
    """Synthetic metadata only; no personal photos or EXIF in regression data."""
    with pyexiv2.ImageData((fixtures_dir / "small.jpg").read_bytes()) as image:
        image.modify_exif({
            "Exif.Photo.DateTimeOriginal": "2026:01:02 03:04:05",
            "Exif.Image.Make": "Test",
            "Exif.Image.Model": "Камера",
            "Exif.GPSInfo.GPSLatitude": "12/1 30/1 0/1",
            "Exif.GPSInfo.GPSLatitudeRef": "S",
            "Exif.GPSInfo.GPSLongitude": "45/1 15/1 0/1",
            "Exif.GPSInfo.GPSLongitudeRef": "W",
        })
        return image.get_bytes()


@pytest.mark.parametrize("directory", ["семья", "旅行", "photos_📷"])
def test_unicode_path_metadata_matches_ascii(tmp_path, tagged_jpeg, directory):
    ascii_path = tmp_path / "sample.jpg"
    unicode_path = tmp_path / directory / "снимок.jpg"
    unicode_path.parent.mkdir()
    ascii_path.write_bytes(tagged_jpeg)
    unicode_path.write_bytes(tagged_jpeg)

    result = read_exif(unicode_path)
    assert result == read_exif(ascii_path)
    assert result.error is None
    assert result.taken_at == datetime(2026, 1, 2, 3, 4, 5)
    assert result.camera == "Test Камера"
    assert result.gps_lat == pytest.approx(-12.5)
    assert result.gps_lon == pytest.approx(-45.25)
    assert (result.width, result.height) == (640, 480)


def test_failed_path_open_retries_bytes(tmp_path, tagged_jpeg, monkeypatch, caplog):
    path = tmp_path / "sample.jpg"
    path.write_bytes(tagged_jpeg)
    expected = read_exif(path)
    path_reader = Mock(side_effect=RuntimeError("synthetic path open failure"))
    monkeypatch.setattr(pyexiv2, "Image", path_reader)

    assert read_exif(path) == expected
    path_reader.assert_called_once_with(str(path))
    assert not caplog.records


def test_successful_path_open_does_not_read_bytes(fixtures_dir, monkeypatch):
    byte_reader = Mock(side_effect=AssertionError("unnecessary byte read"))
    monkeypatch.setattr(pyexiv2, "ImageData", byte_reader)
    assert read_exif(fixtures_dir / "small.jpg").error is None
    byte_reader.assert_not_called()


def test_unreadable_metadata_warns_without_private_details(fixtures_dir, monkeypatch, caplog):
    monkeypatch.setattr(pyexiv2, "Image", Mock(side_effect=RuntimeError("private path")))
    monkeypatch.setattr(pyexiv2, "ImageData", Mock(side_effect=ValueError("private tags")))
    result = read_exif(fixtures_dir / "small.jpg")

    assert result.error == "ValueError"
    assert result.taken_at is None
    assert (result.width, result.height) == (640, 480)
    assert "Could not read EXIF metadata (ValueError)" in caplog.text
    assert "private" not in caplog.text
    assert str(fixtures_dir) not in caplog.text


@pytest.mark.parametrize("fallback", [False, True])
def test_reader_closed_if_metadata_read_fails(fixtures_dir, monkeypatch, fallback):
    image = Mock()
    image.read_exif.side_effect = RuntimeError("synthetic read failure")
    monkeypatch.setattr(pyexiv2, "Image", Mock(
        side_effect=RuntimeError() if fallback else None, return_value=image,
    ))
    monkeypatch.setattr(pyexiv2, "ImageData", Mock(return_value=image))

    assert read_exif(fixtures_dir / "small.jpg").error == "RuntimeError"
    image.close.assert_called_once()


def test_unicode_metadata_persisted_during_indexing(tmp_path, tagged_jpeg):
    from selects.config import get_folder_config
    from selects.db import init_db, session_scope
    from selects.db.models import Photo
    from selects.indexer.orchestrator import index_folder

    path = tmp_path / "семья" / "sample.jpg"
    path.parent.mkdir()
    path.write_bytes(tagged_jpeg)
    cfg = get_folder_config(tmp_path)
    try:
        assert index_folder(cfg) == 1
        with session_scope(init_db(cfg.db_path)) as session:
            photo = session.query(Photo).one()
            assert photo.taken_at == datetime(2026, 1, 2, 3, 4, 5)
            assert photo.camera == "Test Камера"
            assert photo.gps_lat == pytest.approx(-12.5)
            assert photo.gps_lon == pytest.approx(-45.25)
    finally:
        from tests.test_indexer_orchestrator import _dispose_engine

        _dispose_engine(cfg.db_path)
