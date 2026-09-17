from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import DbSession, LiquidityConfidentialResource
from app.schemas.cashflow_forecast import (
    CashflowForecastMode,
    CashflowForecastRead,
    CashflowForecastScenario,
    CashflowHistoryRead,
    CashflowHorizon,
)
from app.services import cashflow_forecast

router = APIRouter(tags=["cashflow-forecast"])


@router.get(
    "/banks/{bank_id}/cashflow-forecast",
    response_model=CashflowForecastRead,
    operation_id="getCashflowForecast",
)
def get_cashflow_forecast(  # noqa: PLR0913 - typed query contract names every control
    bank_id: str,
    db: DbSession,
    access: LiquidityConfidentialResource,
    horizon: Annotated[CashflowHorizon, Query()] = CashflowHorizon.DAYS_30,
    mode: Annotated[CashflowForecastMode, Query()] = "lstm",
    scenario: Annotated[CashflowForecastScenario, Query()] = "baseline",
) -> CashflowForecastRead:
    return cashflow_forecast.get_forecast(
        db,
        access.ctx,
        access.bank.id,
        horizon=int(horizon),
        mode=mode,
        scenario=scenario,
    )


@router.get(
    "/banks/{bank_id}/cashflow-history",
    response_model=CashflowHistoryRead,
    operation_id="getCashflowHistory",
)
def get_cashflow_history(
    bank_id: str,
    db: DbSession,
    access: LiquidityConfidentialResource,
    days: Annotated[int, Query(ge=30, le=365)] = 90,
) -> CashflowHistoryRead:
    return cashflow_forecast.get_history(db, access.ctx, access.bank.id, days=days)
