from datetime import datetime
import struct

import pytest

from selects.decode.video import probe
from selects.decode.video_dates import parse_creation_time, read_mp4_creation_time


@pytest.fixture()
def dated_video(fixtures_dir) -> bytes:
    data = bytearray((fixtures_dir / "small.mp4").read_bytes())
    position = data.index(b"mvhd")
    assert data[position + 4] == 0
    seconds = int((datetime(2026, 1, 2, 3, 4, 5) - datetime(1904, 1, 1)).total_seconds())
    data[position + 8:position + 12] = struct.pack(">I", seconds)
    return bytes(data)


@pytest.mark.parametrize("version,extended", [(0, False), (1, False), (1, True)])
def test_mp4_movie_header_versions(tmp_path, version, extended):
    expected = datetime(2045, 1, 2, 3, 4, 5) if version else datetime(2026, 1, 2, 3, 4, 5)
    seconds = int((expected - datetime(1904, 1, 1)).total_seconds())
    payload = bytes([version, 0, 0, 0]) + seconds.to_bytes(8 if version else 4, "big")
    mvhd = struct.pack(">I4s", len(payload) + 8, b"mvhd") + payload
    moov = (struct.pack(">I4sQ", 1, b"moov", len(mvhd) + 16) if extended
            else struct.pack(">I4s", len(mvhd) + 8, b"moov")) + mvhd
    path = tmp_path / "movie.mov"
    path.write_bytes(moov)
    assert read_mp4_creation_time(path) == expected


@pytest.mark.parametrize("payload", [
    struct.pack(">I4s", 200, b"moov"),
    struct.pack(">I4s", 4, b"moov"),
    struct.pack(">I4s", 1, b"moov"),
    struct.pack(">I4sI4s", 16, b"moov", 8, b"mvhd"),
])
def test_invalid_headers_fail_without_inventing_dates(tmp_path, payload):
    path = tmp_path / "bad.mp4"
    path.write_bytes(payload)
    with pytest.raises(ValueError):
        read_mp4_creation_time(path)


def test_zero_creation_time_is_absent(fixtures_dir):
    assert read_mp4_creation_time(fixtures_dir / "small.mp4") is None


@pytest.mark.parametrize("raw,expected", [
    ("2026-01-02T03:04:05.123Z", datetime(2026, 1, 2, 3, 4, 5, 123000)),
    ("2026-01-02T08:34:05+05:30", datetime(2026, 1, 2, 3, 4, 5)),
    ("invalid", None), ("1904-01-01T00:00:00Z", None), ("", None),
])
def test_iso_creation_times(raw, expected):
    assert parse_creation_time(raw) == expected


def test_unicode_video_date_with_and_without_ffprobe(tmp_path, dated_video, monkeypatch):
    path = tmp_path / "семья" / "movie.mp4"
    path.parent.mkdir()
    path.write_bytes(dated_video)
    expected = datetime(2026, 1, 2, 3, 4, 5)
    assert probe(path).taken_at == expected

    def missing(*args, **kwargs):
        raise FileNotFoundError("ffprobe")

    monkeypatch.setattr("selects.decode.video.subprocess.check_output", missing)
    result = probe(path)
    assert result.taken_at == expected
    assert result.metadata_error is None


@pytest.mark.parametrize("existing_date", [None, datetime(2025, 3, 4)])
def test_video_date_backfill_preserves_analysis_and_then_skips(
    tmp_path, dated_video, monkeypatch, existing_date,
):
    from selects.config import get_folder_config
    from selects.db import init_db, session_scope
    from selects.db.models import Video
    from selects.indexer.metadata import METADATA_VERSION
    from selects.indexer.orchestrator import index_folder
    from tests.test_indexer_orchestrator import _dispose_engine

    path = tmp_path / "семья" / "movie.mp4"
    path.parent.mkdir()
    path.write_bytes(dated_video)
    cfg = get_folder_config(tmp_path)
    try:
        assert index_folder(cfg) == 1
        Session = init_db(cfg.db_path)
        with session_scope(Session) as session:
            video = session.query(Video).one()
            assert video.taken_at == datetime(2026, 1, 2, 3, 4, 5)
            video.taken_at = existing_date
            video.metadata_version = None
            video.siglip = b"cached analysis"
            video.highlights_json = "[]"
            preview = cfg.state_dir / video.preview_path
            original_mtime = preview.stat().st_mtime_ns

        def unexpected(*args, **kwargs):
            pytest.fail("Recovery must skip decoding and previously checked metadata")

        monkeypatch.setattr("selects.indexer.orchestrator.decode_first_frame", unexpected)
        assert index_folder(cfg) == int(existing_date is None)
        with session_scope(Session) as session:
            video = session.query(Video).one()
            assert video.taken_at == (existing_date or datetime(2026, 1, 2, 3, 4, 5))
            assert video.metadata_version == METADATA_VERSION
            assert video.siglip == b"cached analysis"
            assert video.highlights_json == "[]"
        assert preview.stat().st_mtime_ns == original_mtime
        monkeypatch.setattr("selects.indexer.orchestrator.probe", unexpected)
        assert index_folder(cfg) == 0
    finally:
        _dispose_engine(cfg.db_path)
