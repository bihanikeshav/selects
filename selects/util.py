"""Small shared helpers used across the backend."""

from datetime import datetime, timezone

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
