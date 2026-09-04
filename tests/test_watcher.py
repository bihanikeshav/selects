"""Tests for selects.watcher: detection, debounce, incremental indexing."""
from __future__ import annotations

from unittest.mock import patch

from selects.config import get_folder_config
from selects.watcher import Debouncer, LibraryWatcher, detect_candidates, run_incremental_index


def _make_cfg(tmp_path):
    folder = tmp_path / "lib"
    folder.mkdir()
    return get_folder_config(folder)


def test_detect_candidates_finds_new_file(tmp_path):
    cfg = _make_cfg(tmp_path)
    from selects.db import init_db

    init_db(cfg.db_path)

    new_file = cfg.folder / "photo.jpg"
    new_file.write_bytes(b"fake-jpeg-bytes")

    candidates = detect_candidates(cfg)
    assert new_file in candidates


def test_detect_candidates_ignores_already_indexed(tmp_path):
    cfg = _make_cfg(tmp_path)
    from selects.db import init_db, session_scope
    from selects.db.models import Photo

    Session = init_db(cfg.db_path)
    existing = cfg.folder / "existing.jpg"
    existing.write_bytes(b"data")
    mtime = existing.stat().st_mtime

    with session_scope(Session) as s:
        s.add(Photo(path=str(existing), sha256="abc", mtime=mtime))

    candidates = detect_candidates(cfg)
    assert existing not in candidates


def test_debounce_holds_until_size_stable(tmp_path):
    cfg = _make_cfg(tmp_path)
    from selects.db import init_db

    init_db(cfg.db_path)

    f = cfg.folder / "copying.jpg"
    f.write_bytes(b"partial")

    debouncer = Debouncer()

    # First poll: candidate seen for the first time -> not yet stable.
    candidates = detect_candidates(cfg)
    stable = debouncer.poll(candidates)
    assert f not in stable

    # File is still being written (size changes) -> still not stable.
    f.write_bytes(b"partial-more-bytes")
    candidates = detect_candidates(cfg)
    stable = debouncer.poll(candidates)
    assert f not in stable

    # File stops changing -> next poll with identical (size, mtime) is stable.
    candidates = detect_candidates(cfg)
    stable = debouncer.poll(candidates)
    assert f in stable


def test_run_incremental_index_uses_pipeline_stages(tmp_path):
    cfg = _make_cfg(tmp_path)
    from selects.db import init_db

    init_db(cfg.db_path)

    new_file = cfg.folder / "new.jpg"
    new_file.write_bytes(b"data")
    # A second, untouched file that should NOT be picked up by the
    # incremental run even though it exists on disk.
    other_file = cfg.folder / "other.jpg"
    other_file.write_bytes(b"other-data")

    with patch("selects.server.pipeline_runner.run_pipeline_stages") as mock_run:
        mock_run.return_value = {"index": 1}
        added = run_incremental_index(cfg, [new_file], publish=None)

    assert added == 1
    mock_run.assert_called_once()
    _args, kwargs = mock_run.call_args
    assert kwargs.get("paths") == [new_file]


def test_run_incremental_index_still_runs_pipeline_when_nothing_added(tmp_path):
    cfg = _make_cfg(tmp_path)
    from selects.db import init_db

    init_db(cfg.db_path)

    with patch("selects.server.pipeline_runner.run_pipeline_stages") as mock_run:
        mock_run.return_value = {"index": 0}
        added = run_incremental_index(cfg, [], publish=None)

    assert added == 0
    mock_run.assert_called_once()


class _FakeIndexManager:
    def __init__(self, allow: bool = True) -> None:
        self.allow = allow
        self.began = 0
        self.ended = 0

    def begin_indexing(self) -> bool:
        self.began += 1
        return self.allow

    def end_indexing(self) -> None:
        self.ended += 1


def test_poll_once_skips_when_indexing_lock_held(tmp_path):
    cfg = _make_cfg(tmp_path)
    from selects.db import init_db

    init_db(cfg.db_path)
    photo = cfg.folder / "photo.jpg"
    photo.write_bytes(b"data")

    mgr = _FakeIndexManager(allow=False)
    w = LibraryWatcher(cfg, interval=60, manager=mgr)
    w.poll_once()  # first sighting: not yet stable

    with patch("selects.watcher.run_incremental_index") as mock_run:
        added = w.poll_once()

    assert added == 0
    mock_run.assert_not_called()
    assert mgr.began == 1
    assert mgr.ended == 0


def test_poll_once_acquires_and_releases_indexing_lock(tmp_path):
    cfg = _make_cfg(tmp_path)
    from selects.db import init_db

    init_db(cfg.db_path)
    photo = cfg.folder / "photo.jpg"
    photo.write_bytes(b"data")

    mgr = _FakeIndexManager(allow=True)
    w = LibraryWatcher(cfg, interval=60, manager=mgr)
    w.poll_once()

    with patch("selects.watcher.run_incremental_index") as mock_run:
        mock_run.return_value = 1
        added = w.poll_once()

    assert added == 1
    mock_run.assert_called_once()
    assert mgr.began == 1
    assert mgr.ended == 1


def test_poll_once_releases_lock_when_pipeline_raises(tmp_path):
    cfg = _make_cfg(tmp_path)
    from selects.db import init_db

    init_db(cfg.db_path)
    photo = cfg.folder / "photo.jpg"
    photo.write_bytes(b"data")

    mgr = _FakeIndexManager(allow=True)
    w = LibraryWatcher(cfg, interval=60, manager=mgr)
    w.poll_once()

    with patch("selects.watcher.run_incremental_index") as mock_run:
        mock_run.side_effect = RuntimeError("boom")
        try:
            w.poll_once()
        except RuntimeError:
            pass

    assert mgr.began == 1
    assert mgr.ended == 1


def test_watcher_stop_then_start(tmp_path):
    cfg = _make_cfg(tmp_path)
    from selects.db import init_db

    init_db(cfg.db_path)
    w = LibraryWatcher(cfg, interval=60)
    w.poll_once = lambda: 0  # type: ignore[method-assign]
    w.start()
    assert w.is_running
    w.stop()
    w.start()
    assert w.is_running
    w.stop()
    assert not w.is_running
