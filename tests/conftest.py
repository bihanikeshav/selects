"""Shared pytest fixtures for travelcull tests."""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest


FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture()
def fixtures_dir() -> Path:
    """Return the path to the static fixtures directory."""
    return FIXTURES_DIR


@pytest.fixture()
def tmp_folder(tmp_path: Path) -> Path:
    """Return an empty temporary directory."""
    return tmp_path


@pytest.fixture()
def populated_folder(tmp_path: Path) -> Path:
    """Return a temporary directory pre-populated with sample files."""
    # Create a small directory tree with varied file types
    (tmp_path / "photos").mkdir()
    (tmp_path / "photos" / "sub").mkdir()
    (tmp_path / "videos").mkdir()
    (tmp_path / ".travelcull").mkdir()
    (tmp_path / ".git").mkdir()

    # Create dummy files
    (tmp_path / "photos" / "img001.jpg").write_bytes(b"\xff\xd8\xff" + b"\x00" * 100)
    (tmp_path / "photos" / "img002.HEIC").write_bytes(b"\x00" * 100)
    (tmp_path / "photos" / "sub" / "img003.jpeg").write_bytes(b"\xff\xd8\xff" + b"\x00" * 100)
    (tmp_path / "videos" / "clip001.mp4").write_bytes(b"\x00" * 200)
    (tmp_path / ".travelcull" / "hidden.jpg").write_bytes(b"\xff\xd8\xff" + b"\x00" * 100)
    (tmp_path / ".git" / "config").write_bytes(b"[core]\n")
    (tmp_path / "readme.txt").write_bytes(b"not a media file")

    return tmp_path
