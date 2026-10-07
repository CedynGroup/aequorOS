"""Forecast assumption register routes: the maker's draft and submit, the checker's decision.

Reads take a confidential Forecasting view (the register holds unapproved values
and who proposed them). Drafting, revising and submitting take Forecasting
``edit``; deciding takes ``review`` at the route and, in the service, ``approve``
with the maker-checker verdict for the exact version.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, status

from app.api.deps import (
    DbSession,
    ForecastingAssumptionEdit,
    ForecastingAssumptionReview,
    ForecastingConfidentialView,
)
from app.forecasting import service
from app.forecasting.schemas import (
    ForecastAssumptionDecision,
    ForecastAssumptionRegisterRead,
    ForecastAssumptionVersionCreate,
    ForecastAssumptionVersionRead,
    ForecastAssumptionVersionUpdate,
)

router = APIRouter(tags=["forecasting"])

_VERSIONS = "/banks/{bank_id}/forecast/assumption-versions"
_VERSION = f"{_VERSIONS}/{{version_id}}"


@router.get(
    _VERSIONS,
    response_model=ForecastAssumptionRegisterRead,
    operation_id="getForecastAssumptionRegister",
)
def get_forecast_assumption_register(
    bank_id: str, db: DbSession, access: ForecastingConfidentialView
) -> ForecastAssumptionRegisterRead:
    return service.get_register(db, access.ctx, bank_id)


@router.post(
    _VERSIONS,
    response_model=ForecastAssumptionVersionRead,
    status_code=status.HTTP_201_CREATED,
    operation_id="createForecastAssumptionVersion",
)
def create_forecast_assumption_version(
    bank_id: str,
    payload: ForecastAssumptionVersionCreate,
    db: DbSession,
    access: ForecastingAssumptionEdit,
) -> ForecastAssumptionVersionRead:
    return service.create_version(db, access.ctx, bank_id, payload)


@router.get(
    _VERSION,
    response_model=ForecastAssumptionVersionRead,
    operation_id="getForecastAssumptionVersion",
)
def get_forecast_assumption_version(
    bank_id: str, version_id: UUID, db: DbSession, access: ForecastingConfidentialView
) -> ForecastAssumptionVersionRead:
    return service.get_version(db, access.ctx, bank_id, version_id)


@router.patch(
    _VERSION,
    response_model=ForecastAssumptionVersionRead,
    operation_id="updateForecastAssumptionVersion",
)
def update_forecast_assumption_version(
    bank_id: str,
    version_id: UUID,
    payload: ForecastAssumptionVersionUpdate,
    db: DbSession,
    access: ForecastingAssumptionEdit,
) -> ForecastAssumptionVersionRead:
    return service.update_version(db, access.ctx, bank_id, version_id, payload)


@router.post(
    f"{_VERSION}/submit",
    response_model=ForecastAssumptionVersionRead,
    operation_id="submitForecastAssumptionVersion",
)
def submit_forecast_assumption_version(
    bank_id: str, version_id: UUID, db: DbSession, access: ForecastingAssumptionEdit
) -> ForecastAssumptionVersionRead:
    return service.submit_version(db, access.ctx, bank_id, version_id)


@router.post(
    f"{_VERSION}/approve",
    response_model=ForecastAssumptionVersionRead,
    operation_id="approveForecastAssumptionVersion",
)
def approve_forecast_assumption_version(
    bank_id: str,
    version_id: UUID,
    payload: ForecastAssumptionDecision,
    db: DbSession,
    access: ForecastingAssumptionReview,
) -> ForecastAssumptionVersionRead:
    return service.approve_version(db, access.ctx, bank_id, version_id, payload)


@router.post(
    f"{_VERSION}/reject",
    response_model=ForecastAssumptionVersionRead,
    operation_id="rejectForecastAssumptionVersion",
)
def reject_forecast_assumption_version(
    bank_id: str,
    version_id: UUID,
    payload: ForecastAssumptionDecision,
    db: DbSession,
    access: ForecastingAssumptionReview,
) -> ForecastAssumptionVersionRead:
    return service.reject_version(db, access.ctx, bank_id, version_id, payload)
