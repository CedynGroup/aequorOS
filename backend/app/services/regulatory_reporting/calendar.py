"""Reporting-obligation calendar (docs/regulatory_reporting.md §5, ``calendar.py``).

For every registry entry: the currently-due reporting date plus the upcoming
reporting dates inside the horizon, each with its deadline-rule due date, the
current non-superseded solo package covering it, and a RAG grade —
``overdue`` (deadline passed without a submitted/acknowledged package),
``due_soon`` (deadline within the warning window), else ``on_track``.

Downtime semantics (BoG Notice BG/FMD/2026/07): a package submitted via the
email fallback is NOT complete until re-uploaded through ORASS, so a
``submitted`` package with ``pending_orass_reupload`` still set does not
satisfy its obligation for RAG purposes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.models import (
    RegulatoryPackage,
    RegulatoryReportingSettings,
    RegulatorySubmissionEvent,
)
from app.schemas.regulatory_reporting import (
    ReportingObligationListRead,
    ReportingObligationRead,
    ReturnAnchorListRead,
    ReturnAnchorRead,
)
from app.services.regulatory_reporting.anchors import (
    anchor_dates,
    horizon_end_for,
    snapshot_coverage,
)
from app.services.regulatory_reporting.common import get_bank_or_404
from app.services.regulatory_reporting.eligibility import resolve_eligibility
from app.services.regulatory_reporting.registry import (
    ReturnDefinition,
    get_definition,
    monthly_day,
)

DUE_SOON_DAYS = 7
_COMPLETED_STATUSES = ("submitted", "acknowledged")

type _PackageKey = tuple[str, date]


@dataclass(frozen=True)
class _PackageSummary:
    """The only package fields an obligation/anchor row renders."""

    id: UUID
    status: str
    version: int


def _calendar_package_state(
    db: Session,
    ctx: TenantContext,
    bank_id: str,
    schedule: dict[str, list[date]],
) -> tuple[dict[_PackageKey, _PackageSummary], set[UUID]]:
    """Load current solo packages and pending ORASS flags in at most two queries.

    Calendar size is driven by regulator anchors (hundreds of obligations for a
    12-month horizon), so neither package nor submission-event reads may live in
    the obligation loop.  The first query projects only the fields the board
    needs; the optional second query reads every relevant submitted-event chain
    once and applies the same "latest submitted event wins" rule as the package
    workflow.
    """
    scheduled_keys = {
        (return_code, reporting_date)
        for return_code, reporting_dates in schedule.items()
        for reporting_date in reporting_dates
    }
    if not scheduled_keys:
        return {}, set()

    reporting_dates = [key[1] for key in scheduled_keys]
    rows = db.execute(
        select(
            RegulatoryPackage.id,
            RegulatoryPackage.return_code,
            RegulatoryPackage.reporting_date,
            RegulatoryPackage.status,
            RegulatoryPackage.version,
        ).where(
            RegulatoryPackage.organization_id == ctx.organization_id,
            RegulatoryPackage.bank_id == bank_id,
            RegulatoryPackage.return_code.in_(tuple(schedule)),
            RegulatoryPackage.reporting_date >= min(reporting_dates),
            RegulatoryPackage.reporting_date <= max(reporting_dates),
            # The obligation board enumerates one solo row per anchor. Solo and
            # consolidated package version chains are independent and must not
            # compete for that row.
            RegulatoryPackage.basis == "solo",
            RegulatoryPackage.status != "superseded",
        )
    ).all()
    packages = {
        (row.return_code, row.reporting_date): _PackageSummary(
            id=row.id,
            status=row.status,
            version=row.version,
        )
        for row in rows
        if (row.return_code, row.reporting_date) in scheduled_keys
    }

    submitted_ids = [package.id for package in packages.values() if package.status == "submitted"]
    if not submitted_ids:
        return packages, set()

    pending_by_package: dict[UUID, bool] = {}
    event_rows = db.execute(
        select(
            RegulatorySubmissionEvent.package_id,
            RegulatorySubmissionEvent.detail,
        )
        .where(
            RegulatorySubmissionEvent.organization_id == ctx.organization_id,
            RegulatorySubmissionEvent.package_id.in_(submitted_ids),
            RegulatorySubmissionEvent.event == "submitted",
        )
        .order_by(
            RegulatorySubmissionEvent.package_id,
            RegulatorySubmissionEvent.occurred_at,
            RegulatorySubmissionEvent.id,
        )
    ).all()
    for row in event_rows:
        pending_by_package[row.package_id] = bool(row.detail.get("pending_orass_reupload"))
    return packages, {
        package_id for package_id, is_pending in pending_by_package.items() if is_pending
    }


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
    horizon_end = horizon_end_for(today, horizon_months)
    overrides = _deadline_overrides(db, ctx, bank.id)
    # Return eligibility resolves through the SINGLE authority (audit ARCH-8,
    # ``eligibility.py``) — the same object ``generation.generate_package``
    # gates on, so the calendar and the package-mint site cannot disagree about
    # what this institution may file. SDI scoping (docs/sdi.md §6.2) is one of
    # its dimensions: every return registered so far is a bank/BoG return, so a
    # savings-&-loans tenant resolves to an empty calendar until the SDI/ORASS
    # return pack lands. That is BoG's deferral, not a bug, and it is stated in
    # words on ``ReportingObligationListRead.coverage_note`` below.
    eligibility = resolve_eligibility(db, ctx, bank, as_of=today)

    obligations: list[ReportingObligationRead] = []
    # Anchors come from the registry through the SINGLE authority the Returns
    # workspace also consumes (``anchors.anchor_dates``), so the calendar and
    # the generate screen cannot offer different reporting dates — the same
    # one-authority rule eligibility follows. Event-driven returns yield no
    # anchors there; their packages still appear in the package list/history.
    schedule = {
        definition.code: anchor_dates(definition, today, horizon_end)
        for definition in eligibility.eligible_definitions()
    }
    # Whether the bank has a computed position AS OF each anchor, resolved in ONE
    # pass across every return rather than per definition (33 returns for a
    # universal bank). An anchor with no snapshot is still a real obligation —
    # it is BoG's date, not ours — so it is listed and marked, never hidden.
    coverage = snapshot_coverage(
        db, ctx, bank, sorted({date_ for dates in schedule.values() for date_ in dates})
    )
    packages, pending_reuploads = _calendar_package_state(db, ctx, bank.id, schedule)
    for definition in eligibility.eligible_definitions():
        for reporting_date in schedule[definition.code]:
            due_date = _due_date(definition, reporting_date, overrides)
            package = packages.get((definition.code, reporting_date))
            pending_reupload = package is not None and package.id in pending_reuploads
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
                    data_status=(
                        "computed" if coverage[reporting_date].covered else "awaiting_data"
                    ),
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
        # The note the eligibility authority has always been able to write, now
        # carried on the payload (audit 2026-08-22 D-20). It is None whenever the
        # institution has an eligible return set, so this adds a sentence exactly
        # where a reader would otherwise see an unexplained empty calendar.
        coverage_note=eligibility.coverage_note(),
    )


def list_return_anchors(  # noqa: PLR0913 - tenant + return + horizon + injectable clock
    db: Session,
    ctx: TenantContext,
    bank_id: str,
    return_code: str,
    horizon_months: int = 3,
    *,
    as_of: date | None = None,
) -> ReturnAnchorListRead:
    """The reporting dates ONE return reports on, with data + package state.

    This is what the Returns workspace selects a reporting date from. It used to
    select from ``bank_reporting_periods`` — the snapshots ingestion happens to
    have produced — which made a BoG deadline invisible whenever the bank had
    not yet ingested a book for it, and made the weekly returns effectively
    unfileable (``anchors`` module docstring has the measurement).

    An anchor with ``data_status='awaiting_data'`` is still listed. That is the
    point: the obligation is BoG's and its deadline runs regardless, so the
    honest surface shows the date and says nothing has been computed for it —
    it does not omit the date and it does not silently offer an earlier book.
    """
    bank = get_bank_or_404(db, ctx, bank_id)
    today = as_of or date.today()
    definition = get_definition(return_code)
    if definition is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Return {return_code!r} is not registered.",
        )

    eligibility = resolve_eligibility(db, ctx, bank, as_of=today)
    decision = eligibility.decide(definition, reporting_date=today)
    if not decision.eligible:
        return ReturnAnchorListRead(
            bank_id=bank.id,
            return_code=definition.code,
            frequency=definition.frequency,
            as_of=today,
            horizon_months=horizon_months,
            anchors=[],
            ineligible_reason=" ".join(decision.blocking_reasons),
        )

    horizon_end = horizon_end_for(today, horizon_months)
    reporting_dates = anchor_dates(definition, today, horizon_end)
    coverage = snapshot_coverage(db, ctx, bank, reporting_dates)
    overrides = _deadline_overrides(db, ctx, bank.id)
    packages, pending_reuploads = _calendar_package_state(
        db, ctx, bank.id, {definition.code: reporting_dates}
    )

    anchors: list[ReturnAnchorRead] = []
    for reporting_date in reporting_dates:
        due_date = _due_date(definition, reporting_date, overrides)
        package = packages.get((definition.code, reporting_date))
        pending_reupload = package is not None and package.id in pending_reuploads
        covered = coverage[reporting_date]
        anchors.append(
            ReturnAnchorRead(
                reporting_date=reporting_date,
                due_date=due_date,
                due_time=definition.due_time,
                data_status="computed" if covered.covered else "awaiting_data",
                nearest_computed_before=None if covered.covered else covered.nearest_before,
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
    anchors.sort(key=lambda item: item.reporting_date, reverse=True)
    return ReturnAnchorListRead(
        bank_id=bank.id,
        return_code=definition.code,
        frequency=definition.frequency,
        as_of=today,
        horizon_months=horizon_months,
        anchors=anchors,
    )
