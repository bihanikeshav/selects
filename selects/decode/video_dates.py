"""Video creation dates, including MP4/MOV movie headers without ffprobe."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import BinaryIO, Iterator


def parse_creation_time(value: str) -> datetime | None:
    """Normalize an ISO container timestamp to naive UTC for SQLite."""
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
        return parsed if parsed > datetime(1904, 1, 1) else None
    except (ValueError, AttributeError, OverflowError):
        return None


def _boxes(stream: BinaryIO, start: int, end: int) -> Iterator[tuple[bytes, int, int]]:
    """Walk box headers only, seeking past media payloads rather than loading them."""
    position = start
    while position + 8 <= end:
        stream.seek(position)
        header = stream.read(8)
        if len(header) != 8:
            raise ValueError("Truncated video box header")
        size = int.from_bytes(header[:4], "big")
        kind = header[4:]
        header_size = 8
        if size == 1:
            extended = stream.read(8)
            if len(extended) != 8:
                raise ValueError("Truncated extended video box header")
            size = int.from_bytes(extended, "big")
            header_size = 16
        elif size == 0:
            size = end - position
        if size < header_size or position + size > end:
            raise ValueError("Invalid video box size")
        yield kind, position + header_size, position + size
        position += size


def read_mp4_creation_time(path: Path) -> datetime | None:
    """Read the UTC mvhd creation time (1904 epoch), supporting versions 0 and 1."""
    if path.suffix.lower() not in {".mp4", ".mov", ".m4v", ".3gp", ".3g2"}:
        raise ValueError("Container dates require ffprobe for this video format")
    with path.open("rb") as stream:
        stream.seek(0, 2)
        size = stream.tell()
        for kind, start, end in _boxes(stream, 0, size):
            if kind != b"moov":
                continue
            for child, payload, limit in _boxes(stream, start, end):
                if child != b"mvhd":
                    continue
                stream.seek(payload)
                version_flags = stream.read(4)
                if len(version_flags) != 4 or version_flags[0] not in (0, 1):
                    raise ValueError("Unsupported movie header version")
                length = 8 if version_flags[0] == 1 else 4
                if payload + 4 + length > limit:
                    raise ValueError("Truncated movie creation time")
                raw = stream.read(length)
                if len(raw) != length:
                    raise ValueError("Truncated movie creation time")
                seconds = int.from_bytes(raw, "big")
                if seconds == 0:
                    return None
                try:
                    return datetime(1904, 1, 1) + timedelta(seconds=seconds)
                except OverflowError:
                    raise ValueError("Invalid movie creation time") from None
    return None
