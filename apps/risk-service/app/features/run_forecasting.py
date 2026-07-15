from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, status

from app.api.deps import DbSession, MutationTenant, Tenant
from app.schemas.forecasting import (
    ForecastRunCreate,
    ForecastRunListRead,
    ForecastRunRead,
    ForecastScenarioListRead,
    OptimizerResultRead,
    OptimizerRunCreate,
    WhatIfResultRead,
    WhatIfRunCreate,
)
from app.services import regulatory_forecasting

router = APIRouter(tags=["forecasting"])


@router.get(
    "/banks/{bank_id}/forecast/scenarios",
    response_model=ForecastScenarioListRead,
    operation_id="listForecastScenarios",
)
def list_forecast_scenarios(bank_id: UUID, db: DbSession, ctx: Tenant) -> ForecastScenarioListRead:
    return regulatory_forecasting.list_forecast_scenarios(db, ctx, bank_id)


@router.post(
    "/banks/{bank_id}/forecast/runs",
    response_model=ForecastRunRead,
    status_code=status.HTTP_201_CREATED,
    operation_id="createForecastRun",
)
def create_forecast_run(
    bank_id: UUID,
    payload: ForecastRunCreate,
    db: DbSession,
    ctx: MutationTenant,
) -> ForecastRunRead:
    return regulatory_forecasting.create_forecast_run(db, ctx, bank_id, payload)


@router.get(
    "/banks/{bank_id}/forecast/runs",
    response_model=ForecastRunListRead,
    operation_id="listForecastRuns",
)
def list_forecast_runs(
    bank_id: UUID,
    db: DbSession,
    ctx: Tenant,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ForecastRunListRead:
    return regulatory_forecasting.list_forecast_runs(db, ctx, bank_id, limit=limit, offset=offset)


@router.get(
    "/banks/{bank_id}/forecast/runs/{run_id}",
    response_model=ForecastRunRead,
    operation_id="getForecastRun",
)
def get_forecast_run(bank_id: UUID, run_id: UUID, db: DbSession, ctx: Tenant) -> ForecastRunRead:
    return regulatory_forecasting.get_forecast_run(db, ctx, bank_id, run_id)


@router.post(
    "/banks/{bank_id}/forecast/optimizer",
    response_model=OptimizerResultRead,
    status_code=status.HTTP_201_CREATED,
    operation_id="runStrategicOptimizer",
)
def run_strategic_optimizer(
    bank_id: UUID,
    payload: OptimizerRunCreate,
    db: DbSession,
    ctx: MutationTenant,
) -> OptimizerResultRead:
    return regulatory_forecasting.run_strategic_optimizer(db, ctx, bank_id, payload)


@router.post(
    "/banks/{bank_id}/forecast/whatif",
    response_model=WhatIfResultRead,
    status_code=status.HTTP_201_CREATED,
    operation_id="runWhatIfAnalysis",
)
def run_whatif_analysis(
    bank_id: UUID,
    payload: WhatIfRunCreate,
    db: DbSession,
    ctx: MutationTenant,
) -> WhatIfResultRead:
    return regulatory_forecasting.run_whatif_analysis(db, ctx, bank_id, payload)
