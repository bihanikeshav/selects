"""Verify indexing using the shipped executable, outside the source checkout.

Usage: python packaging/smoke_bundle.py dist/selects/selects[.exe]
No ML weights or third-party Python packages are needed by this checker.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tempfile


def smoke_bundle(executable: Path) -> None:
    executable = executable.resolve(strict=True)
    fixture = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "small.jpg"
    with tempfile.TemporaryDirectory(prefix="selects-bundle-smoke-") as temp:
        work = Path(temp)
        # Spaces and non-ASCII characters exercise typical Windows user paths.
        library = work / "Holiday photos café"
        library.mkdir()
        photo = library / "sample.jpg"
        shutil.copyfile(fixture, photo)
        env = os.environ.copy()
        env.pop("PYTHONPATH", None)
        env.pop("PYTHONHOME", None)
        env["SELECTS_REGISTRY"] = str(work / "libraries.json")

        for operation in ("index", "re-index"):
            result = subprocess.run(
                [str(executable), "index", str(library), "--pass", "index"],
                cwd=work, env=env, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=120,
            )
            if result.returncode:
                raise RuntimeError(
                    f"Bundle indexing failed ({result.returncode}):\n"
                    f"{result.stdout}\n{result.stderr}"
                )
            # A successful exit alone is insufficient: indexing can skip files
            # after a decode failure. Check the actual DB and generated assets.
            db = library / ".selects" / "index.db"
            if not db.is_file():
                raise RuntimeError("Bundle did not create the library database")
            with closing(sqlite3.connect(db)) as connection:
                rows = connection.execute("SELECT path, sha256 FROM photos").fetchall()
            if len(rows) != 1 or Path(rows[0][0]) != photo:
                raise RuntimeError(f"Expected one indexed photo, got {rows!r}")
            for directory in ("thumbs", "previews"):
                if not (library / ".selects" / directory / f"{rows[0][1]}.jpg").is_file():
                    raise RuntimeError(f"Bundle did not generate {directory}")
            print(f"Bundle {operation} passed: one photo with thumbnail and preview")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("executable", type=Path)
    smoke_bundle(parser.parse_args().executable)
