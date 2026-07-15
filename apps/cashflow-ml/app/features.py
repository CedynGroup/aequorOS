"""Calendar feature engineering shared by the synthetic generator, baseline, and LSTM."""

from __future__ import annotations

import calendar
import datetime
from functools import lru_cache

# dow one-hot (7) + day-of-month/31 + month-end-window + payday + mid-month flags.
CALENDAR_FEATURE_COUNT = 11
PAYDAY_DAY_OF_MONTH = 25
MID_MONTH_START = 14
MID_MONTH_END = 16
MONTH_END_BUSINESS_DAYS = 2


@lru_cache(maxsize=256)
def month_end_business_days(year: int, month: int) -> tuple[datetime.date, ...]:
    """The last ``MONTH_END_BUSINESS_DAYS`` business days (Mon-Fri) of a month."""
    day = datetime.date(year, month, calendar.monthrange(year, month)[1])
    found: list[datetime.date] = []
    while len(found) < MONTH_END_BUSINESS_DAYS:
        if day.weekday() < 5:
            found.append(day)
        day -= datetime.timedelta(days=1)
    return tuple(reversed(found))


def is_business_day(day: datetime.date) -> bool:
    return day.weekday() < 5


def is_month_end_window(day: datetime.date) -> bool:
    return day in month_end_business_days(day.year, day.month)


def is_payday(day: datetime.date) -> bool:
    return day.day == PAYDAY_DAY_OF_MONTH


def is_mid_month(day: datetime.date) -> bool:
    return MID_MONTH_START <= day.day <= MID_MONTH_END


def calendar_features(day: datetime.date) -> list[float]:
    """11 calendar features for a target day (see ``CALENDAR_FEATURE_COUNT``)."""
    dow_one_hot = [1.0 if day.weekday() == i else 0.0 for i in range(7)]
    return [
        *dow_one_hot,
        day.day / 31.0,
        float(is_month_end_window(day)),
        float(is_payday(day)),
        float(is_mid_month(day)),
    ]
