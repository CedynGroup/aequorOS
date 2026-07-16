"""Pull schedule computation (market_data_adapter.md §9.2 step 6).

Pure functions only: given a frequency, the last pull time, and the
institution's timezone, compute when the next pull is due. Job-queue
integration (enqueueing, retries, worker claims) lives with the rest of the
background-job machinery, not here.

Schedule defaults per §9.2:
- END_OF_DAY: 17:00 in the institution's timezone, business days.
- HOURLY: every hour during business hours (08:00-17:00 local, weekdays).
- WEEKLY: Monday at start of business (08:00 local).
- MONTHLY: the 1st at start of business (08:00 local).
- ON_DEMAND: never scheduled (returns None).
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.adapters.market_data.scope_taxonomy import DataScope, PullFrequency

BUSINESS_START = time(8, 0)
END_OF_DAY_TIME = time(17, 0)

_SATURDAY = 5  # date.weekday() value


def _next_weekday(day: date) -> date:
    while day.weekday() >= _SATURDAY:
        day += timedelta(days=1)
    return day


def _at(day: date, moment: time, tz: ZoneInfo) -> datetime:
    return datetime.combine(day, moment, tzinfo=tz)


def _next_end_of_day(after: datetime, tz: ZoneInfo) -> datetime:
    local = after.astimezone(tz)
    candidate = _at(_next_weekday(local.date()), END_OF_DAY_TIME, tz)
    if candidate <= local:
        candidate = _at(_next_weekday(candidate.date() + timedelta(days=1)), END_OF_DAY_TIME, tz)
    return candidate


def _next_business_hour(after: datetime, tz: ZoneInfo) -> datetime:
    candidate = after.astimezone(tz) + timedelta(hours=1)
    if candidate.time() < BUSINESS_START:
        candidate = _at(candidate.date(), BUSINESS_START, tz)
    elif candidate.time() > END_OF_DAY_TIME:
        candidate = _at(candidate.date() + timedelta(days=1), BUSINESS_START, tz)
    if candidate.weekday() >= _SATURDAY:
        candidate = _at(_next_weekday(candidate.date()), BUSINESS_START, tz)
    return candidate


def _next_monday(after: datetime, tz: ZoneInfo) -> datetime:
    local = after.astimezone(tz)
    days_ahead = (7 - local.weekday()) % 7
    candidate = _at(local.date() + timedelta(days=days_ahead), BUSINESS_START, tz)
    if candidate <= local:
        candidate = _at(candidate.date() + timedelta(days=7), BUSINESS_START, tz)
    return candidate


def _next_month_first(after: datetime, tz: ZoneInfo) -> datetime:
    local = after.astimezone(tz)
    year, month = local.year, local.month
    candidate = _at(date(year, month, 1), BUSINESS_START, tz)
    if candidate <= local:
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
        candidate = _at(date(year, month, 1), BUSINESS_START, tz)
    return candidate


def next_pull_due(
    frequency: PullFrequency,
    last_pull_at: datetime | None,
    institution_tz: str,
    now: datetime,
) -> datetime | None:
    """When the next pull for this frequency is due.

    Returns ``None`` for ON_DEMAND (never scheduled). A scope that has never
    been pulled (``last_pull_at is None``) is due immediately (returns
    ``now``). Otherwise returns the first schedule slot strictly after
    ``last_pull_at`` — which may be in the past relative to ``now``, meaning
    the pull is overdue.
    """
    if frequency is PullFrequency.ON_DEMAND:
        return None
    if last_pull_at is None:
        return now
    tz = ZoneInfo(institution_tz)
    if frequency is PullFrequency.END_OF_DAY:
        return _next_end_of_day(last_pull_at, tz)
    if frequency is PullFrequency.HOURLY:
        return _next_business_hour(last_pull_at, tz)
    if frequency is PullFrequency.WEEKLY:
        return _next_monday(last_pull_at, tz)
    return _next_month_first(last_pull_at, tz)


def due_scopes(
    connection_schedule: Mapping[DataScope | str, PullFrequency | str],
    last_pull_map: Mapping[DataScope | str, datetime | None],
    now: datetime,
    institution_tz: str = "UTC",
) -> list[DataScope]:
    """The scopes whose next scheduled pull time has arrived.

    ``connection_schedule`` is the per-connection scope-to-frequency mapping
    (as stored on ``MarketDataConnection.schedule``, so string keys/values
    from JSON are accepted alongside enums). ``last_pull_map`` records each
    scope's last successful pull; scopes absent from it are due immediately.
    ON_DEMAND scopes are never returned.
    """
    due: list[DataScope] = []
    normalized_last: dict[DataScope, datetime | None] = {
        (DataScope[key] if isinstance(key, str) else key): value
        for key, value in last_pull_map.items()
    }
    for raw_scope, raw_frequency in connection_schedule.items():
        scope = DataScope[raw_scope] if isinstance(raw_scope, str) else raw_scope
        frequency = (
            PullFrequency[raw_frequency] if isinstance(raw_frequency, str) else raw_frequency
        )
        next_due = next_pull_due(frequency, normalized_last.get(scope), institution_tz, now)
        if next_due is not None and next_due <= now:
            due.append(scope)
    return sorted(due, key=lambda scope: scope.value)
