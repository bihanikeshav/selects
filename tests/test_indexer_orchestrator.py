import shutil
from pathlib import Path

import pytest

from selects.config import get_folder_config
from selects.db import init_db, session_scope
from selects.db.models import Photo, PipelineState, Video
from selects.indexer.orchestrator import index_folder

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


def test_same_sha_different_paths_both_indexed(tmp_path):
    shutil.copy(FIXTURES_DIR / "small.jpg", tmp_path / "a.jpg")
    shutil.copy(FIXTURES_DIR / "small.jpg", tmp_path / "b.jpg")
    cfg = get_folder_config(tmp_path)
    n = index_folder(cfg)
    assert n == 2
    Session = init_db(cfg.db_path)
    with session_scope(Session) as s:
        photos = s.query(Photo).all()
        assert len(photos) == 2
        assert {p.sha256 for p in photos} == {photos[0].sha256}
        assert len({p.path for p in photos}) == 2
        assert (cfg.thumbs_dir / f"{photos[0].sha256}.jpg").exists()


def test_path_upsert_on_content_change_resets_pipeline(tmp_path):
    dest = tmp_path / "img.jpg"
    shutil.copy(FIXTURES_DIR / "small.jpg", dest)
    cfg = get_folder_config(tmp_path)
    index_folder(cfg)
    Session = init_db(cfg.db_path)
    with session_scope(Session) as s:
        photo = s.query(Photo).one()
        photo_id = photo.id
        old_sha = photo.sha256
        ps = s.query(PipelineState).filter_by(photo_id=photo_id).one()
        ps.classical_done = True
        ps.embedding_done = True
        ps.vl_done = True
        ps.ordering_done = True

    from PIL import Image

    Image.new("RGB", (80, 60), (0, 200, 0)).save(dest, "JPEG")

    n = index_folder(cfg)
    assert n == 0
    with session_scope(Session) as s:
        photo = s.query(Photo).one()
        assert photo.id == photo_id
        assert photo.sha256 != old_sha
        assert photo.width == 80
        assert photo.height == 60
        ps = s.query(PipelineState).filter_by(photo_id=photo_id).one()
        assert ps.classical_done is False
        assert ps.embedding_done is False
        assert ps.vl_done is False
        assert ps.ordering_done is False
        assert (cfg.thumbs_dir / f"{photo.sha256}.jpg").exists()


def test_index_folder_reports_unreadable_files(tmp_path):
    shutil.copy(FIXTURES_DIR / "small.jpg", tmp_path / "ok.jpg")
    (tmp_path / "bad.jpg").write_bytes(b"not a jpeg")
    cfg = get_folder_config(tmp_path)
    messages: list[tuple[int, int, str]] = []

    n = index_folder(cfg, on_progress=lambda i, t, name: messages.append((i, t, name)))

    assert n == 1
    Session = init_db(cfg.db_path)
    with session_scope(Session) as s:
        assert s.query(Photo).count() == 1
    fail_msgs = [name for _, _, name in messages if "could not be read" in name]
    assert fail_msgs
    assert fail_msgs[-1].startswith("1 ")


def test_prune_removes_missing_rows_and_orphan_thumbs(tmp_path):
    shutil.copy(FIXTURES_DIR / "small.jpg", tmp_path / "a.jpg")
    shutil.copy(FIXTURES_DIR / "small.jpg", tmp_path / "b.jpg")
    cfg = get_folder_config(tmp_path)
    assert index_folder(cfg) == 2

    Session = init_db(cfg.db_path)
    with session_scope(Session) as s:
        sha = s.query(Photo).first().sha256
    thumb = cfg.thumbs_dir / f"{sha}.jpg"
    preview = cfg.previews_dir / f"{sha}.jpg"
    assert thumb.exists() and preview.exists()

    # One copy deleted: row goes, shared thumb stays (other row still uses it).
    (tmp_path / "a.jpg").unlink()
    messages: list[str] = []
    assert index_folder(cfg, on_progress=lambda i, t, m: messages.append(m)) == 0
    with session_scope(Session) as s:
        assert [p.path for p in s.query(Photo).all()] == [str(tmp_path / "b.jpg")]
    assert "1 missing file(s) removed" in messages
    assert thumb.exists() and preview.exists()

    # Last copy deleted: row and the now-orphaned thumb/preview both go.
    (tmp_path / "b.jpg").unlink()
    index_folder(cfg)
    with session_scope(Session) as s:
        assert s.query(Photo).count() == 0
    assert not thumb.exists()
    assert not preview.exists()


def test_prune_removes_missing_video_rows(tmp_path):
    shutil.copy(FIXTURES_DIR / "small.mp4", tmp_path / "clip.mp4")
    cfg = get_folder_config(tmp_path)
    assert index_folder(cfg) == 1
    Session = init_db(cfg.db_path)
    with session_scope(Session) as s:
        sha = s.query(Video).one().sha256
    thumb = cfg.thumbs_dir / f"{sha}.jpg"
    assert thumb.exists()

    (tmp_path / "clip.mp4").unlink()
    index_folder(cfg)
    with session_scope(Session) as s:
        assert s.query(Video).count() == 0
    assert not thumb.exists()


def test_paths_subset_does_not_prune(tmp_path):
    shutil.copy(FIXTURES_DIR / "small.jpg", tmp_path / "a.jpg")
    cfg = get_folder_config(tmp_path)
    assert index_folder(cfg) == 1
    Session = init_db(cfg.db_path)

    (tmp_path / "a.jpg").unlink()
    shutil.copy(FIXTURES_DIR / "small.heic", tmp_path / "c.heic")
    messages: list[str] = []
    index_folder(
        cfg,
        on_progress=lambda i, t, m: messages.append(m),
        paths=[tmp_path / "c.heic"],
    )

    with session_scope(Session) as s:
        assert {Path(p.path).name for p in s.query(Photo).all()} == {"a.jpg", "c.heic"}
    assert not any("missing file" in m for m in messages)


def test_no_prune_message_when_nothing_missing(tmp_path):
    shutil.copy(FIXTURES_DIR / "small.jpg", tmp_path / "a.jpg")
    cfg = get_folder_config(tmp_path)
    index_folder(cfg)
    messages: list[str] = []
    index_folder(cfg, on_progress=lambda i, t, m: messages.append(m))
    assert not any("missing file" in m for m in messages)
