"""The dashboard trend window is a calendar-month window, not a row count.

Every expectation below is written out by hand from the D-014 rule — the last
period with data in each of the 12 calendar months before the latest period's
month, then the latest period — never echoed from the function's own output.
"""

from __future__ import annotations

from calendar import monthrange
from dataclasses import dataclass
from datetime import date, timedelta

import pytest

from app.domain.reporting.period_windows import TREND_MONTHS, trailing_month_end_window


@dataclass(frozen=True)
class _Period:
    period_id: int
    period_end: date


def _month_ends(first: tuple[int, int], count: int) -> list[_Period]:
    """``count`` consecutive calendar month-ends starting at ``first`` (year, month)."""
    year, month = first
    periods: list[_Period] = []
    for index in range(count):
        ordinal = year * 12 + month - 1 + index
        y, m = divmod(ordinal, 12)
        periods.append(_Period(index, date(y, m + 1, monthrange(y, m + 1)[1])))
    return periods


def _business_days(start: date, end: date) -> list[_Period]:
    """Every Monday–Friday from ``start`` to ``end`` inclusive, ascending."""
    periods: list[_Period] = []
    current = start
    while current <= end:
        if current.weekday() < 5:
            periods.append(_Period(len(periods), current))
        current += timedelta(days=1)
    return periods


def test_trend_months_is_a_trailing_year() -> None:
    assert TREND_MONTHS == 12


def test_monthly_feeder_keeps_the_thirteen_point_sparkline() -> None:
    """Thirteen month-end rows select exactly as the old ``[-13:]`` slice did."""
    periods = _month_ends((2024, 1), 30)  # 2024-01 … 2026-06
    selected = trailing_month_end_window(periods)
    assert selected == periods[-13:]
    assert [item.period_end for item in selected] == [
        date(2025, 6, 30),
        date(2025, 7, 31),
        date(2025, 8, 31),
        date(2025, 9, 30),
        date(2025, 10, 31),
        date(2025, 11, 30),
        date(2025, 12, 31),
        date(2026, 1, 31),
        date(2026, 2, 28),
        date(2026, 3, 31),
        date(2026, 4, 30),
        date(2026, 5, 31),
        date(2026, 6, 30),
    ]


def test_daily_feeder_spans_twelve_months_not_thirteen_business_days() -> None:
    """A daily book yields one point per month (its last business day) plus today."""
    periods = _business_days(date(2025, 1, 1), date(2026, 9, 21))
    selected = trailing_month_end_window(periods)
    assert len(selected) == 13
    assert [item.period_end for item in selected] == [
        date(2025, 9, 30),  # Tuesday
        date(2025, 10, 31),  # Friday
        date(2025, 11, 28),  # Friday — the 29th/30th fall on the weekend
        date(2025, 12, 31),  # Wednesday
        date(2026, 1, 30),  # Friday — the 31st is a Saturday
        date(2026, 2, 27),  # Friday — the 28th is a Saturday
        date(2026, 3, 31),  # Tuesday
        date(2026, 4, 30),  # Thursday
        date(2026, 5, 29),  # Friday — the 30th/31st fall on the weekend
        date(2026, 6, 30),  # Tuesday
        date(2026, 7, 31),  # Friday
        date(2026, 8, 31),  # Monday
        date(2026, 9, 21),  # the latest period itself, mid-month
    ]
    # The old ``[-13:]`` slice would have shown the last thirteen business days
    # instead — 3 September onwards, a window of under three weeks.
    assert periods[-13].period_end == date(2026, 9, 3)
    assert selected != periods[-13:]


def test_sparse_feeder_leaves_missing_months_absent() -> None:
    """Months without a period are skipped — never interpolated, never zero."""
    periods = [
        _Period(0, date(2025, 8, 31)),
        _Period(1, date(2025, 11, 30)),
        _Period(2, date(2026, 2, 28)),
        _Period(3, date(2026, 3, 31)),
        _Period(4, date(2026, 6, 30)),
    ]
    selected = trailing_month_end_window(periods)
    # Window = 2025-06 … 2026-05 preceding the latest (2026-06); 2025-08, 2025-11,
    # 2026-02 and 2026-03 have data, the other eight months do not.
    assert [item.period_id for item in selected] == [0, 1, 2, 3, 4]


def test_month_outside_the_window_is_dropped_even_when_history_is_sparse() -> None:
    periods = [
        _Period(0, date(2025, 5, 31)),  # 13 months before 2026-06: outside
        _Period(1, date(2025, 6, 30)),  # exactly 12 months before: inside
        _Period(2, date(2026, 6, 30)),
    ]
    assert [item.period_id for item in trailing_month_end_window(periods)] == [1, 2]


def test_latest_period_is_kept_even_when_it_is_not_a_month_end() -> None:
    # Twelve month-ends 2025-09 … 2026-08, then a mid-month book on the 15th.
    periods = [*_month_ends((2025, 9), 12), _Period(99, date(2026, 9, 15))]
    selected = trailing_month_end_window(periods)
    assert selected[-1].period_id == 99
    assert selected[-1].period_end == date(2026, 9, 15)
    assert len(selected) == 13
    assert selected[:-1] == periods[:-1]


def test_only_the_last_period_of_each_month_is_selected() -> None:
    """Two mid-month books in one month contribute that month's LAST one only."""
    periods = [
        _Period(0, date(2026, 5, 8)),
        _Period(1, date(2026, 5, 22)),
        _Period(2, date(2026, 6, 5)),
        _Period(3, date(2026, 6, 19)),
        _Period(4, date(2026, 7, 3)),
    ]
    assert [item.period_id for item in trailing_month_end_window(periods)] == [1, 3, 4]


def test_earlier_periods_in_the_latest_month_are_not_extra_points() -> None:
    """The latest month is represented by the latest period alone."""
    periods = [
        _Period(0, date(2026, 8, 31)),
        _Period(1, date(2026, 9, 4)),
        _Period(2, date(2026, 9, 11)),
        _Period(3, date(2026, 9, 18)),
    ]
    assert [item.period_id for item in trailing_month_end_window(periods)] == [0, 3]


def test_fewer_than_twelve_months_of_history_returns_everything() -> None:
    periods = _month_ends((2026, 1), 5)  # 2026-01 … 2026-05
    assert trailing_month_end_window(periods) == periods


def test_single_period_is_its_own_window() -> None:
    only = _Period(0, date(2026, 3, 31))
    assert trailing_month_end_window([only]) == [only]


def test_empty_input_is_an_empty_window() -> None:
    assert trailing_month_end_window([]) == []


def test_input_order_does_not_matter_and_output_is_ascending() -> None:
    periods = _month_ends((2025, 1), 20)
    shuffled = [periods[7], periods[19], periods[3], periods[12], periods[18], periods[0]]
    selected = trailing_month_end_window(shuffled)
    assert [item.period_end for item in selected] == sorted(item.period_end for item in selected)
    # Latest = 2025-08-31 (index 19); window = 2024-08 … 2025-07 → indexes 7, 12, 18.
    assert [item.period_id for item in selected] == [7, 12, 18, 19]


def test_custom_horizon_widens_or_narrows_the_window() -> None:
    periods = _month_ends((2024, 1), 30)
    assert len(trailing_month_end_window(periods, months=24)) == 25
    assert len(trailing_month_end_window(periods, months=3)) == 4
    assert trailing_month_end_window(periods, months=0) == [periods[-1]]


def test_negative_horizon_is_refused() -> None:
    with pytest.raises(ValueError, match="months"):
        trailing_month_end_window(_month_ends((2026, 1), 2), months=-1)
