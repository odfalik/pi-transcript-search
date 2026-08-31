from __future__ import annotations

import os
import time
from contextlib import contextmanager
from datetime import datetime, timezone

import pytest

from pi_transcript_search.dates import day_bounds


@contextmanager
def system_timezone(name: str):
    if not hasattr(time, "tzset"):
        pytest.skip("tzset is unavailable")
    previous = os.environ.get("TZ")
    os.environ["TZ"] = name
    time.tzset()
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = previous
        time.tzset()


def utc_hour(milliseconds: int) -> int:
    return datetime.fromtimestamp(milliseconds / 1000, tz=timezone.utc).hour


def test_historical_days_use_their_own_dst_offset() -> None:
    with system_timezone("America/Los_Angeles"):
        winter, _ = day_bounds("2026-01-15")
        summer, _ = day_bounds("2026-07-15")
    assert utc_hour(winter) == 8
    assert utc_hour(summer) == 7


def test_dst_transition_days_have_real_local_duration() -> None:
    with system_timezone("America/Los_Angeles"):
        spring_start, spring_end = day_bounds("2026-03-08")
        fall_start, fall_end = day_bounds("2026-11-01")
    assert spring_end - spring_start == 23 * 60 * 60 * 1000
    assert fall_end - fall_start == 25 * 60 * 60 * 1000
