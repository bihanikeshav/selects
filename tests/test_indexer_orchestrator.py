import shutil
from pathlib import Path

import pytest

from travelcull.config import get_folder_config
from travelcull.db import init_db, session_scope
from travelcull.db.models import Photo, Video
from travelcull.indexer.orchestrator import index_folder

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture()
def populated_folder(tmp_path: Path) -> Path:
    """A temp folder with real image/video files for end-to-end indexing tests."""
    (tmp_path / "photos").mkdir()
    (tmp_path / "videos").mkdir()

    shutil.copy(FIXTURES_DIR / "small.jpg", tmp_path / "photos" / "img001.jpg")
    shutil.copy(FIXTURES_DIR / "small.heic", tmp_path / "photos" / "img002.heic")
    shutil.copy(FIXTURES_DIR / "small.mp4", tmp_path / "videos" / "clip001.mp4")

    return tmp_path


def test_index_folder_creates_photo_rows(populated_folder):
    cfg = get_folder_config(populated_folder)
    index_folder(cfg)
    Session = init_db(cfg.db_path)
    with session_scope(Session) as s:
        photos = s.query(Photo).all()
        assert len(photos) >= 2  # jpg + heic at minimum


def test_index_folder_creates_video_rows(populated_folder):
    cfg = get_folder_config(populated_folder)
    index_folder(cfg)
    Session = init_db(cfg.db_path)
    with session_scope(Session) as s:
        videos = s.query(Video).all()
        assert len(videos) == 1


def test_index_folder_is_idempotent(populated_folder):
    cfg = get_folder_config(populated_folder)
    index_folder(cfg)
    n2 = index_folder(cfg)
    assert n2 == 0


def test_indexed_photos_have_previews(populated_folder):
    cfg = get_folder_config(populated_folder)
    index_folder(cfg)
    Session = init_db(cfg.db_path)
    with session_scope(Session) as s:
        for photo in s.query(Photo).all():
            assert photo.thumb_path is not None
            assert (cfg.thumbs_dir / f"{photo.sha256}.jpg").exists()
