"""Injectable clock. Services read time from a ``Clock``, never from ``datetime.now``."""

from __future__ import annotations

import threading
from datetime import UTC, date, datetime, timedelta
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class SettableClock:
    """A clock tests and the seed set explicitly. ``advance`` moves it forward."""

    def __init__(self, start: datetime) -> None:
        self._lock = threading.Lock()
        self._now = start.astimezone(UTC)

    def now(self) -> datetime:
        with self._lock:
            return self._now

    def set(self, moment: datetime) -> None:
        with self._lock:
            self._now = moment.astimezone(UTC)

    def advance(self, **delta: float) -> datetime:
        with self._lock:
            self._now = self._now + timedelta(**delta)
            return self._now


def utc(dt: datetime) -> datetime:
    """Return ``dt`` as an aware UTC datetime (naive values are taken to be UTC)."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def iso(dt: datetime) -> str:
    """RFC 3339 UTC with milliseconds, e.g. ``2026-11-21T15:57:12.418Z``."""
    value = utc(dt)
    return value.strftime("%Y-%m-%dT%H:%M:%S.") + f"{value.microsecond // 1000:03d}Z"


def iso_or_none(dt: datetime | None) -> str | None:
    return None if dt is None else iso(dt)


def parse_iso(value: str) -> datetime:
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        raise ValueError("timestamp must carry a UTC offset")
    return parsed.astimezone(UTC)


def parse_date(value: str) -> date:
    return date.fromisoformat(value)


def is_business_day(day: date) -> bool:
    return day.weekday() < 5


def add_business_days(day: date, count: int) -> date:
    current = day
    remaining = count
    while remaining > 0:
        current = current + timedelta(days=1)
        if is_business_day(current):
            remaining -= 1
    return current
