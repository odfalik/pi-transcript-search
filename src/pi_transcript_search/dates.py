"""Local-calendar and rolling-window filters for session timestamps."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone


def parse_timestamp(value: str | None) -> int | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp() * 1000)


def parse_day(value: str, today: date | None = None) -> date:
    current = today or datetime.now().astimezone().date()
    normalized = value.strip().lower()
    if normalized == "today":
        return current
    if normalized == "yesterday":
        return current - timedelta(days=1)
    return date.fromisoformat(normalized)


def day_bounds(value: str) -> tuple[int, int]:
    selected = parse_day(value)
    # Calling astimezone() on each naive midnight resolves the system timezone's
    # historical rule for that date instead of reusing today's fixed UTC offset.
    start = datetime.combine(selected, time.min).astimezone()
    end = datetime.combine(selected + timedelta(days=1), time.min).astimezone()
    return int(start.timestamp() * 1000), int(end.timestamp() * 1000)


def rolling_bounds(days: int) -> tuple[int, None]:
    if days < 1:
        raise ValueError("--days must be at least 1")
    now = datetime.now().astimezone()
    return int((now - timedelta(days=days)).timestamp() * 1000), None


def explicit_bounds(
    since: str | None, until: str | None
) -> tuple[int | None, int | None]:
    lower = day_bounds(since)[0] if since else None
    upper = day_bounds(until)[1] if until else None
    return lower, upper


def iso_from_millis(value: int | None) -> str | None:
    if value is None:
        return None
    return (
        datetime.fromtimestamp(value / 1000, tz=timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )
