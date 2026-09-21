"""ICAAP risk register: scoring risks, deciding materiality, adding emerging ones."""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Path, status

from app.api.deps import DbSession, IcaapEdit, IcaapView
from app.schemas.common import ErrorResponse
from app.schemas.icaap_risk_capital import (
    IcaapCustomRiskCreate,
    IcaapRetire,
    IcaapRiskPut,
    IcaapRiskRead,
    IcaapRiskRegisterRead,
)
from app.services.icaap import risks

router = APIRouter(tags=["icaap"])

_ERRORS: dict[int | str, dict[str, Any]] = {
    403: {"model": ErrorResponse},
    404: {"model": ErrorResponse},
    409: {"model": ErrorResponse},
    422: {"model": ErrorResponse},
}
_CYCLE = "/banks/{bank_id}/icaap/cycles/{cycle_id}"
RiskKey = Annotated[str, Path(pattern=r"^[a-z][a-z0-9_]{1,79}$")]


@router.get(
    f"{_CYCLE}/risks",
    response_model=IcaapRiskRegisterRead,
    operation_id="listIcaapRisks",
    responses=_ERRORS,
)
def list_icaap_risks(
    bank_id: str, cycle_id: UUID, db: DbSession, access: IcaapView
) -> IcaapRiskRegisterRead:
    """The framework's risk categories merged with this cycle's assessments."""
    _ = bank_id
    return risks.get_register(db, access, cycle_id)


@router.put(
    f"{_CYCLE}/risks/{{risk_key}}",
    response_model=IcaapRiskRead,
    operation_id="putIcaapRisk",
    responses=_ERRORS,
)
def put_icaap_risk(  # noqa: PLR0913 - the addressed risk is five path parts
    bank_id: str,
    cycle_id: UUID,
    risk_key: RiskKey,
    payload: IcaapRiskPut,
    db: DbSession,
    access: IcaapEdit,
) -> IcaapRiskRead:
    """Score a risk. The verdict comes from the governed matrix, not the client."""
    _ = bank_id
    return risks.put_risk(db, access, cycle_id, risk_key, payload)


@router.post(
    f"{_CYCLE}/risks",
    response_model=IcaapRiskRead,
    status_code=status.HTTP_201_CREATED,
    operation_id="createIcaapCustomRisk",
    responses=_ERRORS,
)
def create_icaap_custom_risk(
    bank_id: str,
    cycle_id: UUID,
    payload: IcaapCustomRiskCreate,
    db: DbSession,
    access: IcaapEdit,
) -> IcaapRiskRead:
    """Add an emerging risk the framework does not name."""
    _ = bank_id
    return risks.create_custom_risk(db, access, cycle_id, payload)


@router.post(
    f"{_CYCLE}/risks/{{risk_key}}/retire",
    response_model=IcaapRiskRead,
    operation_id="retireIcaapCustomRisk",
    responses=_ERRORS,
)
def retire_icaap_custom_risk(  # noqa: PLR0913 - the addressed risk is five path parts
    bank_id: str,
    cycle_id: UUID,
    risk_key: RiskKey,
    payload: IcaapRetire,
    db: DbSession,
    access: IcaapEdit,
) -> IcaapRiskRead:
    _ = bank_id
    return risks.retire_custom_risk(db, access, cycle_id, risk_key, payload)
