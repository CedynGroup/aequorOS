"""Shared tenant-scoped lookups and read builders for the reporting hub."""

from __future__ import annotations

from datetime import date
from typing import Any, Literal
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.domain.ingestion.constants import INCLUDED_VALIDATION_STATUSES
from app.models import Bank, BankReportingPeriod, RegulatoryPackage, RegulatoryPackageApproval
from app.models.canonical import CanonicalGlAccount, CanonicalPositionSnapshot
from app.schemas.regulatory_reporting import (
    DeclaredMethodologyRead,
    PackageApprovalRead,
    PackageSourceRunRead,
    RegulatoryPackageRead,
    RegulatoryPackageSummaryRead,
    ValidationReportRead,
)


def require_actor(ctx: TenantContext) -> UUID:
    if ctx.actor_user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="X-User-Id header is required."
        )
    return ctx.actor_user_id


def get_bank_or_404(db: Session, ctx: TenantContext, bank_id: str) -> Bank:
    bank = db.scalar(
        select(Bank).where(Bank.id == bank_id, Bank.organization_id == ctx.organization_id)
    )
    if bank is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bank not found.")
    return bank


#: Human wording for each cadence, used in the refusal below so the message
#: explains WHY a particular date is required rather than only that it is.
_CADENCE_WORDING = {
    "daily": "reports the position at the close of one business day",
    "weekly": "reports the position at the weekly close",
    "monthly": "reports the position at month end",
    "quarterly": "reports the position at quarter end",
    "semiannual": "reports the position at the half-year end",
    "annual": "reports the position at year end",
}


def get_snapshot_for_reporting_date(  # noqa: PLR0913 - lookup keys plus the two
    # descriptors the refusal message needs to explain WHY this date is required
    db: Session,
    ctx: TenantContext,
    bank: Bank,
    reporting_date: date,
    *,
    return_code: str | None = None,
    frequency: str | None = None,
) -> BankReportingPeriod:
    """The computed fact snapshot AS OF ``reporting_date`` — exact, or refuse.

    A return reports the institution's position on the regulator's reporting
    date. The figures must therefore be the figures as of THAT date: a
    Friday-close weekly return cannot be assembled from a month-end book, and a
    daily return cannot be assembled from last month's. So the match is exact
    for every cadence, and a missing snapshot is refused by name rather than
    filled from the nearest earlier one.

    Until 2026-08-23 the daily cadence took the "latest period ending on or
    before" branch instead. Nothing surfaced the substitution, so a daily return
    generated against a bank on a monthly ingestion cadence would have carried a
    month-old book as that day's position, with the stale date visible only
    inside the snapshot. That is the fail-open shape this codebase rejects
    everywhere else; the refusal below replaces it.

    Raises 409 (not 404): the return is registered and the reporting date is a
    real BoG anchor — what is absent is computed state, which is the same
    conflict ``no_baseline_run`` reports one step later.
    """
    period = db.scalar(
        select(BankReportingPeriod).where(
            BankReportingPeriod.organization_id == ctx.organization_id,
            BankReportingPeriod.bank_id == bank.id,
            BankReportingPeriod.period_end == reporting_date,
        )
    )
    if period is not None:
        return period

    nearest = db.scalar(
        select(BankReportingPeriod.period_end)
        .where(
            BankReportingPeriod.organization_id == ctx.organization_id,
            BankReportingPeriod.bank_id == bank.id,
            BankReportingPeriod.period_end < reporting_date,
        )
        .order_by(BankReportingPeriod.period_end.desc())
        .limit(1)
    )
    subject = f"{return_code} " if return_code else "This return "
    cadence = _CADENCE_WORDING.get(frequency or "", "reports the position at the reporting date")
    if nearest is None:
        context = "No financial data has been ingested for this institution yet."
    else:
        context = (
            f"The most recent computed position is {nearest.isoformat()}, which is not a "
            "substitute — an earlier book is not this reporting date's position."
        )
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={
            "error_code": "no_computed_position",
            "message": (
                f"{subject}{cadence}, so it needs the institution's position as of "
                f"{reporting_date.isoformat()}. Nothing has been computed for that date. "
                f"{context} Ingest the book as of {reporting_date.isoformat()} through the "
                "Data Engine, then generate the return."
            ),
        },
    )


def get_package_or_404(
    db: Session, ctx: TenantContext, bank_id: str, package_id: UUID
) -> RegulatoryPackage:
    package = db.scalar(
        select(RegulatoryPackage).where(
            RegulatoryPackage.id == package_id,
            RegulatoryPackage.organization_id == ctx.organization_id,
            RegulatoryPackage.bank_id == bank_id,
        )
    )
    if package is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Regulatory package not found."
        )
    return package


