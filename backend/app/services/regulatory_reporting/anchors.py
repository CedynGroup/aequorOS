"""Reporting anchors — the regulator's own reporting dates for a return.

THE ONE AUTHORITY on the question "which dates does this return report on?".
The answer comes from :class:`ReturnDefinition` — its ``frequency`` and the
BoG cadence conventions — and from nothing else. In particular it does NOT
come from ``bank_reporting_periods``.

Why this module exists (founder review, 2026-08-23)
---------------------------------------------------
A reporting reference date is set by the REGULATOR, not by the reporting
institution and not by when data happened to arrive. That separation is the
settled convention in supervisory reporting: the EBA framework distinguishes
the *reporting reference date* (the as-of date the figures describe) from the
*remittance date* (the deadline to send them), and both are fixed in the
regulation. BoG works the same way — returns are daily, weekly, monthly,
quarterly, semi-annual and annual, each anchored on a period end, with a time
limit counted from that end.

The platform modelled the regulator's side correctly from the start: the
registry's ``frequency`` and ``deadline_rule``, plus ``WEEKLY_ANCHOR_WEEKDAY``,
already describe BoG's calendar with no reference to ingestion. What went
wrong is that the Returns workspace offered a DIFFERENT list — the
``period_end`` values of ``bank_reporting_periods``, which are created as a
side effect of ingestion (``fact_derivation._ensure_period``). That made the
filing calendar a function of data arrival:

- 6 of the 22 BSD forms are weekly (Friday close). Non-daily generation
  matched ``period_end == reporting_date`` exactly, and on the primary the
  reference tenant had 19 Friday period-ends against 517 Fridays in its span —
  17 of those 19 only because the month happened to end on a Friday. 96% of
  the weekly filing dates could not be selected at all.
- A tenant that had ingested nothing had an EMPTY reporting calendar rather
  than a full calendar with nothing computed yet — which reads as "the product
  is broken", not as "no data yet".

So the direction of the dependency is inverted here and stays inverted:

    ReturnDefinition ──▶ anchor date ──▶ snapshot lookup (may be absent)

The anchor exists whether or not the bank has data for it. Whether the bank
CAN file it is a separate, honestly-reported fact — see :class:`AnchorCoverage`
and ``common.get_snapshot_for_reporting_date``.
"""

from __future__ import annotations

from calendar import monthrange
from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.models import Bank, BankReportingPeriod
from app.services.regulatory_reporting.registry import ReturnDefinition

#: Months per cycle for the period-end frequencies (daily and weekly do not
#: land on month ends and are enumerated by their own rules below).
_FREQUENCY_MONTHS = {"monthly": 1, "quarterly": 3, "semiannual": 6, "annual": 12}

#: Daily obligations enumerate only the most recent business days — a full year
#: of daily rows would swamp the calendar — and the window ends at ``as_of``.
DAILY_WINDOW_BUSINESS_DAYS = 5

#: Weekly obligations show a bounded trailing window plus the horizon's anchors.
WEEKLY_TRAILING_WEEKS = 8


def month_end(year: int, month: int) -> date:
    return date(year, month, monthrange(year, month)[1])


def _daily_anchors(as_of: date) -> list[date]:
    """The most recent ``DAILY_WINDOW_BUSINESS_DAYS`` business days ending on or
    before ``as_of`` (weekends skipped), oldest first."""
    dates: list[date] = []
    cursor = as_of
    while len(dates) < DAILY_WINDOW_BUSINESS_DAYS:
        if cursor.weekday() < 5:  # noqa: PLR2004 — Mon..Fri
            dates.append(cursor)
        cursor -= timedelta(days=1)
    return sorted(dates)


