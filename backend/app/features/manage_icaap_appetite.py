"""ICAAP risk appetite and the capital plan's triggers."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, status

from app.api.deps import DbSession, IcaapEdit, IcaapView
from app.schemas.common import ErrorResponse
from app.schemas.icaap_risk_capital import (
    IcaapAppetiteMetricCreate,
    IcaapAppetiteMetricRead,
    IcaapAppetiteMetricUpdate,
    IcaapAppetiteRead,
    IcaapRetire,
    IcaapTriggerEvaluationRead,
)
from app.services.icaap import appetite, capital_triggers

router = APIRouter(tags=["icaap"])

_ERRORS: dict[int | str, dict[str, Any]] = {
    403: {"model": ErrorResponse},
    404: {"model": ErrorResponse},
    409: {"model": ErrorResponse},
    422: {"model": ErrorResponse},
}
_CYCLE = "/banks/{bank_id}/icaap/cycles/{cycle_id}"


@router.get(
    f"{_CYCLE}/appetite",
    response_model=IcaapAppetiteRead,
    operation_id="getIcaapAppetite",
    responses=_ERRORS,
)
def get_icaap_appetite(
    bank_id: str, cycle_id: UUID, db: DbSession, access: IcaapView
) -> IcaapAppetiteRead:
    """The appetite statement, re-evaluated against today's governed floors."""
    _ = bank_id
    return appetite.get_appetite(db, access, cycle_id)


@router.post(
    f"{_CYCLE}/appetite/metrics",
    response_model=IcaapAppetiteMetricRead,
    status_code=status.HTTP_201_CREATED,
    operation_id="createIcaapAppetiteMetric",
    responses=_ERRORS,
)
def create_icaap_appetite_metric(
    bank_id: str,
    cycle_id: UUID,
    payload: IcaapAppetiteMetricCreate,
    db: DbSession,
    access: IcaapEdit,
) -> IcaapAppetiteMetricRead:
    _ = bank_id
    return appetite.create_metric(db, access, cycle_id, payload)


@router.put(
    f"{_CYCLE}/appetite/metrics/{{metric_id}}",
    response_model=IcaapAppetiteMetricRead,
    operation_id="updateIcaapAppetiteMetric",
    responses=_ERRORS,
)
def update_icaap_appetite_metric(  # noqa: PLR0913 - the addressed metric is five path parts
    bank_id: str,
    cycle_id: UUID,
    metric_id: UUID,
    payload: IcaapAppetiteMetricUpdate,
    db: DbSession,
    access: IcaapEdit,
) -> IcaapAppetiteMetricRead:
    _ = bank_id
    return appetite.update_metric(db, access, cycle_id, metric_id, payload)


@router.post(
    f"{_CYCLE}/appetite/metrics/{{metric_id}}/retire",
    response_model=IcaapAppetiteMetricRead,
    operation_id="retireIcaapAppetiteMetric",
    responses=_ERRORS,
)
def retire_icaap_appetite_metric(  # noqa: PLR0913 - the addressed metric is five path parts
    bank_id: str,
    cycle_id: UUID,
    metric_id: UUID,
    payload: IcaapRetire,
    db: DbSession,
    access: IcaapEdit,
) -> IcaapAppetiteMetricRead:
    _ = bank_id
    return appetite.retire_metric(db, access, cycle_id, metric_id, payload)


@router.get(
    f"{_CYCLE}/capital-triggers",
    response_model=IcaapTriggerEvaluationRead,
    operation_id="getIcaapCapitalTriggers",
    responses=_ERRORS,
)
def get_icaap_capital_triggers(
    bank_id: str, cycle_id: UUID, db: DbSession, access: IcaapView
) -> IcaapTriggerEvaluationRead:
    """The approved plan's triggers against current and projected positions."""
    _ = bank_id
    return capital_triggers.evaluate(db, access, cycle_id)
