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
from datetime import date
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.models import RegulatoryPackage
from app.schemas.regulatory_reporting import (
    ReportingObligationListRead,
    ReportingObligationRead,
)
from app.services.regulatory_reporting.common import get_bank_or_404
from app.services.regulatory_reporting.registry import REGISTRY, ReturnDefinition
from app.services.regulatory_reporting.workflow import has_pending_orass_reupload

DUE_SOON_DAYS = 7
_COMPLETED_STATUSES = ("submitted", "acknowledged")
_FREQUENCY_MONTHS = {"monthly": 1, "quarterly": 3, "semiannual": 6, "annual": 12}


def _month_end(year: int, month: int) -> date:
    return date(year, month, monthrange(year, month)[1])


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


def list_obligations(
    db: Session,
    ctx: TenantContext,
    bank_id: UUID,
    horizon_months: int = 3,
    *,
    as_of: date | None = None,
) -> ReportingObligationListRead:
    bank = get_bank_or_404(db, ctx, bank_id)
    today = as_of or date.today()
    total_months = today.year * 12 + (today.month - 1) + horizon_months
    horizon_end = _month_end(total_months // 12, total_months % 12 + 1)

    obligations: list[ReportingObligationRead] = []
    for definition in REGISTRY.values():
        for reporting_date in _reporting_dates(definition, today, horizon_end):
            due_date = definition.deadline_rule(reporting_date)
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