def _weekly_anchors(as_of: date, horizon_end: date) -> list[date]:
    """The last ``WEEKLY_TRAILING_WEEKS`` weekly anchors before ``as_of`` plus
    every anchor up to ``horizon_end`` (Friday close by default).

    The BoG Guide fixes the weekly cadence and the time limit, not the weekday;
    ``WEEKLY_ANCHOR_WEEKDAY`` carries the platform's documented convention.
    """
    from app.services.regulatory_reporting.bog_forms.catalog import (  # noqa: PLC0415
        WEEKLY_ANCHOR_WEEKDAY,
    )

    delta = (as_of.weekday() - WEEKLY_ANCHOR_WEEKDAY) % 7
    last_anchor = as_of - timedelta(days=delta)
    dates = [last_anchor - timedelta(weeks=week) for week in range(WEEKLY_TRAILING_WEEKS, 0, -1)]
    cursor = last_anchor
    while cursor <= horizon_end:
        dates.append(cursor)
        cursor += timedelta(weeks=1)
    return dates


def _period_end_anchors(definition: ReturnDefinition, as_of: date, horizon_end: date) -> list[date]:
    """The most recent elapsed period end plus every period end in the horizon."""
    step = _FREQUENCY_MONTHS[definition.frequency]
    months = tuple(month for month in range(1, 13) if month % step == 0)
    candidates = [
        month_end(year, month)
        for year in range(as_of.year - 2, horizon_end.year + 1)
        for month in months
    ]
    elapsed = [candidate for candidate in candidates if candidate < as_of]
    upcoming = [candidate for candidate in candidates if as_of <= candidate <= horizon_end]
    return ([elapsed[-1]] if elapsed else []) + upcoming


def anchor_dates(definition: ReturnDefinition, as_of: date, horizon_end: date) -> list[date]:
    """Every reporting date this return reports on, oldest first.

    Purely a function of the registry entry and the calendar — no database, no
    tenant, no ingestion. Event-driven returns (the LRT corporate packs) have no
    periodic cycle and return no anchors: expanding their nominal frequency
    would fabricate obligations that do not exist.
    """
    if definition.event_driven:
        return []
    if definition.frequency == "daily":
        return _daily_anchors(as_of)
    if definition.frequency == "weekly":
        return _weekly_anchors(as_of, horizon_end)
    return _period_end_anchors(definition, as_of, horizon_end)


def horizon_end_for(as_of: date, horizon_months: int) -> date:
    """The month end ``horizon_months`` after ``as_of``'s month."""
    total = as_of.year * 12 + (as_of.month - 1) + horizon_months
    return month_end(total // 12, total % 12 + 1)


# ---------------------------------------------------------------------------
# Coverage — whether the bank has computed figures AS OF an anchor
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AnchorCoverage:
    """Whether a snapshot exists for one anchor date, and the nearest one if not.

    ``nearest_before`` is reported so a refusal or an empty state can say what
    the bank DOES have ("your most recent computed position is 30 June 2026")
    instead of only what it lacks. It is never a substitute for the missing
    snapshot — see the note on :func:`snapshot_coverage`.
    """

    reporting_date: date
    covered: bool
    nearest_before: date | None


def snapshot_coverage(
    db: Session, ctx: TenantContext, bank: Bank, dates: list[date]
) -> dict[date, AnchorCoverage]:
    """Which of ``dates`` the bank has a computed fact snapshot for.

    Coverage is EXACT: a snapshot covers an anchor only when its ``period_end``
    equals that anchor. A Friday-close weekly return cannot be produced from a
    month-end book, and a daily return cannot be produced from last month's —
    so "the nearest earlier snapshot" is reported for the message and never
    used as the figures. This is the same fail-closed discipline the rest of the
    platform applies to an unresolved input: state the gap, do not fill it.
    """
    if not dates:
        return {}
    ordered = sorted(dates)
    # One scan of every period end at or before the newest anchor: enough to
    # answer both exact coverage and "nearest earlier" without a query per date.
    period_ends = set(
        db.scalars(
            select(BankReportingPeriod.period_end).where(
                BankReportingPeriod.organization_id == ctx.organization_id,
                BankReportingPeriod.bank_id == bank.id,
                BankReportingPeriod.period_end <= ordered[-1],
            )
        ).all()
    )
    descending = sorted(period_ends, reverse=True)
    coverage: dict[date, AnchorCoverage] = {}
    for anchor in ordered:
        nearest = next((end for end in descending if end < anchor), None)
        coverage[anchor] = AnchorCoverage(
            reporting_date=anchor,
            covered=anchor in period_ends,
            nearest_before=nearest,
        )
    return coverage
