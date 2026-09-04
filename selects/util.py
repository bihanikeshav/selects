"""Small shared helpers used across the backend."""

from datetime import datetime, timezone
from typing import Iterator, Sequence, TypeVar

T = TypeVar("T")

# SQLite's default SQLITE_MAX_VARIABLE_NUMBER is 999 on many system builds, so
# an ``IN (...)`` over a whole library's worth of ids raises "too many SQL
# variables". Every unbounded IN in the codebase goes through ``chunked``.
ID_CHUNK = 500


def chunked(items: Sequence[T], size: int = ID_CHUNK) -> Iterator[Sequence[T]]:
    """Yield *items* in slices small enough for a single SQL ``IN (...)``."""
    for start in range(0, len(items), size):
        yield items[start:start + size]


# The verdicts that mean "the user kept this photo". "silver" is the legacy
# second keep tier, still accepted for compatibility; "skip" is deliberately
# absent — a skipped photo is still undecided. Single source of truth for the
# routes, the exporter and the taste model.
KEEP_DECISIONS = ("keep", "silver")


def utcnow() -> datetime:
    """Return the current UTC time as a naive datetime.

    Equivalent to the deprecated ``datetime.utcnow()`` but explicit about
    the timezone it is derived from.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)
