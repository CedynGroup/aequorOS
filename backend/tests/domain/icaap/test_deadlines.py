"""Filing dates: month arithmetic with a month-end clamp, and no invented values."""

from __future__ import annotations

from datetime import date

import pytest

from app.domain.icaap.frameworks.schema import Citation, DeadlineSpec

_CITE = (Citation(doc="DOC", ref="1"),)


def _spec(month_day: str = "12-31") -> DeadlineSpec:
    return DeadlineSpec(
        as_of="fy_end",
        fy_end_month_day=month_day,
        months_after_fye_param="icaap_submission_months",
        citations=_CITE,
    )


@pytest.mark.parametrize(
    ("fiscal_year", "expected"),
    [(2024, date(2024, 12, 31)), (2026, date(2026, 12, 31))],
)
def test_the_year_end_comes_from_the_frameworks_own_convention(
    fiscal_year: int, expected: date
) -> None:
    assert _spec().fy_end(fiscal_year) == expected


def test_a_february_year_end_clamps_to_the_real_last_day() -> None:
    assert _spec("02-30").fy_end(2025) == date(2025, 2, 28)
    assert _spec("02-30").fy_end(2024) == date(2024, 2, 29)


@pytest.mark.parametrize(
    ("as_of", "months", "expected"),
    [
        # Ghana: three months after a 31 December year end is 31 March.
        (date(2026, 12, 31), 3, date(2027, 3, 31)),
        # Four months lands on 30 April, because April has no 31st. A regulator
        # that writes "not later than 30 April" is expressed this way.
        (date(2026, 12, 31), 4, date(2027, 4, 30)),
        (date(2024, 11, 30), 3, date(2025, 2, 28)),
        (date(2023, 11, 30), 3, date(2024, 2, 29)),
        (date(2026, 12, 31), 12, date(2027, 12, 31)),
        (date(2026, 12, 31), 0, date(2026, 12, 31)),
    ],
)
def test_the_due_date_adds_months_and_clamps_to_month_end(
    as_of: date, months: int, expected: date
) -> None:
    assert _spec().due_date(as_of, months) == expected


def test_a_negative_filing_period_is_refused() -> None:
    """A deadline before the position it reports on is a configuration error."""
    with pytest.raises(ValueError, match="negative"):
        _spec().due_date(date(2026, 12, 31), -1)
