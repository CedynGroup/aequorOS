"""Institution-neutral liquidity-monitoring read endpoint."""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Query
from sqlalchemy import func, select

from app.api.deps import DbSession, Tenant, TenantContext
from app.models import Bank, CanonicalPosition
from app.schemas.sdi import (
    LiquidityMonitoringRead,
    ModuleReadinessRead,
    SdiCounterbalancingCapacityRead,
    SdiFundingConcentrationRead,
    SdiFundingProviderRead,
    SdiMaturityBucketRead,
)
from app.services import banks as banks_service
from app.services import institution_types, sdi_readiness, sdi_views

router = APIRouter(tags=["liquidity-monitoring"])


def _effective_as_of(
    db: DbSession, ctx: TenantContext, bank: Bank, requested: date | None
) -> date:
    if requested is not None:
        return requested
    latest = db.scalar(
        select(func.max(CanonicalPosition.as_of_date)).where(
            CanonicalPosition.organization_id == ctx.organization_id,
            CanonicalPosition.bank_id == bank.id,
        )
    )
    return latest or date.today()


@router.get(
    "/banks/{bank_id}/liquidity-monitoring",
    response_model=LiquidityMonitoringRead,
    operation_id="getLiquidityMonitoring",
)
def get_liquidity_monitoring(
    bank_id: str,
    db: DbSession,
    ctx: Tenant,
    as_of: Annotated[date | None, Query()] = None,
) -> LiquidityMonitoringRead:
    """Shared funding, collateral, maturity, and data-readiness analytics."""
    bank = banks_service.resolve_bank_reference(db, ctx, bank_id)
    when = _effective_as_of(db, ctx, bank, as_of)
    monitoring = sdi_views.get_liquidity_monitoring(db, ctx, bank, when)
    readiness = sdi_readiness.assess_sdi_readiness(db, ctx, bank, when)
    return LiquidityMonitoringRead(
        as_of=monitoring.as_of.isoformat(),
        institution_class=institution_types.get_type(db, bank).institution_class,
        maturity_ladder=[
            SdiMaturityBucketRead(
                code=row.code,
                label=row.label,
                net_mismatch_ghs=row.net_mismatch_ghs,
                cumulative_mismatch_ghs=row.cumulative_mismatch_ghs,
            )
            for row in monitoring.maturity_ladder
        ],
        funding_concentration=SdiFundingConcentrationRead(
            total_deposits_ghs=monitoring.funding_concentration.total_deposits_ghs,
            top_five_deposits_ghs=monitoring.funding_concentration.top_five_deposits_ghs,
            top_five_pct=monitoring.funding_concentration.top_five_pct,
            unattributed_deposits_ghs=monitoring.funding_concentration.unattributed_deposits_ghs,
            providers=[
                SdiFundingProviderRead(
                    name=row.name,
                    deposit_ghs=row.deposit_ghs,
                    pct_total_deposits=row.pct_total_deposits,
                    related=row.related,
                )
                for row in monitoring.funding_concentration.providers
            ],
        ),
        counterbalancing_capacity=SdiCounterbalancingCapacityRead(
            gross_unencumbered_ghs=monitoring.counterbalancing_capacity.gross_unencumbered_ghs,
            monetized_value_ghs=monitoring.counterbalancing_capacity.monetized_value_ghs,
            bog_eligible_ghs=monitoring.counterbalancing_capacity.bog_eligible_ghs,
            uncalibrated_asset_count=monitoring.counterbalancing_capacity.uncalibrated_asset_count,
        ),
        readiness=[
            ModuleReadinessRead(module=row.module, status=row.status, reasons=row.reasons)
            for row in readiness
        ],
    )