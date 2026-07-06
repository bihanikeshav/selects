"""Tests for travelcull.watcher: detection, debounce, incremental indexing."""
from __future__ import annotations

from unittest.mock import patch

from travelcull.config import get_folder_config
from travelcull.watcher import Debouncer, detect_candidates, run_incremental_index


def _make_cfg(tmp_path):
    folder = tmp_path / "lib"
    folder.mkdir()
    return get_folder_config(folder)


def test_detect_candidates_finds_new_file(tmp_path):
    cfg = _make_cfg(tmp_path)
    from travelcull.db import init_db

    init_db(cfg.db_path)

    new_file = cfg.folder / "photo.jpg"
    new_file.write_bytes(b"fake-jpeg-bytes")

    candidates = detect_candidates(cfg)
    assert new_file in candidates


def test_detect_candidates_ignores_already_indexed(tmp_path):
    cfg = _make_cfg(tmp_path)
    from travelcull.db import init_db, session_scope
    from travelcull.db.models import Photo

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
    from travelcull.db import init_db

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


def test_run_incremental_index_only_indexes_given_paths(tmp_path):
    cfg = _make_cfg(tmp_path)
    from travelcull.db import init_db

    init_db(cfg.db_path)

    new_file = cfg.folder / "new.jpg"
    new_file.write_bytes(b"data")
    # A second, untouched file that should NOT be picked up by the
    # incremental run even though it exists on disk.
    other_file = cfg.folder / "other.jpg"
    other_file.write_bytes(b"other-data")

    with patch("travelcull.indexer.orchestrator.index_folder") as mock_index, \
         patch("travelcull.pipeline.run_classical_stage") as mock_classical, \
         patch("travelcull.ml.embed.run_embedding_stage") as mock_embed, \
         patch("travelcull.ml.tags.run_tag_stage") as mock_tag, \
         patch("travelcull.ml.stories.run_story_stage") as mock_story:
        mock_index.return_value = 1

        added = run_incremental_index(cfg, [new_file], publish=None)

    assert added == 1
    _, kwargs = mock_index.call_args
    assert kwargs["paths"] == [new_file]
    mock_classical.assert_called_once()
    mock_embed.assert_called_once()
    mock_tag.assert_called_once()
    mock_story.assert_called_once()


def test_run_incremental_index_skips_stages_when_nothing_added(tmp_path):
    cfg = _make_cfg(tmp_path)
    from travelcull.db import init_db

    init_db(cfg.db_path)

    with patch("travelcull.indexer.orchestrator.index_folder") as mock_index, \
         patch("travelcull.pipeline.run_classical_stage") as mock_classical:
        mock_index.return_value = 0
        added = run_incremental_index(cfg, [], publish=None)

    assert added == 0
    mock_classical.assert_not_called()
