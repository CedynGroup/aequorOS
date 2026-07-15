from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query

from app.api.deps import DbSession, Tenant
from app.schemas.cashflow_forecast import (
    CashflowForecastMode,
    CashflowForecastRead,
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
def get_cashflow_forecast(
    bank_id: UUID,
    db: DbSession,
    ctx: Tenant,
    horizon: Annotated[CashflowHorizon, Query()] = CashflowHorizon.DAYS_30,
    mode: Annotated[CashflowForecastMode, Query()] = "lstm",
) -> CashflowForecastRead:
    return cashflow_forecast.get_forecast(db, ctx, bank_id, horizon=int(horizon), mode=mode)


@router.get(
    "/banks/{bank_id}/cashflow-history",
    response_model=CashflowHistoryRead,
    operation_id="getCashflowHistory",
)
def get_cashflow_history(
    bank_id: UUID,
    db: DbSession,
    ctx: Tenant,
    days: Annotated[int, Query(ge=30, le=365)] = 90,
) -> CashflowHistoryRead:
    return cashflow_forecast.get_history(db, ctx, bank_id, days=days)
