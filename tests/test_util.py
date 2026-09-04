from datetime import datetime, timedelta, timezone

from selects.util import utcnow


def test_utcnow_is_naive_and_close_to_now():
    result = utcnow()
    assert result.tzinfo is None
    expected = datetime.now(timezone.utc).replace(tzinfo=None)
    assert abs(expected - result) < timedelta(seconds=5)
