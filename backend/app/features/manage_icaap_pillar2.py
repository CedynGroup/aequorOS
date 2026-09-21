"""The Pillar 2 register: items, computation, approval, Table 5, the plan proposal."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, status

from app.api.deps import (
    DbSession,
    IcaapCapitalPlanPropose,
    IcaapEdit,
    IcaapPillar2Approve,
    IcaapView,
)
from app.schemas.common import ErrorResponse
from app.schemas.icaap_risk_capital import (
    IcaapCapitalPlanProposalCreate,
    IcaapCapitalPlanProposalRead,
    IcaapParameterUseListRead,
    IcaapParameterUseRead,
    IcaapPillar2Compute,
    IcaapPillar2ItemCreate,
    IcaapPillar2ItemRead,
    IcaapPillar2ItemUpdate,
    IcaapPillar2RegisterRead,
    IcaapPillar2RevisionListRead,
    IcaapRetire,
    IcaapTable5Read,
)
from app.schemas.icaap_risk_capital import (
    IcaapPillar2Approve as IcaapPillar2ApprovePayload,
)
from app.services.icaap import guards, params, pillar2

router = APIRouter(tags=["icaap"])

_ERRORS: dict[int | str, dict[str, Any]] = {
    403: {"model": ErrorResponse},
    404: {"model": ErrorResponse},
    409: {"model": ErrorResponse},
    422: {"model": ErrorResponse},
}
_CYCLE = "/banks/{bank_id}/icaap/cycles/{cycle_id}"


@router.get(
    f"{_CYCLE}/pillar2",
    response_model=IcaapPillar2RegisterRead,
    operation_id="getIcaapPillar2Register",
    responses=_ERRORS,
)
def get_icaap_pillar2_register(
    bank_id: str, cycle_id: UUID, db: DbSession, access: IcaapView
) -> IcaapPillar2RegisterRead:
    """Every Pillar 2 figure, its Table 5 row, and the internal consistency control."""
    _ = bank_id
    return pillar2.get_register(db, access, cycle_id)


@router.post(
    f"{_CYCLE}/pillar2/items",
    response_model=IcaapPillar2ItemRead,
    status_code=status.HTTP_201_CREATED,
    operation_id="createIcaapPillar2Item",
    responses=_ERRORS,
)
def create_icaap_pillar2_item(
    bank_id: str,
    cycle_id: UUID,
    payload: IcaapPillar2ItemCreate,
    db: DbSession,
    access: IcaapEdit,
) -> IcaapPillar2ItemRead:
    _ = bank_id
    return pillar2.create_item(db, access, cycle_id, payload)


@router.put(
    f"{_CYCLE}/pillar2/items/{{item_id}}",
    response_model=IcaapPillar2ItemRead,
    operation_id="updateIcaapPillar2Item",
    responses=_ERRORS,
)
def update_icaap_pillar2_item(  # noqa: PLR0913 - the addressed item is five path parts
    bank_id: str,
    cycle_id: UUID,
    item_id: UUID,
    payload: IcaapPillar2ItemUpdate,
    db: DbSession,
    access: IcaapEdit,
) -> IcaapPillar2ItemRead:
    _ = bank_id
    return pillar2.update_item(db, access, cycle_id, item_id, payload)


@router.post(
    f"{_CYCLE}/pillar2/items/{{item_id}}/compute",
    response_model=IcaapPillar2ItemRead,
    operation_id="computeIcaapPillar2Item",
    responses=_ERRORS,
)
def compute_icaap_pillar2_item(  # noqa: PLR0913 - the addressed item is five path parts
    bank_id: str,
    cycle_id: UUID,
    item_id: UUID,
    payload: IcaapPillar2Compute,
    db: DbSession,
    access: IcaapEdit,
) -> IcaapPillar2ItemRead:
    """Run the method on this cycle's bound figures and record the revision."""
    _ = bank_id
    return pillar2.compute_item(db, access, cycle_id, item_id, payload)


