"""Standing credit-concentration monitor service (credit PR-3).

Loads the credit exposure book (LOAN + INTERBANK_PLACEMENT + SECURITY_HOLDING,
current generation) into the SAME ``ConcentrationExposure`` rows the stress
engine consumes, resolves the capital base for the tenant's regime — Act 930
s.29 Net Own Funds for an SDI, Tier 1 from the current capital-component facts
for a bank — reads the Board limit register, and runs the pure monitor.

The capital base is regime-scoped on purpose (the audit's regime-duplication
rule): an SDI's single-obligor share is measured against NOF, a bank's against
Tier 1. When neither resolves, capital-basis figures are ``None`` and any
capital-basis limit reads "not computable" — never a share against an invented
denominator.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.core.errors import ModuleDataUnavailable
from app.domain.capital.engine import CapitalFact, tier1_capital
from app.domain.credit.concentration_monitor import (
    ConcentrationLimit,
    ConcentrationMonitorResult,
    monitor_concentration,
)
from app.domain.stress.concentration import ConcentrationExposure
from app.models import (
    Bank,
    CanonicalCounterparty,
    CanonicalPosition,
    CanonicalPositionSnapshot,
    CanonicalProduct,
    ParamConcentrationLimit,
)
from app.services import institution_types, sdi_capital
from app.services.live_state import load_current_facts
from app.services.params import get_active_params

_INCLUDED_VALIDATION_STATUSES = ("accepted", "warning")
_CONCENTRATION_POSITION_TYPES = ("LOAN", "INTERBANK_PLACEMENT", "SECURITY_HOLDING")
_ZERO = Decimal("0")


def _dec_or_none(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except ArithmeticError:
        return None


def _group_key(source_reference: str, counterparty: CanonicalCounterparty | None) -> str:
    """Connected-group / single-name identity (le_generation's pattern)."""
    if counterparty is not None:
        if counterparty.group_reference:
            return f"group:{counterparty.group_reference}"
        cp_attributes = counterparty.attributes or {}
        for key in ("group_reference", "group", "parent"):
            value = cp_attributes.get(key)
            if value:
                return f"group:{value}"
        return f"cp:{counterparty.name}"
    return f"pos:{source_reference}"


def load_credit_exposures(
    db: Session, ctx: TenantContext, bank: Bank, as_of: date
) -> list[ConcentrationExposure]:
    """The credit exposure book as concentration rows.

    ``employer`` reads the documented ``attributes.employer`` key (payroll /
    check-off lending; docs/API_INTEGRATION.md §3.4). Unstated dimensions stay
    ``None`` — the monitor discloses coverage instead of grouping unknowns.
    """
    records = db.execute(
        select(
            CanonicalPositionSnapshot, CanonicalPosition, CanonicalCounterparty, CanonicalProduct
        )
        .join(CanonicalPosition, CanonicalPositionSnapshot.position_id == CanonicalPosition.id)
        .outerjoin(
            CanonicalCounterparty,
            CanonicalPositionSnapshot.counterparty_id == CanonicalCounterparty.id,
        )
        .outerjoin(CanonicalProduct, CanonicalPositionSnapshot.product_id == CanonicalProduct.id)
        .where(
            CanonicalPositionSnapshot.organization_id == ctx.organization_id,
            CanonicalPositionSnapshot.bank_id == bank.id,
            CanonicalPositionSnapshot.as_of_date == as_of,
            CanonicalPositionSnapshot.superseded_by.is_(None),
            CanonicalPositionSnapshot.withdrawn_at.is_(None),
            CanonicalPositionSnapshot.validation_status.in_(_INCLUDED_VALIDATION_STATUSES),
            CanonicalPosition.position_type.in_(_CONCENTRATION_POSITION_TYPES),
        )
        .order_by(CanonicalPositionSnapshot.source_reference)
    ).all()
    exposures: list[ConcentrationExposure] = []
    for snapshot, _position, counterparty, product_row in records:
        attributes = snapshot.attributes or {}
        balance_ghs = _dec_or_none(attributes.get("balance_ghs"))
        if balance_ghs is None:
            balance_ghs = Decimal(str(snapshot.balance or _ZERO))
        if balance_ghs <= _ZERO:
            continue
        sector = attributes.get("sector") or attributes.get("industry")
        collateral = attributes.get("collateral_type") or attributes.get("crm_collateral_class")
        employer = attributes.get("employer")
        exposures.append(
            ConcentrationExposure(
                exposure_id=snapshot.source_reference,
                ead=balance_ghs,
                group_key=_group_key(snapshot.source_reference, counterparty),
                sector=str(sector) if sector else None,
                geography=counterparty.country_code if counterparty is not None else None,
                product=product_row.product_code if product_row is not None else None,
                collateral_type=str(collateral) if collateral else None,
                employer=str(employer) if employer else None,
            )
        )
    return exposures


def capital_base(db: Session, ctx: TenantContext, bank: Bank, as_of: date) -> Decimal | None:
    """The regime-scoped capital denominator, or ``None`` when unresolvable."""
    if institution_types.institution_class(db, bank) == "sdi":
        try:
            nof = sdi_capital.net_own_funds(db, ctx, bank, as_of)
        except Exception:  # noqa: BLE001 - an unresolvable base is a real state
            return None
        return nof if nof > _ZERO else None
    try:
        current = load_current_facts(db, ctx, bank, ("capital_component",))
    except ModuleDataUnavailable:
        return None
    capital_facts = [
        CapitalFact(
            fact_group="capital_component",
            category=fact.category,
            amount=Decimal(str(fact.amount)),
            capital_tier=fact.capital_tier,
            is_deduction=fact.is_deduction,
        )
        for fact in current.facts
        if fact.fact_group == "capital_component"
    ]
    if not capital_facts:
        return None
    tier1 = tier1_capital(capital_facts)
    return tier1 if tier1 > _ZERO else None


def active_limits(
    db: Session, ctx: TenantContext, bank: Bank, as_of: date
) -> tuple[ConcentrationLimit, ...]:
    rows = get_active_params(
        db, ctx.organization_id, bank.jurisdiction_code, ParamConcentrationLimit, as_of
    )
    return tuple(
        ConcentrationLimit(
            dimension=row.dimension,
            limit_kind=row.limit_kind,
            value=row.value,
            bucket_key=row.bucket_key,
        )
        for row in rows
    )


def monitor(db: Session, ctx: TenantContext, bank: Bank, as_of: date) -> ConcentrationMonitorResult:
    exposures = load_credit_exposures(db, ctx, bank, as_of)
    if not exposures:
        raise ModuleDataUnavailable(
            error_code="no_credit_exposures",
            reason="No credit exposures (loans, placements or securities) are in the "
            "current canonical book; there is no concentration to measure.",
        )
    return monitor_concentration(
        exposures,
        active_limits(db, ctx, bank, as_of),
        capital_base_ghs=capital_base(db, ctx, bank, as_of),
    )


# ---------------------------------------------------------------------------
# wire reads
# ---------------------------------------------------------------------------


def concentration_read(db: Session, ctx: TenantContext, bank: Bank, as_of: date):
    """The monitor as its wire schema (CreditConcentrationRead)."""
    from app.schemas.regulatory_credit import (  # noqa: PLC0415 - schema import cycle
        ConcentrationBucketRead,
        ConcentrationDimensionRead,
        CreditConcentrationRead,
    )

    result = monitor(db, ctx, bank, as_of)
    basis = "net_own_funds" if institution_types.institution_class(db, bank) == "sdi" else "tier1"

    def bucket(reading: Any) -> Any:
        return ConcentrationBucketRead(
            key=reading.key,
            exposure_ghs=reading.exposure_ghs,
            loan_count=reading.loan_count,
            share_of_book_pct=reading.share_of_book_pct,
            share_of_capital_pct=reading.share_of_capital_pct,
            limit_value=reading.limit_value,
            limit_kind=reading.limit_kind,
            limit_status=reading.limit_status,
            utilization_pct=reading.utilization_pct,
        )

    return CreditConcentrationRead(
        as_of=as_of.isoformat(),
        total_book_ghs=result.total_book_ghs,
        capital_base_ghs=result.capital_base_ghs,
        capital_basis=basis,
        dimensions=[
            ConcentrationDimensionRead(
                dimension=dimension.dimension,
                hhi=dimension.hhi,
                bucket_count=dimension.bucket_count,
                coverage_pct=dimension.coverage_pct,
                stated_exposure_ghs=dimension.stated_exposure_ghs,
                buckets=[bucket(b) for b in dimension.buckets],
                hhi_limit=dimension.hhi_limit,
                hhi_status=dimension.hhi_status,
            )
            for dimension in result.dimensions
        ],
        breaches=[bucket(b) for b in result.breaches],
        limit_count=len(active_limits(db, ctx, bank, as_of)),
    )
