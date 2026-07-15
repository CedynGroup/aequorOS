"""Tenant-scoped proxy to the cashflow-ml forecasting service.

The ML service is tenant-unaware compute and is never exposed to the browser;
this service owns authorization (bank tenant ownership) before forwarding and
re-shapes the ML JSON into typed contracts for the generated OpenAPI client.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
from httpx import Client, HTTPError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.core.config import get_settings
from app.models import Bank
from app.schemas.cashflow_forecast import CashflowForecastRead, CashflowHistoryRead

ML_UNAVAILABLE_DETAIL = "Cash flow forecasting service is unavailable."


def get_forecast(
    db: Session, ctx: TenantContext, bank_id: UUID, *, horizon: int, mode: str
) -> CashflowForecastRead:
    _get_bank_or_404(db, ctx, bank_id)
    payload = _fetch_ml_json("/forecast", {"horizon": horizon, "mode": mode})
    return CashflowForecastRead.model_validate(payload)


def get_history(
    db: Session, ctx: TenantContext, bank_id: UUID, *, days: int
) -> CashflowHistoryRead:
    _get_bank_or_404(db, ctx, bank_id)
    payload = _fetch_ml_json("/history", {"days": days})
    return CashflowHistoryRead.model_validate(payload)


def _fetch_ml_json(path: str, params: dict[str, Any]) -> Any:
    settings = get_settings()
    try:
        with Client(
            base_url=settings.cashflow_ml.base_url,
            timeout=settings.cashflow_ml.timeout_seconds,
        ) as client:
            response = client.get(path, params=params)
            response.raise_for_status()
            return response.json()
    except HTTPError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=ML_UNAVAILABLE_DETAIL,
        ) from exc


def _get_bank_or_404(db: Session, ctx: TenantContext, bank_id: UUID) -> Bank:
    bank = db.scalar(
        select(Bank).where(Bank.id == bank_id, Bank.organization_id == ctx.organization_id)
    )
    if bank is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bank not found.")
    return bank
