"""Reporting-obligation calendar (docs/regulatory_reporting.md §5, ``calendar.py``).

For every registry entry: the currently-due reporting date plus the upcoming
reporting dates inside the horizon, each with its deadline-rule due date, the
current (non-superseded) package covering it, and a RAG grade —
``overdue`` (deadline passed without a submitted/acknowledged package),
``due_soon`` (deadline within the warning window), else ``on_track``.

Downtime semantics (BoG Notice BG/FMD/2026/07): a package submitted via the
email fallback is NOT complete until re-uploaded through ORASS, so a
``submitted`` package with ``pending_orass_reupload`` still set does not
satisfy its obligation for RAG purposes.
"""

from __future__ import annotations

from calendar import monthrange
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.models import RegulatoryPackage, RegulatoryReportingSettings
from app.schemas.regulatory_reporting import (
    ReportingObligationListRead,
    ReportingObligationRead,
)
from app.services.institution_types import institution_class as resolve_institution_class
from app.services.regulatory_reporting.common import get_bank_or_404
from app.services.regulatory_reporting.registry import REGISTRY, ReturnDefinition, monthly_day
from app.services.regulatory_reporting.workflow import has_pending_orass_reupload

DUE_SOON_DAYS = 7
_COMPLETED_STATUSES = ("submitted", "acknowledged")
_FREQUENCY_MONTHS = {"monthly": 1, "quarterly": 3, "semiannual": 6, "annual": 12}
# Daily obligations enumerate only the most recent business days (a full year of
# daily rows would swamp the calendar); the window ends at ``as_of``.
_DAILY_WINDOW_BUSINESS_DAYS = 5


def _month_end(year: int, month: int) -> date:
    return date(year, month, monthrange(year, month)[1])


def _daily_reporting_dates(as_of: date) -> list[date]:
    """The most recent ``_DAILY_WINDOW_BUSINESS_DAYS`` business days ending on
    or before ``as_of`` (weekends skipped), oldest first.

    Daily returns do not expand across the horizon like periodic returns — a
    small trailing window keeps the calendar bounded while still surfacing any
    recently-missed daily filings.
    """
    dates: list[date] = []
    cursor = as_of
    while len(dates) < _DAILY_WINDOW_BUSINESS_DAYS:
        if cursor.weekday() < 5:  # noqa: PLR2004 — Mon..Fri
            dates.append(cursor)
        cursor -= timedelta(days=1)
    return sorted(dates)


_WEEKLY_TRAILING_WEEKS = 8


def _weekly_reporting_dates(as_of: date, horizon_end: date) -> list[date]:
    """The last ``_WEEKLY_TRAILING_WEEKS`` weekly anchor dates before ``as_of``
    plus every anchor up to ``horizon_end`` (Friday close by default)."""
    from app.services.regulatory_reporting.bog_forms.catalog import (  # noqa: PLC0415
        WEEKLY_ANCHOR_WEEKDAY,
    )

    delta = (as_of.weekday() - WEEKLY_ANCHOR_WEEKDAY) % 7
    last_anchor = as_of - timedelta(days=delta)
    dates = [last_anchor - timedelta(weeks=w) for w in range(_WEEKLY_TRAILING_WEEKS, 0, -1)]
    cursor = last_anchor
    while cursor <= horizon_end:
        dates.append(cursor)
        cursor += timedelta(weeks=1)
    return dates


def _period_end_months(frequency: str) -> tuple[int, ...]:
    step = _FREQUENCY_MONTHS[frequency]
    return tuple(month for month in range(1, 13) if month % step == 0)


def _reporting_dates(definition: ReturnDefinition, as_of: date, horizon_end: date) -> list[date]:
    """The most recent elapsed period end plus every period end in the horizon."""
    months = _period_end_months(definition.frequency)
    candidates = [
        _month_end(year, month)
        for year in range(as_of.year - 2, horizon_end.year + 1)
        for month in months
    ]
    elapsed = [candidate for candidate in candidates if candidate < as_of]
    upcoming = [candidate for candidate in candidates if as_of <= candidate <= horizon_end]
    selected = ([elapsed[-1]] if elapsed else []) + upcoming
    return selected


def _rag(
    due_date: date,
    as_of: date,
    package_status: str | None,
    *,
    pending_orass_reupload: bool = False,
) -> str:
    if package_status in _COMPLETED_STATUSES and not pending_orass_reupload:
        return "on_track"
    if as_of > due_date:
        return "overdue"
    if (due_date - as_of).days <= DUE_SOON_DAYS:
        return "due_soon"
    return "on_track"


