"""The release gate must reject a bundle that exits successfully without indexing."""
from contextlib import closing
from pathlib import Path
import runpy
import sqlite3
import subprocess
import tempfile

import pytest


@pytest.mark.parametrize("empty_database", [False, True])
def test_bundle_smoke_rejects_success_without_a_photo(tmp_path, monkeypatch, empty_database):
    smoke = runpy.run_path(
        str(Path(__file__).resolve().parents[1] / "packaging" / "smoke_bundle.py")
    )["smoke_bundle"]
    executable = tmp_path / "selects.exe"
    executable.touch()

    def no_photos(command, **kwargs):
        # Simulate a silent indexing/decode failure with a zero exit status.
        if empty_database:
            state = Path(command[2]) / ".selects"
            state.mkdir()
            with closing(sqlite3.connect(state / "index.db")) as connection:
                connection.execute("CREATE TABLE photos (path TEXT, sha256 TEXT)")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(subprocess, "run", no_photos)
    with pytest.raises(RuntimeError, match="database|Expected one indexed photo"):
        smoke(executable)


def test_bundle_smoke_accepts_resolved_photo_path_under_temp_alias(tmp_path, monkeypatch):
    smoke = runpy.run_path(
        str(Path(__file__).resolve().parents[1] / "packaging" / "smoke_bundle.py")
    )["smoke_bundle"]
    actual = tmp_path / "actual"
    actual.mkdir()
    alias = tmp_path / "alias"
    try:
        alias.symlink_to(actual, target_is_directory=True)
    except OSError:
        pytest.skip("Creating directory symlinks requires privileges on this machine")
    executable = tmp_path / "selects"
    executable.touch()
    temporary_directory = tempfile.TemporaryDirectory
    monkeypatch.setattr(tempfile, "TemporaryDirectory", lambda **kw: temporary_directory(dir=alias, **kw))
    calls = []

    def index_resolved_path(command, **kwargs):
        photo = Path(command[2]) / "sample.jpg"
        assert photo != photo.resolve()  # The alias must actually exercise the regression.
        state = photo.parent / ".selects"
        state.mkdir(exist_ok=True)
        with closing(sqlite3.connect(state / "index.db")) as connection:
            connection.execute("CREATE TABLE IF NOT EXISTS photos (path TEXT UNIQUE, sha256 TEXT)")
            connection.execute("INSERT OR IGNORE INTO photos VALUES (?, ?)", (str(photo.resolve()), "fixture"))
            connection.commit()
        for directory in ("thumbs", "previews"):
            (state / directory).mkdir(exist_ok=True)
            (state / directory / "fixture.jpg").write_bytes(b"preview")
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(subprocess, "run", index_resolved_path)
    smoke(executable)
    assert len(calls) == 2