def validation_passed(package: RegulatoryPackage) -> bool | None:
    report = package.validation_report
    if report is None:
        return None
    return bool(report.get("passed"))


def read_summary(package: RegulatoryPackage) -> RegulatoryPackageSummaryRead:
    return RegulatoryPackageSummaryRead(
        id=package.id,
        bank_id=package.bank_id,
        return_family=package.return_family,  # type: ignore[arg-type]
        return_code=package.return_code,
        reporting_date=package.reporting_date,
        frequency=package.frequency,  # type: ignore[arg-type]
        basis=package.basis,  # type: ignore[arg-type]
        status=package.status,  # type: ignore[arg-type]
        version=package.version,
        supersedes_id=package.supersedes_id,
        generated_by=package.generated_by,
        generated_at=package.generated_at,
        validation_passed=validation_passed(package),
        notes=package.notes,
        attestation_state=package.attestation_state,
        submission_revision=package.submission_revision,
        snapshot_sha256=package.snapshot_sha256,
        regulator_comments=package.regulator_comments,
        created_at=package.created_at,
        updated_at=package.updated_at,
    )


def read_package(db: Session, package: RegulatoryPackage) -> RegulatoryPackageRead:
    approvals = list(
        db.scalars(
            select(RegulatoryPackageApproval)
            .where(
                RegulatoryPackageApproval.package_id == package.id,
                RegulatoryPackageApproval.organization_id == package.organization_id,
            )
            .order_by(
                RegulatoryPackageApproval.occurred_at,
                RegulatoryPackageApproval.id,
            )
        )
    )
    report_payload: dict[str, Any] | None = package.validation_report
    return RegulatoryPackageRead(
        **read_summary(package).model_dump(),
        snapshot=package.snapshot,
        source_runs=[PackageSourceRunRead(**entry) for entry in package.source_runs],
        validation_report=(
            ValidationReportRead(**report_payload) if report_payload is not None else None
        ),
        approvals=[PackageApprovalRead.model_validate(row) for row in approvals],
        declared_methodologies=declared_methodologies(package.snapshot),
    )


def declared_methodologies(snapshot: Any) -> list[DeclaredMethodologyRead]:
    """The CF-1 divergence disclosure, read off the sealed snapshot.

    Audit 2026-08-22 D-20: the generator wrote ``declared_methodologies`` into
    ``snapshot["provenance"]`` and nothing ever read it, so the answer to "which
    ``lcr_pct`` does this return mean?" existed in the record and on no surface.
    Reading it here — from the SEALED snapshot, never from the live registry —
    is what makes it a disclosure of what was filed rather than of what the code
    believes today.

    Tolerant by construction: a package generated before the field existed, or
    one whose provenance block is shaped differently, yields an empty list
    rather than failing a package read.
    """
    if not isinstance(snapshot, dict):
        return []
    provenance = snapshot.get("provenance")
    if not isinstance(provenance, dict):
        return []
    declared = provenance.get("declared_methodologies")
    if not isinstance(declared, list):
        return []
    notes: list[DeclaredMethodologyRead] = []
    for entry in declared:
        if not isinstance(entry, dict) or "metric_id" not in entry:
            continue
        notes.append(DeclaredMethodologyRead.model_validate(entry))
    return notes


# ---------------------------------------------------------------------------
# The validated book, stated (forensic re-audit 2026-08-22, D-4)
# ---------------------------------------------------------------------------

#: The canonical entities the filed-return resolvers admit BY VALIDATION STATUS
#: (``INCLUDED_VALIDATION_STATUSES``): position snapshots and general-ledger
#: accounts. Positions, counterparties and products carry the column too, but
#: the resolvers join them without a status predicate — exactly as
#: ``fact_derivation`` joins them — so they are deliberately NOT counted here. A
#: row this function reports is a row the return genuinely did not read.
UNVALIDATED_ENTITY_LABELS: dict[str, str] = {
    "canonical_position_snapshots": "position snapshot",
    "canonical_gl_accounts": "general-ledger account",
}

#: How a return's resolvers bound the reporting date. See ``unvalidated_book_rows``.
type DateBound = Literal["on", "on_or_before"]

#: Rule id for the unvalidated-book disclosure, shared by every generator that
#: reads the canonical book directly (``bog_form``, ``large_exposures``,
#: ``lmt``). Named for the CONDITION, not for a family: the same exclusion,
#: measured the same way, must reach an approver under one id whichever return
#: raised it — a ``bog_form.``-prefixed rule on an LMT validation report would
#: read as a different control.
UNVALIDATED_BOOK_RULE = "reporting.unvalidated_canonical_rows"


