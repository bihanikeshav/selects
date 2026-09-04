"""Small shared helpers used across the backend."""

from datetime import datetime, timezone


def utcnow() -> datetime:
    """Return the current UTC time as a naive datetime.

    Equivalent to the deprecated ``datetime.utcnow()`` but explicit about
    the timezone it is derived from.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)
