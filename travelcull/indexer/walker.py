"""File walker and classification utilities for travelcull."""
from __future__ import annotations

import hashlib
from enum import Enum
from pathlib import Path
from typing import Iterator, Optional


class FileKind(str, Enum):
    """Supported media file kinds."""

    JPEG = "JPEG"
    HEIC = "HEIC"
    RAW = "RAW"
    VIDEO = "VIDEO"


# Extension sets (all lowercase for case-insensitive comparison)
_JPEG_EXTS: frozenset[str] = frozenset({".jpg", ".jpeg"})
_HEIC_EXTS: frozenset[str] = frozenset({".heic", ".heif"})
_RAW_EXTS: frozenset[str] = frozenset({
    ".dng", ".cr2", ".cr3", ".nef", ".arw", ".raf", ".orf", ".rw2", ".pef"
})
_VIDEO_EXTS: frozenset[str] = frozenset({".mp4", ".mov", ".m4v", ".mkv"})

# Directory names to skip entirely
_SKIP_DIRS: frozenset[str] = frozenset({".travelcull", ".git", "__pycache__", "node_modules"})


def classify(path: Path) -> Optional[FileKind]:
    """Return the FileKind for *path* based on its extension, or None if unsupported."""
    ext = path.suffix.lower()
    if ext in _JPEG_EXTS:
        return FileKind.JPEG
    if ext in _HEIC_EXTS:
        return FileKind.HEIC
    if ext in _RAW_EXTS:
        return FileKind.RAW
    if ext in _VIDEO_EXTS:
        return FileKind.VIDEO
    return None


def walk_supported(root: Path) -> Iterator[tuple[Path, FileKind]]:
    """Yield (path, kind) for all supported media files under *root*.

    Skips directories named in _SKIP_DIRS at any depth.
    """
    for item in root.iterdir():
        if item.is_dir():
            if item.name in _SKIP_DIRS:
                continue
            yield from walk_supported(item)
        elif item.is_file():
            kind = classify(item)
            if kind is not None:
                yield item, kind


def classify_paths(paths: "Iterator[Path] | list[Path]") -> Iterator[tuple[Path, FileKind]]:
    """Yield (path, kind) for an explicit list of candidate paths.

    Unlike :func:`walk_supported`, this does not walk the filesystem tree — it
    simply classifies (and filters to existing files of) a caller-supplied
    subset. Used for incremental indexing where the caller (e.g. the watch
    folder poller) already knows which paths are new.
    """
    for item in paths:
        item = Path(item)
        if not item.is_file():
            continue
        kind = classify(item)
        if kind is not None:
            yield item, kind


def sha256_of(path: Path) -> str:
    """Return the hex-encoded SHA-256 digest of a file's contents."""
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()