def unvalidated_book_rows(
    db: Session, ctx: TenantContext, bank: Bank, *, as_of: date, bound: DateBound = "on_or_before"
) -> dict[str, dict[str, int]]:
    """Current-generation canonical rows at ``as_of`` that did NOT pass validation.

    D-4 closed the divergence — the BoG return layer now excludes exactly what
    the calculation engines exclude. Excluding silently is the other half of the
    same defect: a return compiled off a book carrying ``pending``, ``error`` or
    ``blocked`` rows understates, and nothing on the artifact says so. This is
    the measurement that lets the return say it.

    Counted per entity per status over the current generation only, so a
    superseded or withdrawn row — already retired by other means — is never
    reported as a validation problem. An empty mapping means the book behind
    this return is fully validated; it is never inferred from absence of data,
    because a bank with no canonical rows also has no unvalidated ones and the
    return's own ``input_required`` lines say that instead.

    ``bound`` must match the date rule of the resolvers that fed the return, or
    the disclosure describes rows the return never looked at — a false statement
    on a filed artifact, which is the same class of defect as the silence it
    replaces. ``"on_or_before"`` is the BoG-form rule (``positions.sum`` reads
    the latest snapshot on or before period end); ``"on"`` is the Large
    Exposures / LMT rule (``le_generation._load_canonical_rows`` matches the
    period end exactly).
    """
    counts: dict[str, dict[str, int]] = {}
    for model in (CanonicalPositionSnapshot, CanonicalGlAccount):
        rows = db.execute(
            select(model.validation_status, func.count())
            .where(
                model.organization_id == ctx.organization_id,
                model.bank_id == bank.id,
                model.as_of_date == as_of if bound == "on" else model.as_of_date <= as_of,
                model.superseded_by.is_(None),
                model.withdrawn_at.is_(None),
                # The INVERSE of the admitted scope, on purpose: this query
                # counts what every other query in this package refuses. It is
                # the same one constant, read the other way round, so the
                # disclosure can never describe a scope the resolvers do not use.
                model.validation_status.not_in(INCLUDED_VALIDATION_STATUSES),
            )
            .group_by(model.validation_status)
        ).all()
        by_status = {str(status_value): int(count) for status_value, count in rows if count}
        if by_status:
            counts[model.__tablename__] = by_status
    return counts


def unvalidated_book_detail(
    counts: dict[str, dict[str, int]], *, as_of: date, bound: DateBound = "on_or_before"
) -> str | None:
    """The disclosure sentence for :func:`unvalidated_book_rows`, or ``None``.

    ``None`` when the book is fully validated — the finding is then not emitted
    at all, rather than emitted as an all-clear, because "nothing was excluded"
    is already what every unqualified figure on the return means.

    ``bound`` must be the SAME value passed to :func:`unvalidated_book_rows`: it
    both selects the rows and states the date rule in the sentence, so the two
    cannot describe different populations. The fallback clause about an earlier
    passing snapshot is true only under ``"on_or_before"`` — a return that reads
    one exact date has no earlier snapshot to fall back to, and saying otherwise
    would understate the loss to the officer who signs.
    """
    if not counts:
        return None
    parts: list[str] = []
    for table in sorted(counts):
        by_status = counts[table]
        total = sum(by_status.values())
        label = UNVALIDATED_ENTITY_LABELS.get(table, table)
        breakdown = ", ".join(f"{name}: {n}" for name, n in sorted(by_status.items()))
        parts.append(f"{total} {label} row(s) ({breakdown})")
    when = (
        f"dated {as_of.isoformat()}" if bound == "on" else f"dated on or before {as_of.isoformat()}"
    )
    fallback = (
        "; this return reads that reporting date only, so an excluded row is simply absent"
        if bound == "on"
        else "; where a position has an earlier snapshot that did pass, the return reports "
        "that earlier snapshot instead"
    )
    return (
        f"{' and '.join(parts)} {when} are in the current generation of this institution's "
        "canonical book but have NOT passed validation. Every line of this return excludes "
        f"them, exactly as the calculation engines do{fallback}. The figures here are "
        "therefore compiled from the validated book only, and are understated to the extent "
        "those rows are genuine. Validate or withdraw them before filing."
    )


def unvalidated_book_finding(note: str | None) -> list[dict[str, str]]:
    """The generation finding for a disclosure sentence — WARNING, at most one.

    Not an ERROR: the resolvers exclude exactly what the calculation engines
    exclude, so refusing to generate here would refuse a filing that the
    internal ratio the return must agree with computes happily — rebuilding the
    D-4 divergence backwards. Whether an unvalidated backlog should stop a
    filing is a supervisory policy decision and belongs to the institution's own
    signing policy, not to a generator.

    Empty when the book is fully validated: absence of the finding IS the
    all-clear, and a separate "nothing was excluded" line would be the blanket
    reassurance P0-14 removed from the movement rule.
    """
    if not note:
        return []
    return [{"rule": UNVALIDATED_BOOK_RULE, "severity": "WARNING", "detail": note}]
