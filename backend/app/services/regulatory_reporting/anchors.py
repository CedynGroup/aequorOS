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

The window runs BOTH ways (founder review, 2026-09-19)
------------------------------------------------------
The first cut of this module offered a forward horizon plus exactly ONE elapsed
period end. That is the wrong shape for the same reason ingestion-derived dates
were: an OVERDUE return for an elapsed period is precisely the one the bank
still owes the regulator, and a bank whose last book is a quarter old could not
select any date it had figures for. On 2026-09-19 a tenant whose newest computed
position was 30 June 2026 was offered 31 August 2026 onward for a monthly
return — five dates, every one ``awaiting_data`` — and 30 June, which had a
snapshot, was not in the list at all. The return could not be generated.

So the offered span is one bounded WINDOW (:class:`AnchorWindow`), used by every
cadence: a trailing ``lookback_months`` of elapsed anchors and a forward
``horizon_months`` of upcoming ones. Widening the window changes nothing about
where a reporting date comes from — it is still ``ReturnDefinition`` and the BoG
cadence conventions, still no tenant data, still no ingestion.
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

#: How far FORWARD the offered window reaches by default. A quarter of upcoming
#: obligations is enough to plan against; nothing forward can be filed yet.
DEFAULT_HORIZON_MONTHS = 3

#: How far BACK the offered window reaches by default — two quarters.
#:
#: The number has to answer one question: how far behind can a bank be and still
#: be handed every date it owes? Two quarters covers a bank one or two quarters
#: in arrears on the cadences that carry the BSD family — six elapsed month ends
#: for a monthly return, both open quarter ends for a quarterly one — which is
#: the realistic worst case for a catch-up filing run. It is deliberately NOT a
#: statute of limitations: a return older than the window is still owed, and the
#: floor below keeps the most recent elapsed anchor of the long cadences
#: (semi-annual, annual) selectable however old it is. Callers may widen it to
#: 24 months. It is jurisdiction-neutral: months of the return's own cadence,
#: with no BoG-specific count anywhere in it.
DEFAULT_LOOKBACK_MONTHS = 6

#: Daily obligations enumerate only the most recent business days — one lookback
#: month is already ~22 of them and two quarters ~130, which is a picker nobody
#: can read. The shared window still bounds how far back they may reach; this
#: bounds how many are listed. The daily window ends at ``as_of``.
DAILY_WINDOW_BUSINESS_DAYS = 5


def month_end(year: int, month: int) -> date:
    return date(year, month, monthrange(year, month)[1])


@dataclass(frozen=True)
class AnchorWindow:
    """The bounded span of reporting dates a return offers, for ONE ``as_of``.

    One window concept for every cadence, so the daily, weekly and period-end
    paths cannot drift into three different notions of "recent": ``start`` is
    the oldest ELAPSED anchor offered, ``end`` the newest upcoming one, and
    ``as_of`` the dividing line between the two (it is also what decides which
    anchors are already owed).
    """

    as_of: date
    start: date
    end: date


def horizon_end_for(as_of: date, horizon_months: int) -> date:
    """The month end ``horizon_months`` after ``as_of``'s month."""
    total = as_of.year * 12 + (as_of.month - 1) + horizon_months
    return month_end(total // 12, total % 12 + 1)


def lookback_start_for(as_of: date, lookback_months: int) -> date:
    """The first day of the month ``lookback_months`` before ``as_of``'s month.

    The mirror of :func:`horizon_end_for`: the horizon runs to a month END so
    the last upcoming period end falls inside it whole, and the lookback runs
    from a month START so the first elapsed one does.
    """
    total = as_of.year * 12 + (as_of.month - 1) - lookback_months
    return date(total // 12, total % 12 + 1, 1)


def anchor_window(
    as_of: date,
    *,
    lookback_months: int = DEFAULT_LOOKBACK_MONTHS,
    horizon_months: int = DEFAULT_HORIZON_MONTHS,
) -> AnchorWindow:
    """The window of reporting dates offered at ``as_of``."""
    return AnchorWindow(
        as_of=as_of,
        start=lookback_start_for(as_of, lookback_months),
        end=horizon_end_for(as_of, horizon_months),
    )


def _daily_anchors(window: AnchorWindow) -> list[date]:
    """The most recent business days inside ``window``, oldest first.

    Capped at :data:`DAILY_WINDOW_BUSINESS_DAYS` because daily anchors are dense
    (see that constant). The window's start still bounds the walk, so the cap is
    a ceiling on the count and never a reason to reach outside the window.
    """
    dates: list[date] = []
    cursor = window.as_of
    while len(dates) < DAILY_WINDOW_BUSINESS_DAYS and cursor >= window.start:
        if cursor.weekday() < 5:  # noqa: PLR2004 — Mon..Fri
            dates.append(cursor)
        cursor -= timedelta(days=1)
    return sorted(dates)


def _weekly_anchors(window: AnchorWindow) -> list[date]:
    """Every weekly anchor inside ``window`` (Friday close by default).

    The BoG Guide fixes the weekly cadence and the time limit, not the weekday;
    ``WEEKLY_ANCHOR_WEEKDAY`` carries the platform's documented convention.
    """
    from app.services.regulatory_reporting.bog_forms.catalog import (  # noqa: PLC0415
        WEEKLY_ANCHOR_WEEKDAY,
    )

    delta = (window.as_of.weekday() - WEEKLY_ANCHOR_WEEKDAY) % 7
    # The most recent anchor on or before ``as_of`` — the weekly close the bank
    # already owes. Always offered, the same floor the period-end path applies.
    last_elapsed = window.as_of - timedelta(days=delta)
    dates = [last_elapsed]
    cursor = last_elapsed - timedelta(weeks=1)
    while cursor >= window.start:
        dates.append(cursor)
        cursor -= timedelta(weeks=1)
    cursor = last_elapsed + timedelta(weeks=1)
    while cursor <= window.end:
        dates.append(cursor)
        cursor += timedelta(weeks=1)
    return sorted(dates)


def _period_end_anchors(definition: ReturnDefinition, window: AnchorWindow) -> list[date]:
    """Every period end inside ``window``, plus the most recent elapsed one."""
    step = _FREQUENCY_MONTHS[definition.frequency]
    months = tuple(month for month in range(1, 13) if month % step == 0)
    candidates = [
        month_end(year, month)
        for year in range(window.start.year - 2, window.end.year + 1)
        for month in months
    ]
    offered = {candidate for candidate in candidates if window.start <= candidate <= window.end}
    # Floor: the most recent ELAPSED period end is always offered, however far
    # outside the window it falls. An annual return's only elapsed anchor is up
    # to twelve months old and is still the one the bank owes; dropping it
    # because the lookback is shorter than the cadence would hide an obligation
    # rather than bound a list. This is the pre-2026-09-19 behaviour, kept.
    elapsed = [candidate for candidate in candidates if candidate < window.as_of]
    if elapsed:
        offered.add(elapsed[-1])
    return sorted(offered)


def anchor_dates(definition: ReturnDefinition, window: AnchorWindow) -> list[date]:
    """Every reporting date this return reports on inside ``window``, oldest first.

    Purely a function of the registry entry and the calendar — no database, no
    tenant, no ingestion. Event-driven returns (the LRT corporate packs) have no
    periodic cycle and return no anchors: expanding their nominal frequency
    would fabricate obligations that do not exist.
    """
    if definition.event_driven:
        return []
    if definition.frequency == "daily":
        return _daily_anchors(window)
    if definition.frequency == "weekly":
        return _weekly_anchors(window)
    return _period_end_anchors(definition, window)


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
