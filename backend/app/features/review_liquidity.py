from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query

from app.api.deps import DbSession, MutationTenant, Tenant
from app.schemas.liquidity import (
    LiquidityFindingRead,
    LiquidityFindingReview,
    LiquiditySummaryRead,
)
from app.services import liquidity

router = APIRouter(tags=["liquidity"])


@router.get(
    "/cases/{case_id}/liquidity/summary",
    response_model=LiquiditySummaryRead,
    operation_id="getLiquiditySummary",
)
def get_liquidity_summary(
    case_id: UUID,
    db: DbSession,
    ctx: Tenant,
    scenario_id: Annotated[UUID | None, Query()] = None,
    run_id: Annotated[UUID | None, Query()] = None,
) -> LiquiditySummaryRead:
    return liquidity.get_summary(db, ctx, case_id, scenario_id=scenario_id, run_id=run_id)


@router.post(
    "/cases/{case_id}/liquidity/findings/{finding_id}/review",
    response_model=LiquidityFindingRead,
    operation_id="reviewLiquidityFinding",
)
def review_liquidity_finding(
    case_id: UUID,
    finding_id: UUID,
    payload: LiquidityFindingReview,
    db: DbSession,
    ctx: MutationTenant,
) -> LiquidityFindingRead:
    return liquidity.review_finding(db, ctx, case_id, finding_id, payload)
