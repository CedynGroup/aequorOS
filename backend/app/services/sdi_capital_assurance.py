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
                CanonicalPositionSnapshot.withdrawn_at.is_(None),
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
            CanonicalPositionSnapshot.withdrawn_at.is_(None),
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
    """The latest ingested capital_structure generation — the SAME reader the
    ratio and the checks use, so the reconciliation cannot be run against a
    different generation than the CAR it reconciles."""
    return sdi_capital.latest_capital_structure_rows(db, ctx, bank, as_of)


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
                CanonicalGlAccount.withdrawn_at.is_(None),
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
    db: Session,
    ctx: TenantContext,
    bank: Bank,
    as_of: date,
    *,
    summary: sdi_capital.SdiCapitalSummary | None = None,
) -> CapitalHistoryPoint:
    if summary is None:
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
    # A figure resting on an unconfirmed regulatory input is PROVISIONAL: a
    # pending risk weight, an unconfirmed capital floor, an ungoverned bucket
    # taxonomy, an ungoverned RISK-CLASS COMPOSITION, or exposure excluded from
    # RWA because it carries no ingested currency conversion (which understates
    # RWA and so overstates the ratio).
    #
    # The FLOOR's own confirmation status counts (WS-K, 2026-08-21): the SDI
    # minimum capital ratio is cited to an ENABLING provision rather than a
    # published figure, so whether it is settled is a control-plane fact, not
    # something this module may assume. Reading the status rather than the value
    # means re-statusing the parameter is all that is needed here.
    #
    # The composition counts for the same reason (forensic audit "DIVERGENCE
    # #1"): which risk classes the ratio charges for was an emergent property of
    # which code paths existed. While it is the platform's documented default
    # rather than an approved scope, the ratio is provisional — a governed
    # ``sdi_rwa_composition`` row settles it without touching this module.
    provisional = bool(
        summary.pending_parameters
        or summary.car_min_confirmation != "confirmed"
        or not summary.taxonomy_confirmed
        or not summary.composition_confirmed
        or summary.unconverted_position_count
    )
    assessment_status = (
        "not_computable"
        if summary.car_pct is None
        else "provisional"
        if provisional
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
    current_summary = sdi_capital.compute_sdi_capital_summary(db, ctx, bank, as_of)
    current = _history_point(db, ctx, bank, as_of, summary=current_summary)
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
    ]
    # What the ratio charges for is disclosed on the summary itself
    # (``rwa_scope_note``) whatever the scope is. It becomes a FILING blocker only
    # while that scope is the platform's documented default rather than one
    # approved for this institution — an approved credit-only scope is a decision,
    # not an omission.
    if not current_summary.composition_confirmed:
        blockers.append(
            current_summary.rwa_scope_note
            + " Which risk classes the ratio must cover is the platform's documented "
            "default, not a scope approved for this institution. Approve it in the "
            "regulatory-parameter control plane before filing."
        )
    if current.assessment_status == "not_computable":
        blockers.append("Capital adequacy is not computable for this reporting date.")
    if current_summary.pending_parameters:
        blockers.append("One or more simplified risk weights remain pending confirmation.")
    if current_summary.car_min_confirmation != "confirmed":
        blockers.append(
            "The minimum capital adequacy ratio this institution is measured against is "
            "not yet confirmed against a published regulatory instrument, so whether "
            f"the {current_summary.car_min_pct}% floor is met is not a filing "
            "conclusion. Confirm the parameter in the control plane."
        )
    if not current_summary.taxonomy_confirmed:
        blockers.append(
            "The mapping from product type to risk-weight band is the platform's "
            "documented default, not a mapping approved for this institution. Approve "
            "it in the regulatory-parameter control plane before filing."
        )
    if current_summary.unconverted_position_count:
        blockers.append(
            f"{current_summary.unconverted_position_count} asset position(s) in "
            + ", ".join(current_summary.unconverted_currencies)
            + " have no converted balance, so they are left out of risk-weighted "
            "assets. Supply the converted balances — the ratio is overstated until "
            "they are included."
        )
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