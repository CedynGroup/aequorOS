"""FC-0 calendar engine: adjustment, tenor arithmetic, schedules, holiday sets.

The golden reproduction of the Eikon adjusted dates lives in
``test_eikon_reproduction``; these are the unit-level contracts of the engine.
"""

from __future__ import annotations

from datetime import date

import pytest

from app.domain.curves.calendars import (
    GHANA,
    KENYA,
    NIGERIA,
    SOUTH_AFRICA,
    USA,
    BusinessDayConvention,
    Calendar,
    CalendarError,
    add_calendar_months,
    calendar_from_holidays,
    easter_sunday,
    ghana_holidays,
    kenya_holidays,
    next_imm_date,
    nigeria_holidays,
    south_africa_holidays,
    tenor_to_months,
    third_wednesday,
    us_federal_holidays,
)
from app.domain.curves.conventions import DayCount, simple_rate_from_discounts, year_fraction

F = BusinessDayConvention.FOLLOWING
MF = BusinessDayConvention.MODIFIED_FOLLOWING
P = BusinessDayConvention.PRECEDING
MP = BusinessDayConvention.MODIFIED_PRECEDING
U = BusinessDayConvention.UNADJUSTED


def test_is_business_day() -> None:
    assert USA.is_business_day(date(2024, 1, 2))  # Tuesday
    assert not USA.is_business_day(date(2024, 1, 6))  # Saturday
    assert not USA.is_business_day(date(2024, 1, 7))  # Sunday
    assert not USA.is_business_day(date(2024, 1, 1))  # New Year holiday
    assert not USA.is_business_day(date(2024, 1, 15))  # MLK day


def test_adjust_following_and_modified_following() -> None:
    # 2024-03-31 is Easter Sunday; Following crosses into April, ModFollowing rolls back.
    assert USA.adjust(date(2024, 3, 31), F) == date(2024, 4, 1)
    assert USA.adjust(date(2024, 3, 31), MF) == date(2024, 3, 29)  # Good Fri is a US business day
    assert USA.adjust(date(2024, 3, 29), MF) == date(2024, 3, 29)  # already a business day


def test_adjust_preceding_and_modified_preceding() -> None:
    # 2024-06-01 is Saturday; Preceding -> Fri May 31, ModPreceding rolls forward (crosses month).
    assert USA.adjust(date(2024, 6, 1), P) == date(2024, 5, 31)
    assert USA.adjust(date(2024, 6, 1), MP) == date(2024, 6, 3)
    assert USA.adjust(date(2024, 6, 1), U) == date(2024, 6, 1)


def test_advance_business_days() -> None:
    # From Fri 2023-12-29: Sat/Sun, Mon 01-01 holiday -> +1bd = 01-02, +2bd = 01-03.
    assert USA.advance(date(2023, 12, 29), 1) == date(2024, 1, 2)
    assert USA.advance(date(2023, 12, 29), 2) == date(2024, 1, 3)
    assert USA.advance(date(2024, 1, 3), -2) == date(2023, 12, 29)
    assert USA.advance(date(2024, 1, 3), 0) == date(2024, 1, 3)


def test_spot_date_t_plus_two() -> None:
    assert USA.spot_date(date(2023, 12, 29)) == date(2024, 1, 3)
    assert USA.spot_date(date(2024, 3, 18)) == date(2024, 3, 20)
    with pytest.raises(CalendarError):
        USA.spot_date(date(2024, 1, 1), lag=-1)


def test_add_tenor_variants() -> None:
    spot = date(2024, 3, 20)
    assert USA.add_tenor(date(2024, 3, 18), "ON") == date(2024, 3, 19)
    assert USA.add_tenor(date(2024, 3, 18), "TN") == date(2024, 3, 20)
    assert USA.add_tenor(spot, "1W") == date(2024, 3, 27)
    assert USA.add_tenor(spot, "3M") == date(2024, 6, 20)
    assert USA.add_tenor(spot, "1Y") == date(2025, 3, 20)
    assert USA.add_tenor(spot, "1Y3M") == date(2025, 6, 20)
    assert USA.add_tenor(spot, "3Y") == date(2027, 3, 22)  # 2027-03-20 is a Saturday -> MF


def test_add_tenor_rejects_mixed_and_empty() -> None:
    with pytest.raises(CalendarError):
        USA.add_tenor(date(2024, 3, 20), "1M2W")
    with pytest.raises(CalendarError):
        USA.add_tenor(date(2024, 3, 20), "")


def test_add_calendar_months_clamp_and_eom() -> None:
    assert add_calendar_months(date(2024, 1, 31), 1) == date(2024, 2, 29)  # clamp to leap Feb
    assert add_calendar_months(date(2023, 2, 28), 12, end_of_month=True) == date(2024, 2, 29)
    assert add_calendar_months(date(2024, 2, 29), 1, end_of_month=True) == date(2024, 3, 31)


