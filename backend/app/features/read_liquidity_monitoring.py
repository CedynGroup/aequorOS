"""Institution-neutral liquidity-monitoring read endpoint."""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Query
from sqlalchemy import func, select

from app.api.deps import DbSession, LiquidityMonitoringResource, TenantContext
from app.domain.ingestion.constants import INCLUDED_VALIDATION_STATUSES
from app.identity.public import Bank
from app.models import CanonicalPositionSnapshot
from app.models.canonical import is_current_generation
from app.schemas.sdi import (
    LiquidityMonitoringRead,
    ModuleReadinessRead,
    SdiCounterbalancingCapacityRead,
    SdiFundingConcentrationRead,
    SdiFundingProviderRead,
    SdiMaturityBucketRead,
)
from app.services import institution_types, sdi_readiness, sdi_views

router = APIRouter(tags=["liquidity-monitoring"])


def _effective_as_of(
    db: DbSession, ctx: TenantContext, bank: Bank, requested: date | None
) -> date | None:
    """The caller's as-of, else the newest business date the current book
    carries (mirror of ``read_sdi_diagnostics._latest_book_as_of``): the latest
    current-generation, accepted/warning position snapshot — never the
    position's first-seen date.

    ``None`` for a bank whose current book is empty. The route reports that as
    ``as_of: null`` rather than today's date: an empty book has no business date,
    and claiming one turns "nothing has been fed yet" into a position measured
    today.
    """
    if requested is not None:
        return requested
    return db.scalar(
        select(func.max(CanonicalPositionSnapshot.as_of_date)).where(
            CanonicalPositionSnapshot.organization_id == ctx.organization_id,
            CanonicalPositionSnapshot.bank_id == bank.id,
            *is_current_generation(CanonicalPositionSnapshot),
            CanonicalPositionSnapshot.validation_status.in_(INCLUDED_VALIDATION_STATUSES),
        )
    )


@router.get(
    "/banks/{bank_id}/liquidity-monitoring",
    response_model=LiquidityMonitoringRead,
    operation_id="getLiquidityMonitoring",
)
def get_liquidity_monitoring(
    bank_id: str,
    db: DbSession,
    access: LiquidityMonitoringResource,
    as_of: Annotated[date | None, Query()] = None,
) -> LiquidityMonitoringRead:
    """Shared funding, collateral, maturity, and data-readiness analytics."""
    ctx = access.ctx
    bank = access.bank
    when = _effective_as_of(db, ctx, bank, as_of)
    # An empty book is probed at today's date so the readiness rows still name
    # what has to be fed, but that date is never reported as the book's own.
    probe = when or date.today()
    monitoring = sdi_views.get_liquidity_monitoring(db, ctx, bank, probe)
    readiness = sdi_readiness.assess_sdi_readiness(db, ctx, bank, probe)
    return LiquidityMonitoringRead(
        as_of=when.isoformat() if when is not None else None,
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
