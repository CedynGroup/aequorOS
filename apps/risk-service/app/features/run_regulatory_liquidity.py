from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Query, status

from app.api.deps import DbSession, MutationTenant, Tenant
from app.schemas.regulatory_liquidity import (
    Bsd3PreviewRead,
    LiquidityDashboardRead,
    LiquidityScenarioBatchCreate,
    RegulatoryRunBatchRead,
    RegulatoryRunCreate,
    RegulatoryRunListRead,
    RegulatoryRunRead,
)
from app.services import regulatory_capital, regulatory_liquidity

router = APIRouter(tags=["regulatory-liquidity"])


@router.post(
    "/banks/{bank_id}/regulatory-runs",
    response_model=RegulatoryRunRead,
    status_code=status.HTTP_201_CREATED,
    operation_id="createRegulatoryRun",
)
def create_regulatory_run(
    bank_id: UUID,
    payload: RegulatoryRunCreate,
    db: DbSession,
    ctx: MutationTenant,
) -> RegulatoryRunRead:
    # Single entry point for regulatory runs; the schema validates the
    # module/scenario combination and the module picks the engine service.
    if payload.module == "capital":
        return regulatory_capital.create_capital_run(db, ctx, bank_id, payload)
    return regulatory_liquidity.create_liquidity_run(db, ctx, bank_id, payload)


@router.post(
    "/banks/{bank_id}/liquidity/run-all-scenarios",
    response_model=RegulatoryRunBatchRead,
    status_code=status.HTTP_201_CREATED,
    operation_id="runAllLiquidityScenarios",
)
def run_all_liquidity_scenarios(
    bank_id: UUID,
    payload: LiquidityScenarioBatchCreate,
    db: DbSession,
    ctx: MutationTenant,
) -> RegulatoryRunBatchRead:
    return regulatory_liquidity.run_all_liquidity_scenarios(db, ctx, bank_id, payload)


@router.get(
    "/banks/{bank_id}/regulatory-runs",
    response_model=RegulatoryRunListRead,
    operation_id="listRegulatoryRuns",
)
def list_regulatory_runs(  # noqa: PLR0913
    bank_id: UUID,
    db: DbSession,
    ctx: Tenant,
    module: Annotated[
        Literal["liquidity", "capital", "forecast", "optimizer", "whatif"] | None, Query()
    ] = None,
    reporting_period_id: Annotated[UUID | None, Query()] = None,
    scenario_code: Annotated[str | None, Query(max_length=40)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> RegulatoryRunListRead:
    return regulatory_liquidity.list_regulatory_runs(
        db,
        ctx,
        bank_id,
        module=module,
        reporting_period_id=reporting_period_id,
        scenario_code=scenario_code,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/banks/{bank_id}/regulatory-runs/{run_id}",
    response_model=RegulatoryRunRead,
    operation_id="getRegulatoryRun",
)
def get_regulatory_run(
    bank_id: UUID, run_id: UUID, db: DbSession, ctx: Tenant
) -> RegulatoryRunRead:
    return regulatory_liquidity.get_regulatory_run(db, ctx, bank_id, run_id)


@router.get(
    "/banks/{bank_id}/liquidity/dashboard",
    response_model=LiquidityDashboardRead,
    operation_id="getLiquidityDashboard",
)
def get_liquidity_dashboard(
    bank_id: UUID,
    db: DbSession,
    ctx: Tenant,
    reporting_period_id: Annotated[UUID | None, Query()] = None,
) -> LiquidityDashboardRead:
    return regulatory_liquidity.get_liquidity_dashboard(db, ctx, bank_id, reporting_period_id)


@router.get(
    "/banks/{bank_id}/submissions/bsd3",
    response_model=Bsd3PreviewRead,
    operation_id="getBsd3Preview",
)
def get_bsd3_preview(
    bank_id: UUID,
    reporting_period_id: Annotated[UUID, Query()],
    db: DbSession,
    ctx: Tenant,
) -> Bsd3PreviewRead:
    return regulatory_liquidity.get_bsd3_preview(db, ctx, bank_id, reporting_period_id)