def _deadline_overrides(db: Session, ctx: TenantContext, bank_id: str) -> dict[str, int]:
    """The per-bank ``{return_code: day_of_month}`` deadline overrides, or {}."""
    settings = db.scalar(
        select(RegulatoryReportingSettings).where(
            RegulatoryReportingSettings.organization_id == ctx.organization_id,
            RegulatoryReportingSettings.bank_id == bank_id,
        )
    )
    if settings is None:
        return {}
    return {
        str(code): int(day)
        for code, day in settings.deadline_overrides.items()
        if isinstance(day, int)
    }


def _due_date(
    definition: ReturnDefinition, reporting_date: date, overrides: dict[str, int]
) -> date:
    """The obligation's due date, honouring a per-bank monthly-day override.

    An override replaces the registry's default deadline rule with
    ``monthly_day(day)`` — this is how the BSD2 day-14 / FX-NOP day-10
    placeholders get corrected per bank at onboarding once ORASS confirms the
    real day.
    """
    override_day = overrides.get(definition.code)
    if override_day is not None:
        return monthly_day(override_day)(reporting_date)
    return definition.deadline_rule(reporting_date)


def list_obligations(
    db: Session,
    ctx: TenantContext,
    bank_id: str,
    horizon_months: int = 3,
    *,
    as_of: date | None = None,
) -> ReportingObligationListRead:
    bank = get_bank_or_404(db, ctx, bank_id)
    today = as_of or date.today()
    total_months = today.year * 12 + (today.month - 1) + horizon_months
    horizon_end = _month_end(total_months // 12, total_months % 12 + 1)
    overrides = _deadline_overrides(db, ctx, bank.id)
    # SDI scoping (docs/sdi.md §6.2): the calendar is class-filtered so a tenant
    # sees only the returns its institution class is subject to. Every return
    # registered so far is a bank/BoG return, so a savings-&-loans tenant
    # resolves to an empty calendar until the SDI/ORASS return pack lands
    # (docs/sdi.md §Phase F) — the honest state, not a bug.
    bank_class = resolve_institution_class(db, bank)

    obligations: list[ReportingObligationRead] = []
    for definition in REGISTRY.values():
        if bank_class not in definition.institution_classes:
            continue
        # Event-driven returns (plan W5: the LRT corporate packs) have no
        # periodic reporting cycle — expanding their nominal frequency would
        # fabricate obligations that do not exist. Their packages still
        # appear in the package list/history like any other package.
        if definition.event_driven:
            continue
        if definition.frequency == "daily":
            reporting_dates = _daily_reporting_dates(today)
        elif definition.frequency == "weekly":
            # Weekly BoG returns (BSD1/1A/1B/14/15A/15B): Friday-close reporting
            # dates — a bounded trailing window plus the horizon's Fridays. The
            # Guide fixes the cadence and the 9-day limit, not the weekday
            # (documented convention in bog_forms.catalog).
            reporting_dates = _weekly_reporting_dates(today, horizon_end)
        else:
            reporting_dates = _reporting_dates(definition, today, horizon_end)
        for reporting_date in reporting_dates:
            due_date = _due_date(definition, reporting_date, overrides)
            package = db.scalar(
                select(RegulatoryPackage).where(
                    RegulatoryPackage.organization_id == ctx.organization_id,
                    RegulatoryPackage.bank_id == bank.id,
                    RegulatoryPackage.return_code == definition.code,
                    RegulatoryPackage.reporting_date == reporting_date,
                    RegulatoryPackage.status != "superseded",
                )
            )
            pending_reupload = package is not None and has_pending_orass_reupload(db, package)
            obligations.append(
                ReportingObligationRead(
                    return_code=definition.code,
                    return_family=definition.family,
                    title=definition.title,
                    frequency=definition.frequency,
                    fidelity=definition.fidelity,
                    default_channel=definition.default_channel,
                    reporting_date=reporting_date,
                    due_date=due_date,
                    due_time=definition.due_time,
                    basis="solo",
                    package_id=package.id if package is not None else None,
                    package_status=(
                        package.status if package is not None else None  # type: ignore[arg-type]
                    ),
                    package_version=package.version if package is not None else None,
                    rag=_rag(  # type: ignore[arg-type]
                        due_date,
                        today,
                        package.status if package is not None else None,
                        pending_orass_reupload=pending_reupload,
                    ),
                )
            )
    obligations.sort(key=lambda item: (item.due_date, item.return_code))
    return ReportingObligationListRead(
        bank_id=bank.id,
        as_of=today,
        horizon_months=horizon_months,
        obligations=obligations,
    )
