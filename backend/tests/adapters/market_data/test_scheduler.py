from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from app.adapters.market_data.scheduler import due_scopes, next_pull_due
from app.adapters.market_data.scope_taxonomy import DataScope, PullFrequency

ACCRA = "Africa/Accra"  # UTC+0, the reference institution timezone
NOW = datetime(2026, 7, 14, 15, 0, tzinfo=UTC)  # Tuesday


def test_on_demand_is_never_scheduled() -> None:
    assert next_pull_due(PullFrequency.ON_DEMAND, NOW, ACCRA, NOW) is None
    assert next_pull_due(PullFrequency.ON_DEMAND, None, ACCRA, NOW) is None


def test_never_pulled_is_due_immediately() -> None:
    for frequency in (
        PullFrequency.HOURLY,
        PullFrequency.END_OF_DAY,
        PullFrequency.WEEKLY,
        PullFrequency.MONTHLY,
    ):
        assert next_pull_due(frequency, None, ACCRA, NOW) == NOW


# -- END_OF_DAY: 17:00 institution-local, business days -----------------------


def test_eod_same_day_when_pulled_before_five() -> None:
    last = datetime(2026, 7, 14, 12, 0, tzinfo=UTC)  # Tuesday noon
    due = next_pull_due(PullFrequency.END_OF_DAY, last, ACCRA, NOW)
    assert due == datetime(2026, 7, 14, 17, 0, tzinfo=ZoneInfo(ACCRA))


def test_eod_next_day_when_pulled_at_five() -> None:
    last = datetime(2026, 7, 14, 17, 0, tzinfo=UTC)  # Tuesday 17:00
    due = next_pull_due(PullFrequency.END_OF_DAY, last, ACCRA, NOW)
    assert due == datetime(2026, 7, 15, 17, 0, tzinfo=ZoneInfo(ACCRA))


def test_eod_friday_rolls_to_monday() -> None:
    last = datetime(2026, 7, 10, 17, 0, tzinfo=UTC)  # Friday 17:00
    due = next_pull_due(PullFrequency.END_OF_DAY, last, ACCRA, NOW)
    assert due == datetime(2026, 7, 13, 17, 0, tzinfo=ZoneInfo(ACCRA))  # Monday


def test_eod_honors_institution_timezone() -> None:
    # 21:00 UTC on Tuesday is 17:00 in New York (EDT): the EOD pull just ran,
    # so the next one is Wednesday 17:00 local.
    last = datetime(2026, 7, 14, 21, 0, tzinfo=UTC)
    due = next_pull_due(PullFrequency.END_OF_DAY, last, "America/New_York", NOW)
    assert due is not None
    assert due == datetime(2026, 7, 15, 17, 0, tzinfo=ZoneInfo("America/New_York"))
    assert due.astimezone(UTC) == datetime(2026, 7, 15, 21, 0, tzinfo=UTC)


# -- HOURLY: business hours 08:00-17:00 local, weekdays -----------------------


def test_hourly_within_business_hours() -> None:
    last = datetime(2026, 7, 14, 10, 0, tzinfo=UTC)
    due = next_pull_due(PullFrequency.HOURLY, last, ACCRA, NOW)
    assert due == datetime(2026, 7, 14, 11, 0, tzinfo=ZoneInfo(ACCRA))


def test_hourly_after_close_rolls_to_next_morning() -> None:
    last = datetime(2026, 7, 14, 16, 30, tzinfo=UTC)  # Tuesday 16:30
    due = next_pull_due(PullFrequency.HOURLY, last, ACCRA, NOW)
    assert due == datetime(2026, 7, 15, 8, 0, tzinfo=ZoneInfo(ACCRA))  # Wednesday 08:00


def test_hourly_before_open_clamps_to_open() -> None:
    last = datetime(2026, 7, 14, 6, 30, tzinfo=UTC)
    due = next_pull_due(PullFrequency.HOURLY, last, ACCRA, NOW)
    assert due == datetime(2026, 7, 14, 8, 0, tzinfo=ZoneInfo(ACCRA))


