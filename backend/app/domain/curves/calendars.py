"""Business-day calendars, tenor arithmetic and schedule generation (spec §2.5, FC-0).

The single biggest quant gap the forward-curve spec calls out: swaps and their
forward grids are dated against a named holiday calendar with business-day
adjustment, spot lag and end-of-month rules. This module is that engine — pure,
deterministic, calendar-only (no rates, no DB).

Design:

- :class:`BusinessDayConvention` — Following / ModifiedFollowing / Preceding /
  ModifiedPreceding / Unadjusted (spec §2.5).
- :class:`Calendar` — a named calendar: a weekend rule (``frozenset`` of Python
  weekday indices, Mon=0) plus an explicit holiday ``frozenset[date]``. Two are
  seeded: **USA** and **GHANA** (GHIPSS). Their holiday sets are *generated* over
  a wide span (:data:`_CALENDAR_START_YEAR`..:data:`_CALENDAR_END_YEAR`) so tenor
  dates decades out (the 50Y pillar lands in 2074) still adjust correctly — a
  static holiday list from a vendor export cannot.
- :func:`Calendar.adjust` / :func:`Calendar.advance` / :func:`Calendar.add_tenor`
  / :func:`Calendar.spot_date` — the adjustment, business-day stepping, tenor
  rolling and T+2 spot primitives.
- :func:`Calendar.generate_schedule` — adjusted period-boundary dates for a
  schedule (pillar or forward-grid rows).

**USA holiday convention (the Eikon-matching rule, documented).** The seeded USA
calendar is the eleven US federal holidays with a **Sunday -> Monday observance
only** (a holiday landing on Saturday is *not* rolled back to the Friday). This
is what reproduces the real Eikon template's date math exactly: its quarterly
grid keeps 2027-12-31 (a Friday) as a period boundary even though 2028-01-01 is a
Saturday, i.e. Eikon did not close the preceding Friday — while its monthly grid
still rolls off Memorial Day 2027-05-31. The vendor's exported holiday CSV, by
contrast, lists both-direction observances (e.g. 2027-12-31); it is therefore
*not* the calendar Eikon adjusted against, and is not used here. Real US federal
calendars do roll Saturday -> Friday; a caller who needs that passes an explicit
holiday set. Islamic-date note only applies to Ghana (below).

**Ghana / GHIPSS holidays (documented).** New Year, Constitution Day (7 Jan,
from 2019), Independence (6 Mar), Good Friday & Easter Monday (computed via the
Western/Gregorian Easter algorithm), May Day (1 May), Founders' Day (4 Aug, from
2019), Republic/Kwame Nkrumah Memorial Day (21 Sep), Farmers' Day (first Friday
of December), Christmas and Boxing Day, each rolled Sunday -> Monday
(GHIPSS practice). **Eid al-Fitr and Eid al-Adha are Islamic (lunar) holidays
whose Gregorian dates require official sighting; they are OMITTED here as static
placeholders pending an authoritative source** rather than approximated, so a
Ghana swap date is never silently wrong on a guessed Eid date.

**NGN / KES / ZAR holidays (FC-6c, documented — each list's statutory source is
cited at its generator).** Nigeria (Public Holidays Act, CBN observed set),
Kenya (Public Holidays Act, Cap. 110) and South Africa (Public Holidays Act 36
of 1994) are seeded alongside USA and GHANA. All three use the Saturday/Sunday
weekend and the same **Sunday -> Monday** observance (for South Africa this is
the Act's own rule; for Nigeria and Kenya it is the government's standing
practice of moving a Sunday public holiday to the following Monday). As with
Ghana, **Islamic (lunar) holidays — Eid al-Fitr, Eid al-Adha, Mawlid — are
OMITTED** rather than approximated, since their Gregorian dates need official
sighting; a caller with an authoritative Eid set can union it in via
:func:`calendar_from_holidays`.

**IMM roll (FC-G3).** :func:`third_wednesday` and :func:`next_imm_date` (plus the
:meth:`Calendar.imm_date` adjusted form) give the quarterly IMM roll — the third
Wednesday of March / June / September / December — that forward-start futures and
FRAs are dated against. The Eikon v3 pillar tab is anchored two business days
before the March-2024 IMM (its spot is that IMM), which is why its short pillars
are forward-starting rather than spot deposits.
"""

