"""Evidence-aware assurance view over the SDI simplified capital calculation.

This is deliberately a read model. It derives only from ingested canonical data
and identifies missing reconciliation, provision, methodology, and return
evidence as blockers rather than filling those gaps with assumptions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.domain.stress.appendix_ii import _STATUTORY
from app.models import (
    Bank,
    CanonicalGlAccount,
    CanonicalPosition,
    CanonicalPositionSnapshot,
    CanonicalReferenceRow,
)
from app.services import loan_classification, sdi_capital, sdi_capital_checks

_ZERO = Decimal("0")
_INCLUDED = ("accepted", "warning")


@dataclass(frozen=True)
class CapitalHistoryPoint:
    as_of: date
    net_own_funds_ghs: Decimal
    total_rwa_ghs: Decimal
    car_pct: Decimal | None
    capital_headroom_ghs: Decimal | None
    npl_exposure_ghs: Decimal
    npl_ratio: Decimal
    required_provision_ghs: Decimal
    actual_provision_ghs: Decimal | None
    provision_coverage_pct: Decimal | None
    assessment_status: str


@dataclass(frozen=True)
class CapitalAssurance:
    as_of: date
    current: CapitalHistoryPoint
    history: list[CapitalHistoryPoint] = field(default_factory=list)
    mapped_gl_capital_ghs: Decimal | None = None
    capital_to_gl_difference_ghs: Decimal | None = None
    gl_reconciliation_status: str = "not_mapped"
    reserve_change_ghs: Decimal | None = None
    filing_status: str = "blocked"
    filing_blockers: list[str] = field(default_factory=list)


def _decimal(value: object) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _available_dates(
    db: Session, ctx: TenantContext, bank: Bank, as_of: date, limit: int
) -> list[date]:
    position_dates = set(
        db.scalars(
            select(CanonicalPositionSnapshot.as_of_date)
            .where(
                CanonicalPositionSnapshot.organization_id == ctx.organization_id,
                CanonicalPositionSnapshot.bank_id == bank.id,
                CanonicalPositionSnapshot.as_of_date <= as_of,
                CanonicalPositionSnapshot.superseded_by.is_(None),
                CanonicalPositionSnapshot.validation_status.in_(_INCLUDED),
            )
            .distinct()
        )
    )
    capital_dates = set(
        db.scalars(
            select(CanonicalReferenceRow.as_of_date)
            .where(
                CanonicalReferenceRow.organization_id == ctx.organization_id,
                CanonicalReferenceRow.bank_id == bank.id,
                CanonicalReferenceRow.dataset_kind == "capital_structure",
                CanonicalReferenceRow.as_of_date <= as_of,
            )
            .distinct()
        )
    )
    return sorted(position_dates & capital_dates)[-limit:]


def _actual_provision(
    db: Session, ctx: TenantContext, bank: Bank, as_of: date
) -> Decimal | None:
    rows = db.execute(
        select(CanonicalPositionSnapshot)
        .join(CanonicalPosition, CanonicalPositionSnapshot.position_id == CanonicalPosition.id)
        .where(
            CanonicalPositionSnapshot.organization_id == ctx.organization_id,
            CanonicalPositionSnapshot.bank_id == bank.id,
            CanonicalPositionSnapshot.as_of_date == as_of,
            CanonicalPositionSnapshot.superseded_by.is_(None),
            CanonicalPositionSnapshot.validation_status.in_(_INCLUDED),
            CanonicalPosition.position_type == "LOAN",
        )
    ).scalars()
    values = [
        value
        for row in rows
        if (value := _decimal((row.attributes or {}).get("ecl_provision_ghs"))) is not None
    ]
    return sum(values, _ZERO) if values else None


def _capital_rows(
    db: Session, ctx: TenantContext, bank: Bank, as_of: date
) -> list[CanonicalReferenceRow]:
    scope = (
        CanonicalReferenceRow.organization_id == ctx.organization_id,
        CanonicalReferenceRow.bank_id == bank.id,
        CanonicalReferenceRow.dataset_kind == "capital_structure",
    )
    latest = db.scalar(
        select(CanonicalReferenceRow.as_of_date)
        .where(*scope, CanonicalReferenceRow.as_of_date <= as_of)
        .order_by(CanonicalReferenceRow.as_of_date.desc())
        .limit(1)
    )
    if latest is None:
        return []
    batch = db.scalar(
        select(CanonicalReferenceRow.ingestion_batch_id)
        .where(*scope, CanonicalReferenceRow.as_of_date == latest)
        .order_by(CanonicalReferenceRow.created_at.desc(), CanonicalReferenceRow.id.desc())
        .limit(1)
    )
    return list(
        db.scalars(
            select(CanonicalReferenceRow).where(
                *scope,
                CanonicalReferenceRow.as_of_date == latest,
                CanonicalReferenceRow.ingestion_batch_id == batch,
            )
        )
    )


def _mapped_gl_capital(
    db: Session, ctx: TenantContext, bank: Bank, as_of: date
) -> tuple[Decimal | None, str]:
    """Return mapped GL capital only when every capital component names its GL row.

    ``gl_account_code`` is an ingestion mapping, not a name-based heuristic. An
    absent or unresolved code makes the reconciliation unavailable by design.
    """
    rows = _capital_rows(db, ctx, bank, as_of)
    codes = [str((row.payload or {}).get("gl_account_code", "")).strip() for row in rows]
    if not rows or not all(codes) or len(codes) != len(set(codes)):
        return None, "not_mapped"
    balances = {
        row.account_code: row.balance or _ZERO
        for row in db.scalars(
            select(CanonicalGlAccount).where(
                CanonicalGlAccount.organization_id == ctx.organization_id,
                CanonicalGlAccount.bank_id == bank.id,
                CanonicalGlAccount.as_of_date == as_of,
                CanonicalGlAccount.superseded_by.is_(None),
                CanonicalGlAccount.account_code.in_(codes),
            )
        )
    }
    if len(balances) != len(codes):
        return None, "mapping_incomplete"
    return sum((balances[code] for code in codes), _ZERO), "mapped"


def _reserve(components: dict[str, Decimal]) -> Decimal:
    return sum((components.get(component, _ZERO) for component in _STATUTORY), _ZERO)


def _history_point(
    db: Session, ctx: TenantContext, bank: Bank, as_of: date
) -> CapitalHistoryPoint:
    summary = sdi_capital.compute_sdi_capital_summary(db, ctx, bank, as_of)
    classification = loan_classification.classify_loan_book(db, ctx, bank, as_of).result
    actual_provision = _actual_provision(db, ctx, bank, as_of)
    headroom = (
        summary.net_own_funds_ghs - summary.total_rwa_ghs * summary.car_min_pct / Decimal("100")
        if summary.car_pct is not None
        else None
    )
    coverage = (
        actual_provision * Decimal("100") / classification.npl_exposure_ghs
        if actual_provision is not None and classification.npl_exposure_ghs > _ZERO
        else None
    )
    assessment_status = (
        "not_computable"
        if summary.car_pct is None
        else "provisional"
        if summary.pending_parameters
        else "review_required"
    )
    return CapitalHistoryPoint(
        as_of=as_of,
        net_own_funds_ghs=summary.net_own_funds_ghs,
        total_rwa_ghs=summary.total_rwa_ghs,
        car_pct=summary.car_pct,
        capital_headroom_ghs=headroom,
        npl_exposure_ghs=classification.npl_exposure_ghs,
        npl_ratio=classification.npl_ratio,
        required_provision_ghs=classification.total_provision_required_ghs,
        actual_provision_ghs=actual_provision,
        provision_coverage_pct=coverage,
        assessment_status=assessment_status,
    )


def get_sdi_capital_assurance(
    db: Session,
    ctx: TenantContext,
    bank: Bank,
    as_of: date,
    *,
    history_limit: int = 12,
) -> CapitalAssurance:
    """Return historic controls plus explicit evidence blockers for an SDI review."""
    history_dates = _available_dates(db, ctx, bank, as_of, history_limit)
    current = _history_point(db, ctx, bank, as_of)
    history = [_history_point(db, ctx, bank, point_date) for point_date in history_dates]
    if not history or history[-1].as_of != as_of:
        history.append(current)

    mapped_gl_capital, reconciliation_status = _mapped_gl_capital(db, ctx, bank, as_of)
    capital_difference = (
        current.net_own_funds_ghs - mapped_gl_capital if mapped_gl_capital is not None else None
    )
    components = sdi_capital_checks.capital_components(db, ctx, bank, as_of)
    current_reserve = _reserve(components)
    previous_reserve = None
    if len(history) > 1:
        prior_components = sdi_capital_checks.capital_components(db, ctx, bank, history[-2].as_of)
        previous_reserve = _reserve(prior_components)

    blockers = [
        "The prescribed BoG SDI capital-adequacy return template is not registered.",
        "The simplified SDI RWA methodology requires regulator-confirmed scope before filing.",
    ]
    if current.assessment_status == "not_computable":
        blockers.append("Capital adequacy is not computable for this reporting date.")
    if current.assessment_status == "provisional":
        blockers.append("One or more simplified risk weights remain pending confirmation.")
    if current.actual_provision_ghs is None:
        blockers.append("Booked ECL provision balances were not supplied on the loan snapshots.")
    if mapped_gl_capital is None:
        blockers.append(
            "Capital components need explicit gl_account_code mappings for GL reconciliation."
        )
    blockers.append(
        "Statutory-reserve transfer compliance requires mapped period profit and transfer evidence."
    )
    return CapitalAssurance(
        as_of=as_of,
        current=current,
        history=history,
        mapped_gl_capital_ghs=mapped_gl_capital,
        capital_to_gl_difference_ghs=capital_difference,
        gl_reconciliation_status=reconciliation_status,
        reserve_change_ghs=(
            current_reserve - previous_reserve if previous_reserve is not None else None
        ),
        filing_blockers=blockers,
    )