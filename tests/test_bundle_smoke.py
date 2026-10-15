"""The release gate must reject a bundle that exits successfully without indexing."""
from contextlib import closing
from pathlib import Path
import runpy
import sqlite3
import subprocess

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
