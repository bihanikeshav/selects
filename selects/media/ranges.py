"""Framework-neutral byte range parsing and bounded file iteration."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Iterator


class RangeNotSatisfiable(ValueError):
    """The requested range cannot be represented for the resource size."""


@dataclass(frozen=True)
class ByteRange:
    """An inclusive byte range, suitable for a ``Content-Range`` header."""

    start: int
    end: int
    size: int

    def __post_init__(self) -> None:
        if self.start < 0 or self.end < self.start or self.size < self.end + 1:
            raise ValueError("invalid inclusive byte range")

    @property
    def length(self) -> int:
        return self.end - self.start + 1

    @property
    def content_range(self) -> str:
        return f"bytes {self.start}-{self.end}/{self.size}"


def parse_range_header(value: str | None, size: int) -> ByteRange | None:
    """Parse a single RFC 9110 ``Range: bytes=...`` value.

    Multiple ranges are intentionally rejected: a single-range response is
    useful for video seeking and avoids constructing multipart bodies in a
    low-level helper.  ``None`` means no Range header was supplied.
    """

    if value is None or not value.strip():
        return None
    if size < 0:
        raise ValueError("resource size must be non-negative")
    raw = value.strip()
    if not raw.lower().startswith("bytes="):
        raise RangeNotSatisfiable("only byte ranges are supported")
    spec = raw[6:].strip()
    if not spec or "," in spec:
        raise RangeNotSatisfiable("exactly one byte range is supported")
    if "-" not in spec:
        raise RangeNotSatisfiable("malformed byte range")
    start_text, end_text = (part.strip() for part in spec.split("-", 1))
    if size == 0:
        raise RangeNotSatisfiable("empty resources have no satisfiable range")
    try:
        if not start_text:
            suffix = int(end_text)
            if suffix <= 0:
                raise RangeNotSatisfiable("suffix length must be positive")
            start, end = max(0, size - suffix), size - 1
        else:
            start = int(start_text)
            if start < 0:
                raise RangeNotSatisfiable("range start must be non-negative")
            end = int(end_text) if end_text else size - 1
            if end < start:
                raise RangeNotSatisfiable("range end precedes range start")
            start, end = start, min(end, size - 1)
    except ValueError as exc:
        raise RangeNotSatisfiable("malformed byte range") from exc
    if start >= size:
        raise RangeNotSatisfiable("range starts after resource end")
    return ByteRange(start, end, size)


def iter_file_range(
    path: str | Path,
    byte_range: ByteRange | None = None,
    *,
    chunk_size: int = 1024 * 1024,
) -> Iterator[bytes]:
    """Yield a file or bounded file range without loading it into memory."""

    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    source = Path(path)
    size = source.stat().st_size
    selected = byte_range or ByteRange(0, size - 1, size) if size else None
    if selected is None:
        return
    if selected.size != size or selected.end >= size:
        raise ValueError("byte range does not match current file size")
    with source.open("rb") as handle:
        yield from iter_stream_range(handle, selected, chunk_size=chunk_size)


def iter_stream_range(
    stream: BinaryIO,
    byte_range: ByteRange,
    *,
    chunk_size: int = 1024 * 1024,
) -> Iterator[bytes]:
    """Yield exactly the inclusive range from a seekable binary stream."""

    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    stream.seek(byte_range.start)
    remaining = byte_range.length
    while remaining:
        chunk = stream.read(min(chunk_size, remaining))
        if not chunk:
            raise OSError("stream ended before requested byte range")
        remaining -= len(chunk)
        yield chunk


# Short aliases make the helper convenient to import in route modules while
# keeping the descriptive names above as the canonical API.
parse_byte_range = parse_range_header
iter_range = iter_stream_range