from __future__ import annotations

import calendar as _calendar
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date, timedelta
from enum import Enum

__all__ = [
    "BusinessDayConvention",
    "Calendar",
    "CalendarError",
    "GHANA",
    "KENYA",
    "NIGERIA",
    "SOUTH_AFRICA",
    "USA",
    "add_calendar_months",
    "build_calendar",
    "calendar_from_holidays",
    "easter_sunday",
    "ghana_holidays",
    "kenya_holidays",
    "next_imm_date",
    "nigeria_holidays",
    "south_africa_holidays",
    "tenor_to_months",
    "third_wednesday",
    "us_federal_holidays",
]

_WEDNESDAY = 2
_IMM_MONTHS: tuple[int, ...] = (3, 6, 9, 12)

_CALENDAR_START_YEAR = 1990
_CALENDAR_END_YEAR = 2100

_SATURDAY = 5
_SUNDAY = 6
_WEEKEND_SAT_SUN: frozenset[int] = frozenset({_SATURDAY, _SUNDAY})

_TENOR_PATTERN = re.compile(r"^(?:(\d+)Y)?(?:(\d+)M)?(?:(\d+)W)?(?:(\d+)D)?$")


class CalendarError(ValueError):
    """A calendar query or tenor string violates the calendar contract."""


class BusinessDayConvention(Enum):
    """How to roll a raw date onto a business day (spec §2.5)."""

    UNADJUSTED = "unadjusted"
    FOLLOWING = "following"
    MODIFIED_FOLLOWING = "modified_following"
    PRECEDING = "preceding"
    MODIFIED_PRECEDING = "modified_preceding"


