"""Tests for travelcull.indexer.walker."""
from __future__ import annotations

from pathlib import Path

import pytest

from travelcull.indexer.walker import FileKind, classify, sha256_of, walk_supported


class TestClassify:
    def test_jpg_lower(self) -> None:
        assert classify(Path("photo.jpg")) == FileKind.JPEG

    def test_jpg_upper(self) -> None:
        assert classify(Path("photo.JPG")) == FileKind.JPEG

    def test_jpeg_mixed(self) -> None:
        assert classify(Path("photo.JPEG")) == FileKind.JPEG

    def test_heic_lower(self) -> None:
        assert classify(Path("photo.heic")) == FileKind.HEIC

    def test_heif_upper(self) -> None:
        assert classify(Path("photo.HEIF")) == FileKind.HEIC

    def test_dng(self) -> None:
        assert classify(Path("photo.dng")) == FileKind.RAW

    def test_cr2(self) -> None:
        assert classify(Path("photo.cr2")) == FileKind.RAW

    def test_cr3(self) -> None:
        assert classify(Path("photo.cr3")) == FileKind.RAW

    def test_nef(self) -> None:
        assert classify(Path("photo.nef")) == FileKind.RAW

    def test_arw(self) -> None:
        assert classify(Path("photo.arw")) == FileKind.RAW

    def test_mp4_lower(self) -> None:
        assert classify(Path("clip.mp4")) == FileKind.VIDEO

    def test_mov_upper(self) -> None:
        assert classify(Path("clip.MOV")) == FileKind.VIDEO

    def test_mkv(self) -> None:
        assert classify(Path("clip.mkv")) == FileKind.VIDEO

    def test_unsupported_txt(self) -> None:
        assert classify(Path("readme.txt")) is None

    def test_unsupported_no_ext(self) -> None:
        assert classify(Path("Makefile")) is None

    def test_unsupported_png(self) -> None:
        assert classify(Path("image.png")) is None


class TestWalkSupported:
    def test_finds_files_in_subdirs(self, populated_folder: Path) -> None:
        found = list(walk_supported(populated_folder))
        paths = [p for p, _ in found]
        # Should find img001.jpg, img002.HEIC, sub/img003.jpeg, clip001.mp4
        assert len(found) == 4

    def test_skips_travelcull_dir(self, populated_folder: Path) -> None:
        found = list(walk_supported(populated_folder))
        paths = [str(p) for p, _ in found]
        assert not any(".travelcull" in p for p in paths)

    def test_skips_git_dir(self, populated_folder: Path) -> None:
        found = list(walk_supported(populated_folder))
        paths = [str(p) for p, _ in found]
        assert not any(".git" in p for p in paths)

    def test_ignores_unsupported_files(self, populated_folder: Path) -> None:
        found = list(walk_supported(populated_folder))
        paths = [str(p) for p, _ in found]
        assert not any("readme.txt" in p for p in paths)

    def test_correct_kinds_assigned(self, populated_folder: Path) -> None:
        found = {p.name: kind for p, kind in walk_supported(populated_folder)}
        assert found["img001.jpg"] == FileKind.JPEG
        assert found["img002.HEIC"] == FileKind.HEIC
        assert found["img003.jpeg"] == FileKind.JPEG
        assert found["clip001.mp4"] == FileKind.VIDEO

    def test_skips_node_modules(self, tmp_path: Path) -> None:
        nm = tmp_path / "node_modules"
        nm.mkdir()
        (nm / "photo.jpg").write_bytes(b"\xff\xd8\xff" + b"\x00" * 10)
        assert list(walk_supported(tmp_path)) == []

    def test_skips_pycache(self, tmp_path: Path) -> None:
        pc = tmp_path / "__pycache__"
        pc.mkdir()
        (pc / "photo.jpg").write_bytes(b"\xff\xd8\xff" + b"\x00" * 10)
        assert list(walk_supported(tmp_path)) == []


class TestSha256Of:
    def test_returns_64_hex_chars(self, tmp_path: Path) -> None:
        f = tmp_path / "test.bin"
        f.write_bytes(b"hello world")
        digest = sha256_of(f)
        assert len(digest) == 64
        assert all(c in "0123456789abcdef" for c in digest)

    def test_stable_across_calls(self, tmp_path: Path) -> None:
        f = tmp_path / "stable.bin"
        f.write_bytes(b"some content")
        assert sha256_of(f) == sha256_of(f)

    def test_differs_for_different_content(self, tmp_path: Path) -> None:
        f1 = tmp_path / "a.bin"
        f2 = tmp_path / "b.bin"
        f1.write_bytes(b"content A")
        f2.write_bytes(b"content B")
        assert sha256_of(f1) != sha256_of(f2)

    def test_known_hash(self, tmp_path: Path) -> None:
        # sha256("") = e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855
        f = tmp_path / "empty.bin"
        f.write_bytes(b"")
        assert sha256_of(f) == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