@router.post(
    f"{_CYCLE}/pillar2/items/{{item_id}}/approve",
    response_model=IcaapPillar2ItemRead,
    operation_id="approveIcaapPillar2Item",
    responses=_ERRORS,
)
def approve_icaap_pillar2_item(  # noqa: PLR0913 - the addressed item is five path parts
    bank_id: str,
    cycle_id: UUID,
    item_id: UUID,
    payload: IcaapPillar2ApprovePayload,
    db: DbSession,
    access: IcaapPillar2Approve,
) -> IcaapPillar2ItemRead:
    """Approve a specific revision. The person who produced it cannot."""
    _ = bank_id
    return pillar2.approve_item(db, access, cycle_id, item_id, payload)


@router.post(
    f"{_CYCLE}/pillar2/items/{{item_id}}/retire",
    response_model=IcaapPillar2ItemRead,
    operation_id="retireIcaapPillar2Item",
    responses=_ERRORS,
)
def retire_icaap_pillar2_item(  # noqa: PLR0913 - the addressed item is five path parts
    bank_id: str,
    cycle_id: UUID,
    item_id: UUID,
    payload: IcaapRetire,
    db: DbSession,
    access: IcaapEdit,
) -> IcaapPillar2ItemRead:
    _ = bank_id
    return pillar2.retire_item(db, access, cycle_id, item_id, payload)


@router.get(
    f"{_CYCLE}/pillar2/items/{{item_id}}/revisions",
    response_model=IcaapPillar2RevisionListRead,
    operation_id="listIcaapPillar2ItemRevisions",
    responses=_ERRORS,
)
def list_icaap_pillar2_item_revisions(
    bank_id: str, cycle_id: UUID, item_id: UUID, db: DbSession, access: IcaapView
) -> IcaapPillar2RevisionListRead:
    """Every figure this item has ever held, newest first. Nothing is rewritten."""
    _ = bank_id
    return pillar2.list_revisions(db, access, cycle_id, item_id)


@router.get(
    f"{_CYCLE}/pillar2/table5",
    response_model=IcaapTable5Read,
    operation_id="getIcaapTable5",
    responses=_ERRORS,
)
def get_icaap_table5(
    bank_id: str, cycle_id: UUID, db: DbSession, access: IcaapView
) -> IcaapTable5Read:
    """Appendix II Table 5: Pillar 1 from the attested run, Pillar 2 from here."""
    _ = bank_id
    return pillar2.get_table5(db, access, cycle_id)


@router.post(
    f"{_CYCLE}/pillar2/capital-plan-proposal",
    response_model=IcaapCapitalPlanProposalRead,
    status_code=status.HTTP_201_CREATED,
    operation_id="proposeIcaapCapitalPlanUpdate",
    responses=_ERRORS,
)
def propose_icaap_capital_plan_update(
    bank_id: str,
    cycle_id: UUID,
    payload: IcaapCapitalPlanProposalCreate,
    db: DbSession,
    access: IcaapCapitalPlanPropose,
) -> IcaapCapitalPlanProposalRead:
    """Carry the approved figures into a capital-plan DRAFT, which somebody else approves."""
    _ = bank_id
    return pillar2.propose_capital_plan_update(db, access, cycle_id, payload)


@router.get(
    f"{_CYCLE}/parameters",
    response_model=IcaapParameterUseListRead,
    operation_id="listIcaapParameters",
    responses=_ERRORS,
)
def list_icaap_parameters(
    bank_id: str, cycle_id: UUID, db: DbSession, access: IcaapView
) -> IcaapParameterUseListRead:
    """Every governed figure this ICAAP reads, at its as-of date, with provenance."""
    _ = bank_id
    cycle = guards.get_cycle_or_404(db, access, cycle_id)
    resolved = params.resolve_p2(db, access.bank, as_of=cycle.as_of_date)
    return IcaapParameterUseListRead(
        as_of=cycle.as_of_date,
        parameters=[
            IcaapParameterUseRead(**use) for use in resolved.uses(params.P2_PARAMETER_CODES)
        ],
        missing=sorted(resolved.missing),
    )