def test_generate_schedule() -> None:
    boundaries = USA.generate_schedule(
        date(2023, 12, 31), periods=3, interval_months=3, convention=MF, adjust_start=False
    )
    assert boundaries[0] == date(2023, 12, 31)  # raw anchor kept
    assert boundaries[1:] == (date(2024, 3, 29), date(2024, 6, 28), date(2024, 9, 30))


def test_tenor_to_months() -> None:
    assert tenor_to_months("5Y") == 60
    assert tenor_to_months("1Y6M") == 18
    assert tenor_to_months("9M") == 9
    with pytest.raises(CalendarError):
        tenor_to_months("2W")


def test_easter_sunday_known_values() -> None:
    assert easter_sunday(2024) == date(2024, 3, 31)
    assert easter_sunday(2023) == date(2023, 4, 9)
    assert easter_sunday(2025) == date(2025, 4, 20)


def test_us_federal_holidays_2024() -> None:
    holidays = us_federal_holidays(2024)
    for day in (
        date(2024, 1, 1),  # New Year
        date(2024, 1, 15),  # MLK
        date(2024, 2, 19),  # Presidents
        date(2024, 5, 27),  # Memorial
        date(2024, 6, 19),  # Juneteenth
        date(2024, 7, 4),  # Independence
        date(2024, 9, 2),  # Labor
        date(2024, 11, 28),  # Thanksgiving
        date(2024, 12, 25),  # Christmas
    ):
        assert day in holidays


def test_us_juneteenth_only_from_2021() -> None:
    assert date(2020, 6, 19) not in us_federal_holidays(2020)
    # 2021-06-19 is a Saturday; Sunday-only observance keeps it on the Saturday (no Fri roll-back).
    assert date(2021, 6, 19) in us_federal_holidays(2021)
    assert date(2021, 6, 18) not in us_federal_holidays(2021)


def test_us_sunday_only_observance() -> None:
    # 2023-01-01 is a Sunday -> observed Monday 2023-01-02.
    assert date(2023, 1, 2) in USA.holidays
    # 2028-01-01 is a Saturday -> NOT rolled back to Friday 2027-12-31 (the Eikon-matching rule).
    assert date(2027, 12, 31) not in USA.holidays
    assert USA.is_business_day(date(2027, 12, 31))


def test_ghana_holidays_2024() -> None:
    holidays = ghana_holidays(2024)
    assert date(2024, 3, 6) in holidays  # Independence
    assert date(2024, 3, 29) in holidays  # Good Friday
    assert date(2024, 4, 1) in holidays  # Easter Monday
    assert date(2024, 5, 1) in holidays  # May Day
    assert date(2024, 12, 6) in holidays  # Farmers' Day (1st Friday Dec)
    assert not GHANA.is_business_day(date(2024, 3, 29))
    assert GHANA.is_business_day(date(2024, 3, 28))


def test_calendar_from_explicit_holidays() -> None:
    cal = calendar_from_holidays("CUSTOM", [date(2025, 7, 4)])
    assert not cal.is_business_day(date(2025, 7, 4))
    assert cal.is_business_day(date(2025, 7, 3))


def test_calendar_is_frozen() -> None:
    with pytest.raises(Exception):  # noqa: B017 - frozen dataclass raises FrozenInstanceError
        USA.name = "mutated"  # type: ignore[misc]


def test_year_fraction_new_day_counts() -> None:
    # 30/360: a full year is exactly 1.0; a US-rule end-of-month case.
    assert year_fraction(date(2024, 1, 1), date(2025, 1, 1), DayCount.THIRTY_360) == pytest.approx(
        1.0
    )
    thirty = year_fraction(date(2024, 1, 30), date(2024, 2, 28), DayCount.THIRTY_360)
    assert thirty == pytest.approx(28 / 360)
    # ACT/ACT ISDA across a leap-year boundary splits by each year's length:
    # 2023-12-01..2024-01-01 is 31 days in 2023 (/365), 2024-01-01..2024-02-01 is 31 in 2024 (/366).
    frac = year_fraction(date(2023, 12, 1), date(2024, 2, 1), DayCount.ACT_ACT)
    assert frac == pytest.approx(31 / 365 + 31 / 366)


def test_simple_rate_from_discounts_matches_money_market() -> None:
    # DF=1/(1+r*tau) round-trips to r on the same basis.
    tau = year_fraction(date(2024, 1, 3), date(2024, 4, 3), DayCount.ACT_360)
    r = 0.0535
    df_end = 1.0 / (1.0 + r * tau)
    start, end = date(2024, 1, 3), date(2024, 4, 3)
    got = simple_rate_from_discounts(1.0, df_end, start, end, DayCount.ACT_360)
    assert got == pytest.approx(r)


