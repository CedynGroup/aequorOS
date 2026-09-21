"""Balance-sheet forecasting routes.

Every route names its exact Forecasting binding: aggregated view for the
scenario presets and run summaries, confidential view for a full run, and
confidential run for anything that mints a run. The service re-checks run
authority before it computes, so the dependency here is the route's contract,
not its only guard.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, status

from app.api.deps import (
    DbSession,
    ForecastingAggregatedView,
    ForecastingRun,
    ForecastingRunDetailView,
)
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
def list_forecast_scenarios(
    bank_id: str, db: DbSession, access: ForecastingAggregatedView
) -> ForecastScenarioListRead:
    return regulatory_forecasting.list_forecast_scenarios(db, access.ctx, bank_id)


@router.post(
    "/banks/{bank_id}/forecast/runs",
    response_model=ForecastRunRead,
    status_code=status.HTTP_201_CREATED,
    operation_id="createForecastRun",
)
def create_forecast_run(
    bank_id: str,
    payload: ForecastRunCreate,
    db: DbSession,
    access: ForecastingRun,
) -> ForecastRunRead:
    return regulatory_forecasting.create_forecast_run(db, access.ctx, bank_id, payload)


@router.get(
    "/banks/{bank_id}/forecast/runs",
    response_model=ForecastRunListRead,
    operation_id="listForecastRuns",
)
def list_forecast_runs(
    bank_id: str,
    db: DbSession,
    access: ForecastingAggregatedView,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ForecastRunListRead:
    return regulatory_forecasting.list_forecast_runs(
        db, access.ctx, bank_id, limit=limit, offset=offset
    )


@router.get(
    "/banks/{bank_id}/forecast/runs/{run_id}",
    response_model=ForecastRunRead,
    operation_id="getForecastRun",
)
def get_forecast_run(
    bank_id: str, run_id: UUID, db: DbSession, access: ForecastingRunDetailView
) -> ForecastRunRead:
    return regulatory_forecasting.get_forecast_run(db, access.ctx, bank_id, run_id)


@router.post(
    "/banks/{bank_id}/forecast/optimizer",
    response_model=OptimizerResultRead,
    status_code=status.HTTP_201_CREATED,
    operation_id="runStrategicOptimizer",
)
def run_strategic_optimizer(
    bank_id: str,
    payload: OptimizerRunCreate,
    db: DbSession,
    access: ForecastingRun,
) -> OptimizerResultRead:
    return regulatory_forecasting.run_strategic_optimizer(db, access.ctx, bank_id, payload)


@router.post(
    "/banks/{bank_id}/forecast/whatif",
    response_model=WhatIfResultRead,
    status_code=status.HTTP_201_CREATED,
    operation_id="runWhatIfAnalysis",
)
def run_whatif_analysis(
    bank_id: str,
    payload: WhatIfRunCreate,
    db: DbSession,
    access: ForecastingRun,
) -> WhatIfResultRead:
    return regulatory_forecasting.run_whatif_analysis(db, access.ctx, bank_id, payload)
