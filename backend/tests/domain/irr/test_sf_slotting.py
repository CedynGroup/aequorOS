"""Where a cash flow lands on the governed bucket ladder.

The framework's own table is upper-INCLUSIVE: a flow falling exactly on a
bucket's upper bound belongs to that bucket, not the next one. Getting that
wrong moves a year-end coupon out of the one-year bucket, which is precisely
the bucket the earnings measure is built on. The boundaries are therefore
tested one day either side, on real calendar dates, with month-end clamping,
because "three months after 31 December" is a calendar question and not
a 90-day question.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.domain.irr.standardised_cash_flows import add_months, add_tenor, slot, year_fraction
from app.domain.irr.standardised_params import Tenor, parse_tenor
from tests.domain.irr.test_sf_fixtures import sf_parameters

AS_OF = date(2026, 12, 31)

#: (flow date, expected bucket key). Each pair sits on a bound or one day past.
BOUNDARIES: tuple[tuple[date, str], ...] = (
    (date(2020, 1, 1), "b01"),      # long past due
    (date(2026, 12, 30), "b01"),    # yesterday
    (date(2026, 12, 31), "b01"),    # today
    (date(2027, 1, 1), "b01"),      # exactly one day: still overnight
    (date(2027, 1, 2), "b02"),
    (date(2027, 1, 31), "b02"),     # one month, clamped to month end
    (date(2027, 2, 1), "b03"),
    (date(2027, 3, 31), "b03"),     # three months
    (date(2027, 4, 1), "b04"),
    (date(2027, 6, 30), "b04"),     # six months
    (date(2027, 7, 1), "b05"),
    (date(2027, 9, 30), "b05"),     # nine months
    (date(2027, 10, 1), "b06"),
    (date(2027, 12, 31), "b06"),    # twelve months
    (date(2028, 1, 1), "b07"),
    (date(2028, 6, 30), "b07"),     # eighteen months
    (date(2028, 7, 1), "b08"),
    (date(2028, 12, 31), "b08"),    # two years
    (date(2029, 1, 1), "b09"),
    (date(2029, 12, 31), "b09"),    # three years
    (date(2031, 12, 31), "b11"),    # five years
    (date(2032, 1, 1), "b12"),
    (date(2041, 12, 31), "b17"),    # fifteen years
    (date(2046, 12, 31), "b18"),    # twenty years
    (date(2047, 1, 1), "b19"),      # past the last bound: the open bucket
    (date(2099, 1, 1), "b19"),
)


@pytest.mark.parametrize(("when", "expected"), BOUNDARIES)
def test_a_flow_lands_in_the_bucket_whose_upper_bound_it_reaches(
    when: date, expected: str
) -> None:
    params = sf_parameters()

    index = slot(AS_OF, when, params.buckets)

    assert params.bucket_keys[index] == expected


def test_every_bucket_bound_slots_to_its_own_bucket() -> None:
    """Exhaustive, so a future bucket edit cannot quietly shift the ladder."""
    params = sf_parameters()

    for index, bucket in enumerate(params.buckets):
        if bucket.upper is None:
            continue
        assert slot(AS_OF, add_tenor(AS_OF, bucket.upper), params.buckets) == index
        past_bound = add_tenor(AS_OF, bucket.upper) + (date(2027, 1, 2) - date(2027, 1, 1))
        assert slot(AS_OF, past_bound, params.buckets) == index + 1


def test_month_arithmetic_clamps_to_the_end_of_the_month() -> None:
    assert add_months(date(2026, 1, 31), 1) == date(2026, 2, 28)
    assert add_months(date(2024, 1, 31), 1) == date(2024, 2, 29)
    assert add_months(date(2026, 12, 31), 2) == date(2027, 2, 28)
    assert add_months(date(2026, 3, 31), -1) == date(2026, 2, 28)
    assert add_months(date(2026, 12, 31), -12) == date(2025, 12, 31)


def test_a_year_is_twelve_months() -> None:
    assert add_tenor(AS_OF, Tenor(3, "Y")) == add_tenor(AS_OF, Tenor(36, "M"))
    assert add_tenor(date(2024, 2, 29), Tenor(1, "Y")) == date(2025, 2, 28)


def test_days_are_days() -> None:
    assert add_tenor(AS_OF, Tenor(1, "D")) == date(2027, 1, 1)
    assert add_tenor(AS_OF, Tenor(30, "D")) == date(2027, 1, 30)


def test_slotting_by_years_is_also_upper_inclusive() -> None:
    """Deposit core maturities arrive in years, not as dates."""
    params = sf_parameters()

    assert params.bucket_keys[params.index_for_years(Decimal("5"))] == "b11"
    assert params.bucket_keys[params.index_for_years(Decimal("5.000001"))] == "b12"
    assert params.bucket_keys[params.index_for_years(Decimal("0"))] == "b01"
    assert params.bucket_keys[params.index_for_years(Decimal("40"))] == "b19"


def test_accrual_is_act_365_and_never_negative() -> None:
    assert year_fraction(AS_OF, date(2027, 3, 31)) == Decimal(90) / Decimal(365)
    assert year_fraction(AS_OF, date(2027, 6, 30)) == Decimal(181) / Decimal(365)
    assert year_fraction(AS_OF, AS_OF) == Decimal(0)
    assert year_fraction(AS_OF, date(2026, 1, 1)) == Decimal(0)


def test_a_tenor_reads_the_way_the_table_prints_it() -> None:
    assert parse_tenor("1D", param_code="x") == Tenor(1, "D")
    assert parse_tenor("18M", param_code="x") == Tenor(18, "M")
    assert parse_tenor("20y", param_code="x") == Tenor(20, "Y")


def test_bucket_widths_close_the_open_ended_bucket_on_its_midpoint() -> None:
    """The last bucket has no upper bound, so its width comes from its midpoint."""
    params = sf_parameters()

    widths = params.widths_years

    assert widths[0] == Decimal(1) / Decimal(365)
    assert widths[10] == Decimal(1)
    assert widths[-1] == Decimal(10)
    assert sum(widths[:-1], Decimal(0)) == Decimal(20)
