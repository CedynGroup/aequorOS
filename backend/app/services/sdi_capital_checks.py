"""SDI simplified-capital regulatory checks (docs/sdi.md §4.2, §7 Phase E).

Additive, control-plane-driven checks over the ``capital_structure`` reference
dataset — the minimum paid-up-capital floor (Act 930 s.29 / SDI Subsector ToR)
and statutory-reserve-fund adequacy (NBFI r.7). Isolated reads: they do NOT run
inside the capital engine and touch no bank golden. ``paid_up_min`` is licence-
specific (``institution_type`` scope, expressed in GHS millions);
``statutory_reserve_fund_pct`` is class-keyed. Every result carries the resolved
value's source citation + confirmation status so a pending default is never
presented as authoritative, and missing data yields NOT-COMPUTABLE, never a
false green.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.domain.stress.appendix_ii import _PAID_UP, _STATUTORY
from app.models import Bank, CanonicalReferenceRow
from app.services import regulatory_parameters

_ZERO = Decimal("0")
_GHS_PER_MILLION = Decimal("1000000")


@dataclass(frozen=True)
class CapitalCheckResult:
    """One simplified-capital check with full provenance."""

    check: str
    #: True/False when computable; None when the required data is absent.
    compliant: bool | None
    actual_ghs: Decimal | None
    required_ghs: Decimal | None
    detail: str
    source_citation: str
    confirmation_status: str

    @property
    def computable(self) -> bool:
        return self.compliant is not None


def capital_components(
    db: Session, ctx: TenantContext, bank: Bank, as_of: date
) -> dict[str, Decimal]:
    """Sum ``capital_structure`` amounts by lower-cased component name, from the
    latest ingested generation on/before ``as_of`` (latest as_of_date, then latest
    batch — the reference-dataset supersession convention). Empty when the dataset
    was never ingested."""
    scope = (
        CanonicalReferenceRow.organization_id == ctx.organization_id,
        CanonicalReferenceRow.bank_id == bank.id,
        CanonicalReferenceRow.dataset_kind == "capital_structure",
    )
    latest = db.scalar(
        select(func.max(CanonicalReferenceRow.as_of_date)).where(
            *scope, CanonicalReferenceRow.as_of_date <= as_of
        )
    )
    totals: dict[str, Decimal] = {}
    if latest is None:
        return totals
    latest_batch = db.scalar(
        select(CanonicalReferenceRow.ingestion_batch_id)
        .where(*scope, CanonicalReferenceRow.as_of_date == latest)
        .order_by(CanonicalReferenceRow.created_at.desc(), CanonicalReferenceRow.id.desc())
        .limit(1)
    )
    for row in db.scalars(
        select(CanonicalReferenceRow).where(
            *scope,
            CanonicalReferenceRow.as_of_date == latest,
            CanonicalReferenceRow.ingestion_batch_id == latest_batch,
        )
    ):
        payload = row.payload or {}
        component = str(payload.get("capital_component", "")).strip().lower()
        amount = payload.get("amount_ghs")
        if not component or amount is None:
            continue
        totals[component] = totals.get(component, _ZERO) + abs(Decimal(str(amount)))
    return totals


def _sum(components: dict[str, Decimal], names: frozenset[str]) -> Decimal:
    return sum((components.get(name, _ZERO) for name in names), _ZERO)


def check_paid_up_capital(
    db: Session, ctx: TenantContext, bank: Bank, as_of: date
) -> CapitalCheckResult:
    """Paid-up capital ≥ the licence's statutory minimum (docs/sdi.md §2.2)."""
    param = regulatory_parameters.resolve(db, bank, "paid_up_min", as_of=as_of)
    required_ghs = param.decimal * _GHS_PER_MILLION
    components = capital_components(db, ctx, bank, as_of)
    if not components:
        return CapitalCheckResult(
            check="paid_up_capital",
            compliant=None,
            actual_ghs=None,
            required_ghs=required_ghs,
            detail=(
                "The capital_structure reference dataset has not been ingested — "
                "paid-up capital is not computable."
            ),
            source_citation=param.source_citation,
            confirmation_status=param.confirmation_status,
        )
    paid_up = _sum(components, _PAID_UP)
    compliant = paid_up >= required_ghs
    detail = (
        f"Paid-up capital {paid_up} GHS "
        f"{'meets' if compliant else 'is below'} the statutory minimum {required_ghs} GHS "
        f"({param.decimal}m)."
    )
    return CapitalCheckResult(
        check="paid_up_capital",
        compliant=compliant,
        actual_ghs=paid_up,
        required_ghs=required_ghs,
        detail=detail,
        source_citation=param.source_citation,
        confirmation_status=param.confirmation_status,
    )


def check_statutory_reserve_fund(
    db: Session, ctx: TenantContext, bank: Bank, as_of: date
) -> CapitalCheckResult:
    """Statutory-reserve-fund adequacy (NBFI r.7): the fund is built from 50% of
    net profit UNTIL it equals paid-up capital, then ≥15%. Without a P&L transfer
    history we check FUND ADEQUACY — the statutory reserve fund should have built
    to at least paid-up capital; a shortfall means the fund is still building
    (the higher transfer rate still applies). The ``statutory_reserve_fund_pct``
    value is surfaced for provenance."""
    param = regulatory_parameters.resolve(db, bank, "statutory_reserve_fund_pct", as_of=as_of)
    components = capital_components(db, ctx, bank, as_of)
    if not components:
        return CapitalCheckResult(
            check="statutory_reserve_fund",
            compliant=None,
            actual_ghs=None,
            required_ghs=None,
            detail=(
                "The capital_structure reference dataset has not been ingested — "
                "statutory-reserve-fund adequacy is not computable."
            ),
            source_citation=param.source_citation,
            confirmation_status=param.confirmation_status,
        )
    reserve = _sum(components, _STATUTORY)
    paid_up = _sum(components, _PAID_UP)
    fully_built = reserve >= paid_up
    detail = (
        f"Statutory reserve fund {reserve} GHS is "
        f"{'fully built (≥ paid-up capital)' if fully_built else 'still building'} "
        f"against paid-up capital {paid_up} GHS; until fully built, {param.decimal}% of "
        "net profit must be transferred to the fund each period (NBFI r.7)."
    )
    return CapitalCheckResult(
        check="statutory_reserve_fund",
        compliant=fully_built,
        actual_ghs=reserve,
        required_ghs=paid_up,
        detail=detail,
        source_citation=param.source_citation,
        confirmation_status=param.confirmation_status,
    )