def test_hourly_friday_close_rolls_to_monday_open() -> None:
    last = datetime(2026, 7, 10, 16, 30, tzinfo=UTC)  # Friday 16:30
    due = next_pull_due(PullFrequency.HOURLY, last, ACCRA, NOW)
    assert due == datetime(2026, 7, 13, 8, 0, tzinfo=ZoneInfo(ACCRA))  # Monday 08:00


# -- WEEKLY: Monday at start of business ---------------------------------------


def test_weekly_due_next_monday() -> None:
    last = datetime(2026, 7, 8, 9, 0, tzinfo=UTC)  # Wednesday
    due = next_pull_due(PullFrequency.WEEKLY, last, ACCRA, NOW)
    assert due == datetime(2026, 7, 13, 8, 0, tzinfo=ZoneInfo(ACCRA))  # Monday


def test_weekly_pull_on_monday_schedules_following_monday() -> None:
    last = datetime(2026, 7, 13, 8, 0, tzinfo=UTC)  # Monday 08:00 exactly
    due = next_pull_due(PullFrequency.WEEKLY, last, ACCRA, NOW)
    assert due == datetime(2026, 7, 20, 8, 0, tzinfo=ZoneInfo(ACCRA))


# -- MONTHLY: the 1st at start of business --------------------------------------


def test_monthly_due_first_of_next_month() -> None:
    last = datetime(2026, 7, 1, 8, 0, tzinfo=UTC)
    due = next_pull_due(PullFrequency.MONTHLY, last, ACCRA, NOW)
    assert due == datetime(2026, 8, 1, 8, 0, tzinfo=ZoneInfo(ACCRA))


def test_monthly_mid_month_pull_still_targets_first() -> None:
    last = datetime(2026, 7, 15, 11, 0, tzinfo=UTC)
    due = next_pull_due(PullFrequency.MONTHLY, last, ACCRA, NOW)
    assert due == datetime(2026, 8, 1, 8, 0, tzinfo=ZoneInfo(ACCRA))


def test_monthly_december_rolls_into_new_year() -> None:
    last = datetime(2026, 12, 31, 9, 0, tzinfo=UTC)
    due = next_pull_due(PullFrequency.MONTHLY, last, ACCRA, NOW)
    assert due == datetime(2027, 1, 1, 8, 0, tzinfo=ZoneInfo(ACCRA))


def test_monthly_before_business_start_on_the_first() -> None:
    last = datetime(2026, 7, 1, 6, 0, tzinfo=UTC)  # 1st, before 08:00
    due = next_pull_due(PullFrequency.MONTHLY, last, ACCRA, NOW)
    assert due == datetime(2026, 7, 1, 8, 0, tzinfo=ZoneInfo(ACCRA))


# -- due_scopes -----------------------------------------------------------------


def test_due_scopes_selects_only_due_entries() -> None:
    schedule = {
        # JSON-shaped keys/values, as stored on MarketDataConnection.schedule.
        "FX_SPOT_USD_GHS": "HOURLY",
        DataScope.YIELD_CURVE_GHS: PullFrequency.END_OF_DAY,
        "MACRO_GHANA_GDP_FORECAST": "ON_DEMAND",
        DataScope.SECURITY_MASTER_GOG_BONDS: "WEEKLY",
    }
    last_pulls = {
        "FX_SPOT_USD_GHS": datetime(2026, 7, 14, 13, 30, tzinfo=UTC),  # due 14:30 < now
        DataScope.YIELD_CURVE_GHS: datetime(2026, 7, 13, 17, 0, tzinfo=UTC),  # due 17:00 > now
        "MACRO_GHANA_GDP_FORECAST": None,
        # SECURITY_MASTER_GOG_BONDS never pulled -> due immediately.
    }
    due = due_scopes(schedule, last_pulls, NOW, institution_tz=ACCRA)
    assert due == [DataScope.FX_SPOT_USD_GHS, DataScope.SECURITY_MASTER_GOG_BONDS]


def test_due_scopes_empty_schedule() -> None:
    assert due_scopes({}, {}, NOW) == []