# ---------------------------------------------------------------------------
# Holiday generators (documented, deterministic)
# ---------------------------------------------------------------------------


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """The ``n``-th ``weekday`` (Mon=0) of a month, e.g. 3rd Monday of January."""
    first = date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    return first + timedelta(days=offset + 7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    """The last ``weekday`` (Mon=0) of a month, e.g. last Monday of May."""
    last_day = _calendar.monthrange(year, month)[1]
    day = date(year, month, last_day)
    return day - timedelta(days=(day.weekday() - weekday) % 7)


def _observe_sunday_to_monday(day: date) -> date:
    """Sunday -> Monday observance (no Saturday roll-back). Documented in module."""
    return day + timedelta(days=1) if day.weekday() == _SUNDAY else day


def easter_sunday(year: int) -> date:
    """Western (Gregorian) Easter Sunday via the Anonymous/Meeus algorithm."""
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    ell = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * ell) // 451
    month = (h + ell - 7 * m + 114) // 31
    day = ((h + ell - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def us_federal_holidays(year: int) -> frozenset[date]:
    """The eleven US federal holidays for a year, Sunday -> Monday observance only.

    Juneteenth is included from 2021 (its first observance). See the module
    docstring for why Saturday holidays are *not* rolled to the Friday — that is
    the rule that reproduces the Eikon USA calendar exactly.
    """
    days = {
        _observe_sunday_to_monday(date(year, 1, 1)),  # New Year's Day
        _nth_weekday(year, 1, 0, 3),  # Martin Luther King Jr. Day
        _nth_weekday(year, 2, 0, 3),  # Washington's Birthday (Presidents' Day)
        _last_weekday(year, 5, 0),  # Memorial Day
        _observe_sunday_to_monday(date(year, 7, 4)),  # Independence Day
        _nth_weekday(year, 9, 0, 1),  # Labor Day
        _nth_weekday(year, 10, 0, 2),  # Columbus Day
        _observe_sunday_to_monday(date(year, 11, 11)),  # Veterans Day
        _nth_weekday(year, 11, 3, 4),  # Thanksgiving
        _observe_sunday_to_monday(date(year, 12, 25)),  # Christmas Day
    }
    if year >= 2021:
        days.add(_observe_sunday_to_monday(date(year, 6, 19)))  # Juneteenth
    return frozenset(days)


def ghana_holidays(year: int) -> frozenset[date]:
    """Ghana / GHIPSS public holidays for a year (Islamic Eids omitted — see module).

    Fixed-date holidays roll Sunday -> Monday. Constitution Day (7 Jan) and the
    renamed Founders' Day (4 Aug) are included from 2019, when they were enacted.
    """
    easter = easter_sunday(year)
    days = {
        _observe_sunday_to_monday(date(year, 1, 1)),  # New Year's Day
        _observe_sunday_to_monday(date(year, 3, 6)),  # Independence Day
        easter - timedelta(days=2),  # Good Friday
        easter + timedelta(days=1),  # Easter Monday
        _observe_sunday_to_monday(date(year, 5, 1)),  # May Day / Workers' Day
        _observe_sunday_to_monday(date(year, 9, 21)),  # Kwame Nkrumah Memorial Day
        _first_friday_of_december(year),  # Farmers' Day
        _observe_sunday_to_monday(date(year, 12, 25)),  # Christmas Day
        _observe_sunday_to_monday(date(year, 12, 26)),  # Boxing Day
    }
    if year >= 2019:
        days.add(_observe_sunday_to_monday(date(year, 1, 7)))  # Constitution Day
        days.add(_observe_sunday_to_monday(date(year, 8, 4)))  # Founders' Day
    return frozenset(days)


def _first_friday_of_december(year: int) -> date:
    return _nth_weekday(year, 12, 4, 1)  # Friday=4, first occurrence


def nigeria_holidays(year: int) -> frozenset[date]:
    """Nigeria federal public holidays for a year (Islamic Eids/Mawlid omitted).

    Source: Nigeria Public Holidays Act (Cap. P40, LFN 2004) plus the Central
    Bank of Nigeria's observed set. Fixed-date holidays roll Sunday -> Monday
    (the Federal Government's standing practice of declaring the following Monday
    when a public holiday falls on a Sunday). Democracy Day moved to **12 June
    from 2019** (Public Holiday declaration of 2018); the earlier **29 May**
    observance (2000-2018) is included for that window. Good Friday and Easter
    Monday are the Western/Gregorian Easter dates.
    """
    easter = easter_sunday(year)
    days = {
        _observe_sunday_to_monday(date(year, 1, 1)),  # New Year's Day
        easter - timedelta(days=2),  # Good Friday
        easter + timedelta(days=1),  # Easter Monday
        _observe_sunday_to_monday(date(year, 5, 1)),  # Workers' Day
        _observe_sunday_to_monday(date(year, 10, 1)),  # Independence Day
        _observe_sunday_to_monday(date(year, 12, 25)),  # Christmas Day
        _observe_sunday_to_monday(date(year, 12, 26)),  # Boxing Day
    }
    if year >= 2019:
        days.add(_observe_sunday_to_monday(date(year, 6, 12)))  # Democracy Day (from 2019)
    elif 2000 <= year <= 2018:
        days.add(_observe_sunday_to_monday(date(year, 5, 29)))  # Democracy Day (pre-2019)
    return frozenset(days)


def kenya_holidays(year: int) -> frozenset[date]:
    """Kenya public holidays for a year (Islamic Idd holidays omitted).

    Source: Kenya Public Holidays Act (Cap. 110). Fixed-date holidays roll
    Sunday -> Monday (statutory: a holiday falling on a Sunday is observed the
    next day). Good Friday and Easter Monday are the Western/Gregorian Easter
    dates. **Moi Day (10 Oct) is omitted** — abolished in 2010 and its
    2017 court reinstatement remained contested — rather than assert a date the
    market did not uniformly observe.
    """
    easter = easter_sunday(year)
    days = {
        _observe_sunday_to_monday(date(year, 1, 1)),  # New Year's Day
        easter - timedelta(days=2),  # Good Friday
        easter + timedelta(days=1),  # Easter Monday
        _observe_sunday_to_monday(date(year, 5, 1)),  # Labour Day
        _observe_sunday_to_monday(date(year, 6, 1)),  # Madaraka Day
        _observe_sunday_to_monday(date(year, 10, 20)),  # Mashujaa (Heroes') Day
        _observe_sunday_to_monday(date(year, 12, 12)),  # Jamhuri (Independence) Day
        _observe_sunday_to_monday(date(year, 12, 25)),  # Christmas Day
        _observe_sunday_to_monday(date(year, 12, 26)),  # Utamaduni (Boxing) Day
    }
    return frozenset(days)


def south_africa_holidays(year: int) -> frozenset[date]:
    """South Africa public holidays for a year (the Act's Sunday -> Monday rule).

    Source: South Africa Public Holidays Act 36 of 1994. The Act itself provides
    that any public holiday falling on a **Sunday** is observed on the following
    Monday (Saturday holidays are not moved) — the exact rule
    :func:`_observe_sunday_to_monday` implements. Good Friday and Family Day
    (Easter Monday) are the Western/Gregorian Easter dates. One-off presidentially
    declared holidays (e.g. election days) are excluded.
    """
    easter = easter_sunday(year)
    days = {
        _observe_sunday_to_monday(date(year, 1, 1)),  # New Year's Day
        _observe_sunday_to_monday(date(year, 3, 21)),  # Human Rights Day
        easter - timedelta(days=2),  # Good Friday
        easter + timedelta(days=1),  # Family Day (Easter Monday)
        _observe_sunday_to_monday(date(year, 4, 27)),  # Freedom Day
        _observe_sunday_to_monday(date(year, 5, 1)),  # Workers' Day
        _observe_sunday_to_monday(date(year, 6, 16)),  # Youth Day
        _observe_sunday_to_monday(date(year, 8, 9)),  # National Women's Day
        _observe_sunday_to_monday(date(year, 9, 24)),  # Heritage Day
        _observe_sunday_to_monday(date(year, 12, 16)),  # Day of Reconciliation
        _observe_sunday_to_monday(date(year, 12, 25)),  # Christmas Day
        _observe_sunday_to_monday(date(year, 12, 26)),  # Day of Goodwill
    }
    return frozenset(days)


# ---------------------------------------------------------------------------
# IMM roll (third Wednesday of Mar/Jun/Sep/Dec) — FC-G3
# ---------------------------------------------------------------------------


def third_wednesday(year: int, month: int) -> date:
    """The third Wednesday of a month — the IMM/futures roll convention."""
    return _nth_weekday(year, month, _WEDNESDAY, 3)


def next_imm_date(reference: date, *, imm_months: tuple[int, ...] = _IMM_MONTHS) -> date:
    """The next quarterly IMM date (third Wednesday of an IMM month) on/after ``reference``.

    IMM months default to March/June/September/December. The returned date is the
    raw third Wednesday; use :meth:`Calendar.imm_date` for the business-day-adjusted
    form (a third Wednesday is a holiday only in rare years).
    """
    for year in (reference.year, reference.year + 1):
        for month in imm_months:
            candidate = third_wednesday(year, month)
            if candidate >= reference:
                return candidate
    raise CalendarError(f"No IMM date found on or after {reference}.")  # pragma: no cover


def _generate_holidays(
    generator: Callable[[int], frozenset[date]], start_year: int, end_year: int
) -> frozenset[date]:
    collected: set[date] = set()
    for year in range(start_year, end_year + 1):
        collected |= generator(year)
    return frozenset(collected)


# ---------------------------------------------------------------------------
# Calendar month arithmetic
# ---------------------------------------------------------------------------


def add_calendar_months(day: date, months: int, end_of_month: bool = False) -> date:
    """Shift a date by whole calendar months.

    The day-of-month is clamped to the target month's length (so 31 Jan + 1M is
    28/29 Feb). With ``end_of_month`` set, a date that is the last day of its
    month maps to the last day of the target month (the EOM roll rule).
    """
    month_index = day.month - 1 + months
    year = day.year + month_index // 12
    month = month_index % 12 + 1
    last_day = _calendar.monthrange(year, month)[1]
    if end_of_month and day.day == _calendar.monthrange(day.year, day.month)[1]:
        return date(year, month, last_day)
    return date(year, month, min(day.day, last_day))


# ---------------------------------------------------------------------------
# Calendar
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Calendar:
    """A named business-day calendar: weekend rule plus an explicit holiday set."""

    name: str
    weekend: frozenset[int] = _WEEKEND_SAT_SUN
    holidays: frozenset[date] = frozenset()

    def is_business_day(self, day: date) -> bool:
        """True when ``day`` is neither a weekend day nor a holiday."""
        return day.weekday() not in self.weekend and day not in self.holidays

    def adjust(self, day: date, convention: BusinessDayConvention) -> date:
        """Roll ``day`` onto a business day per ``convention`` (spec §2.5)."""
        if convention is BusinessDayConvention.UNADJUSTED or self.is_business_day(day):
            return day
        if convention is BusinessDayConvention.FOLLOWING:
            return self._roll(day, +1)
        if convention is BusinessDayConvention.PRECEDING:
            return self._roll(day, -1)
        if convention is BusinessDayConvention.MODIFIED_FOLLOWING:
            rolled = self._roll(day, +1)
            return self._roll(day, -1) if rolled.month != day.month else rolled
        # MODIFIED_PRECEDING
        rolled = self._roll(day, -1)
        return self._roll(day, +1) if rolled.month != day.month else rolled

    def advance(self, day: date, business_days: int) -> date:
        """Advance ``day`` by a signed number of business days (0 returns ``day``)."""
        step = 1 if business_days >= 0 else -1
        cursor = day
        for _ in range(abs(business_days)):
            cursor += timedelta(days=step)
            while not self.is_business_day(cursor):
                cursor += timedelta(days=step)
        return cursor

    def spot_date(self, as_of: date, lag: int = 2) -> date:
        """The spot date: ``as_of`` advanced by ``lag`` business days (T+2 default)."""
        if lag < 0:
            raise CalendarError("Spot lag must be non-negative.")
        return self.advance(as_of, lag)

    def add_tenor(
        self,
        start: date,
        tenor: str,
        convention: BusinessDayConvention = BusinessDayConvention.MODIFIED_FOLLOWING,
        end_of_month: bool = False,
    ) -> date:
        """Resolve a tenor label to an adjusted date measured from ``start``.

        Tenors: ``ON`` (1 business day), ``TN`` (2 business days), ``nD`` /
        ``nW`` (calendar days/weeks, then adjusted), ``nM`` / ``nY`` and their
        combination ``1Y3M`` (calendar months, then adjusted, EOM-aware). ``ON``
        and ``TN`` are pure business-day advances and ignore ``convention``.
        """
        token = tenor.strip().upper()
        if token == "ON":
            return self.advance(start, 1)
        if token == "TN":
            return self.advance(start, 2)
        years, months, weeks, days = _parse_tenor(token)
        if weeks or days:
            if years or months:
                raise CalendarError(f"Tenor '{tenor}' mixes week/day and month/year terms.")
            raw = start + timedelta(days=7 * weeks + days)
            return self.adjust(raw, convention)
        raw = add_calendar_months(start, 12 * years + months, end_of_month=end_of_month)
        return self.adjust(raw, convention)

    def generate_schedule(  # noqa: PLR0913 - the schedule's governed roll parameters
        self,
        start: date,
        periods: int,
        interval_months: int,
        convention: BusinessDayConvention = BusinessDayConvention.MODIFIED_FOLLOWING,
        end_of_month: bool = False,
        adjust_start: bool = True,
    ) -> tuple[date, ...]:
        """Adjusted period-boundary dates rolled from an anchor.

        Returns ``periods + 1`` boundaries: the anchor followed by
        ``add_calendar_months(anchor, k*interval)`` for ``k`` in ``1..periods``,
        each adjusted by ``convention``. ``adjust_start`` controls whether the
        anchor itself is adjusted (the Eikon forward grid keeps its raw
        quarter-end anchor, so the grid builder passes ``adjust_start=False``).
        """
        if periods < 0:
            raise CalendarError("A schedule needs a non-negative number of periods.")
        if interval_months <= 0:
            raise CalendarError("Schedule interval must be a positive number of months.")
        anchor = start if not adjust_start else self.adjust(start, convention)
        boundaries: list[date] = [anchor]
        for k in range(1, periods + 1):
            raw = add_calendar_months(start, k * interval_months, end_of_month=end_of_month)
            boundaries.append(self.adjust(raw, convention))
        return tuple(boundaries)

    def imm_date(
        self,
        reference: date,
        convention: BusinessDayConvention = BusinessDayConvention.UNADJUSTED,
        *,
        imm_months: tuple[int, ...] = _IMM_MONTHS,
    ) -> date:
        """The next quarterly IMM roll on/after ``reference``, business-day adjusted.

        Defaults to ``UNADJUSTED`` (a third Wednesday is virtually always a
        business day); pass a convention to roll the rare holiday case.
        """
        return self.adjust(next_imm_date(reference, imm_months=imm_months), convention)

    def _roll(self, day: date, step: int) -> date:
        cursor = day
        while not self.is_business_day(cursor):
            cursor += timedelta(days=step)
        return cursor


def tenor_to_months(tenor: str) -> int:
    """Whole months in a year/month tenor (``"5Y"`` -> 60, ``"1Y6M"`` -> 18).

    Rejects week/day tenors, which are not month-commensurable — swap and OIS
    schedules roll in months.
    """
    years, months, weeks, days = _parse_tenor(tenor.strip().upper())
    if weeks or days:
        raise CalendarError(f"Tenor '{tenor}' is not a whole number of months.")
    return 12 * years + months


def _parse_tenor(token: str) -> tuple[int, int, int, int]:
    match = _TENOR_PATTERN.match(token)
    if match is None or token == "":
        raise CalendarError(f"Unrecognised tenor '{token}'.")
    years, months, weeks, days = (int(group) if group else 0 for group in match.groups())
    if years == months == weeks == days == 0:
        raise CalendarError(f"Tenor '{token}' has zero length.")
    return years, months, weeks, days


def build_calendar(
    name: str,
    holiday_generator: Callable[[int], frozenset[date]],
    weekend: frozenset[int] = _WEEKEND_SAT_SUN,
    start_year: int = _CALENDAR_START_YEAR,
    end_year: int = _CALENDAR_END_YEAR,
) -> Calendar:
    """Build a :class:`Calendar` by materialising a holiday generator over a span."""
    return Calendar(
        name=name,
        weekend=weekend,
        holidays=_generate_holidays(holiday_generator, start_year, end_year),
    )


def calendar_from_holidays(
    name: str, holidays: Iterable[date], weekend: frozenset[int] = _WEEKEND_SAT_SUN
) -> Calendar:
    """Build a :class:`Calendar` from an explicit holiday set (e.g. a vendor CSV)."""
    return Calendar(name=name, weekend=weekend, holidays=frozenset(holidays))


USA: Calendar = build_calendar("USA", us_federal_holidays)
GHANA: Calendar = build_calendar("GHANA", ghana_holidays)
NIGERIA: Calendar = build_calendar("NIGERIA", nigeria_holidays)
KENYA: Calendar = build_calendar("KENYA", kenya_holidays)
SOUTH_AFRICA: Calendar = build_calendar("SOUTH_AFRICA", south_africa_holidays)