def test_named_calendars_span_far_future() -> None:
    # The 50Y pillar lands in 2074; a static 2030-limited list would fail here.
    assert isinstance(USA, Calendar)
    assert date(2074, 7, 4) in USA.holidays
    assert date(2033, 6, 20) in USA.holidays  # Juneteenth 2033-06-19 Sun -> Mon


# --------------------------------------------------------------------------- #
# IMM roll (FC-G3)                                                             #
# --------------------------------------------------------------------------- #


def test_third_wednesday() -> None:
    assert third_wednesday(2024, 3) == date(2024, 3, 20)  # March 2024 IMM
    assert third_wednesday(2024, 6) == date(2024, 6, 19)
    assert third_wednesday(2023, 12) == date(2023, 12, 20)


def test_next_imm_date_rolls_to_quarterly_third_wednesday() -> None:
    # From the fixture as-of, the next quarterly IMM is the March-2024 third Wednesday.
    assert next_imm_date(date(2023, 12, 29)) == date(2024, 3, 20)
    # On the IMM date itself the same date is returned (on/after semantics).
    assert next_imm_date(date(2024, 3, 20)) == date(2024, 3, 20)
    # The day after rolls to the June IMM.
    assert next_imm_date(date(2024, 3, 21)) == date(2024, 6, 19)


def test_calendar_imm_date_adjusts() -> None:
    # A third Wednesday is almost always a business day, so UNADJUSTED is the default.
    assert USA.imm_date(date(2023, 12, 29)) == date(2024, 3, 20)
    # The pillar-tab base is two business days before the March IMM.
    assert USA.advance(USA.imm_date(date(2023, 12, 29)), -2) == date(2024, 3, 18)


# --------------------------------------------------------------------------- #
# FC-6c calendars — golden business-day adjustments                           #
# --------------------------------------------------------------------------- #


def test_nigeria_holidays_2024() -> None:
    holidays = nigeria_holidays(2024)
    assert date(2024, 10, 1) in holidays  # Independence Day (Tue)
    assert date(2024, 6, 12) in holidays  # Democracy Day (from 2019)
    assert date(2024, 5, 1) in holidays  # Workers' Day
    assert not NIGERIA.is_business_day(date(2024, 10, 1))
    # A settlement landing on Independence Day rolls forward to 2 Oct (Wed).
    assert NIGERIA.adjust(date(2024, 10, 1), F) == date(2024, 10, 2)
    # Pre-2019 Democracy Day was 29 May, not 12 June.
    assert date(2015, 5, 29) in nigeria_holidays(2015)
    assert date(2015, 6, 12) not in nigeria_holidays(2015)


def test_kenya_holidays_2024() -> None:
    holidays = kenya_holidays(2024)
    assert date(2024, 12, 12) in holidays  # Jamhuri Day (Thu)
    assert date(2024, 5, 1) in holidays  # Labour Day
    # Mashujaa Day 2024 is a Sunday -> observed the following Monday (21st).
    assert date(2024, 10, 21) in holidays
    assert not KENYA.is_business_day(date(2024, 10, 21))
    assert KENYA.adjust(date(2024, 12, 12), F) == date(2024, 12, 13)
    # Following from the Sunday crosses the observed Monday to Tuesday the 22nd.
    assert KENYA.adjust(date(2024, 10, 20), F) == date(2024, 10, 22)


def test_south_africa_holidays_2024() -> None:
    holidays = south_africa_holidays(2024)
    for day in (
        date(2024, 3, 21),  # Human Rights Day (Thu)
        date(2024, 6, 17),  # Youth Day 16th (Sun) -> observed Monday 17th
        date(2024, 8, 9),  # National Women's Day (Fri)
        date(2024, 9, 24),  # Heritage Day (Tue)
        date(2024, 12, 16),  # Day of Reconciliation (Mon)
        date(2024, 12, 26),  # Day of Goodwill (Thu)
    ):
        assert day in holidays
    assert not SOUTH_AFRICA.is_business_day(date(2024, 12, 16))
    assert SOUTH_AFRICA.adjust(date(2024, 12, 16), F) == date(2024, 12, 17)
    # Youth Day Sunday -> observed Monday 17th, so Following rolls to Tuesday 18th.
    assert SOUTH_AFRICA.adjust(date(2024, 6, 16), F) == date(2024, 6, 18)


def test_fc6c_calendars_use_sat_sun_weekend() -> None:
    for cal in (NIGERIA, KENYA, SOUTH_AFRICA):
        assert not cal.is_business_day(date(2024, 6, 15))  # Saturday
        assert not cal.is_business_day(date(2024, 6, 16))  # Sunday
        assert cal.is_business_day(date(2024, 6, 18))  # Tuesday, ordinary day
