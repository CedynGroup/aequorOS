"""Reconciling internal and regulatory capital, and allocating it to units."""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Path, status

from app.api.deps import DbSession, IcaapEdit, IcaapView
from app.schemas.common import ErrorResponse
from app.schemas.icaap_risk_capital import (
    IcaapAllocationPut,
    IcaapAllocationRead,
    IcaapExplanation,
    IcaapReason,
    IcaapReconciliationRead,
    IcaapResourcesLineCreate,
    IcaapResourcesLineUpdate,
)
from app.services.icaap import allocation, reconciliation

router = APIRouter(tags=["icaap"])

_ERRORS: dict[int | str, dict[str, Any]] = {
    403: {"model": ErrorResponse},
    404: {"model": ErrorResponse},
    409: {"model": ErrorResponse},
    422: {"model": ErrorResponse},
}
_CYCLE = "/banks/{bank_id}/icaap/cycles/{cycle_id}"
LineKey = Annotated[str, Path(pattern=r"^[a-z][a-z0-9_]{1,79}$")]
ControlCode = Annotated[str, Path(pattern=r"^[a-z][a-z0-9_]{1,39}$")]
ComparisonKey = Annotated[str, Path(pattern=r"^[a-z0-9_:]{1,120}$")]


@router.get(
    f"{_CYCLE}/reconciliation",
    response_model=IcaapReconciliationRead,
    operation_id="getIcaapReconciliation",
    responses=_ERRORS,
)
def get_icaap_reconciliation(
    bank_id: str, cycle_id: UUID, db: DbSession, access: IcaapView
) -> IcaapReconciliationRead:
    """Requirement by risk, resources by component, and the consistency control."""
    _ = bank_id
    return reconciliation.get_reconciliation(db, access, cycle_id)


@router.post(
    f"{_CYCLE}/reconciliation/requirement/compute",
    response_model=IcaapReconciliationRead,
    operation_id="computeIcaapRequirementReconciliation",
    responses=_ERRORS,
)
def compute_icaap_requirement_reconciliation(
    bank_id: str, cycle_id: UUID, payload: IcaapReason, db: DbSession, access: IcaapEdit
) -> IcaapReconciliationRead:
    """Replace every requirement line. Explanations are carried and flagged if stale."""
    _ = bank_id
    return reconciliation.compute_requirement(db, access, cycle_id, payload)


@router.put(
    f"{_CYCLE}/reconciliation/requirement/lines/{{line_key}}/explanation",
    response_model=IcaapReconciliationRead,
    operation_id="explainIcaapRequirementLine",
    responses=_ERRORS,
)
def explain_icaap_requirement_line(  # noqa: PLR0913 - the addressed line is five path parts
    bank_id: str,
    cycle_id: UUID,
    line_key: LineKey,
    payload: IcaapExplanation,
    db: DbSession,
    access: IcaapEdit,
) -> IcaapReconciliationRead:
    _ = bank_id
    return reconciliation.explain_requirement_line(db, access, cycle_id, line_key, payload)


@router.post(
    f"{_CYCLE}/reconciliation/resources/lines",
    response_model=IcaapReconciliationRead,
    status_code=status.HTTP_201_CREATED,
    operation_id="createIcaapResourcesLine",
    responses=_ERRORS,
)
def create_icaap_resources_line(
    bank_id: str,
    cycle_id: UUID,
    payload: IcaapResourcesLineCreate,
    db: DbSession,
    access: IcaapEdit,
) -> IcaapReconciliationRead:
    _ = bank_id
    return reconciliation.create_resources_line(db, access, cycle_id, payload)


@router.put(
    f"{_CYCLE}/reconciliation/resources/lines/{{line_id}}",
    response_model=IcaapReconciliationRead,
    operation_id="updateIcaapResourcesLine",
    responses=_ERRORS,
)
def update_icaap_resources_line(  # noqa: PLR0913 - the addressed line is five path parts
    bank_id: str,
    cycle_id: UUID,
    line_id: UUID,
    payload: IcaapResourcesLineUpdate,
    db: DbSession,
    access: IcaapEdit,
) -> IcaapReconciliationRead:
    _ = bank_id
    return reconciliation.update_resources_line(db, access, cycle_id, line_id, payload)


@router.delete(
    f"{_CYCLE}/reconciliation/resources/lines/{{line_id}}",
    response_model=IcaapReconciliationRead,
    operation_id="deleteIcaapResourcesLine",
    responses=_ERRORS,
)
def delete_icaap_resources_line(
    bank_id: str, cycle_id: UUID, line_id: UUID, db: DbSession, access: IcaapEdit
) -> IcaapReconciliationRead:
    _ = bank_id
    return reconciliation.delete_resources_line(db, access, cycle_id, line_id)


@router.post(
    f"{_CYCLE}/reconciliation/resources/load-regulatory",
    response_model=IcaapReconciliationRead,
    operation_id="loadIcaapRegulatoryCapitalComponents",
    responses=_ERRORS,
)
def load_icaap_regulatory_capital_components(
    bank_id: str, cycle_id: UUID, payload: IcaapReason, db: DbSession, access: IcaapEdit
) -> IcaapReconciliationRead:
    """Seed the resources table from the bound capital position's own components."""
    _ = bank_id
    return reconciliation.load_regulatory_components(db, access, cycle_id, payload)


@router.put(
    f"{_CYCLE}/reconciliation/controls/{{control_code}}/explanations/{{comparison_key}}",
    response_model=IcaapReconciliationRead,
    operation_id="explainIcaapControlDifference",
    responses=_ERRORS,
)
def explain_icaap_control_difference(  # noqa: PLR0913 - the addressed pair is six path parts
    bank_id: str,
    cycle_id: UUID,
    control_code: ControlCode,
    comparison_key: ComparisonKey,
    payload: IcaapExplanation,
    db: DbSession,
    access: IcaapEdit,
) -> IcaapReconciliationRead:
    """Explain why this ICAAP and another source state different figures."""
    _ = bank_id
    return reconciliation.explain_control_difference(
        db, access, cycle_id, control_code, comparison_key, payload
    )


@router.get(
    f"{_CYCLE}/allocation",
    response_model=IcaapAllocationRead,
    operation_id="getIcaapCapitalAllocation",
    responses=_ERRORS,
)
def get_icaap_capital_allocation(
    bank_id: str, cycle_id: UUID, db: DbSession, access: IcaapView
) -> IcaapAllocationRead:
    _ = bank_id
    return allocation.get_allocation(db, access, cycle_id)


@router.put(
    f"{_CYCLE}/allocation",
    response_model=IcaapAllocationRead,
    operation_id="putIcaapCapitalAllocation",
    responses=_ERRORS,
)
def put_icaap_capital_allocation(
    bank_id: str,
    cycle_id: UUID,
    payload: IcaapAllocationPut,
    db: DbSession,
    access: IcaapEdit,
) -> IcaapAllocationRead:
    """Replace the drivers. The amounts are derived so the parts add up exactly."""
    _ = bank_id
    return allocation.put_allocation(db, access, cycle_id, payload)
