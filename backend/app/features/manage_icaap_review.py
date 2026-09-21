"""The ICAAP review chain in motion: submit for review, decide a stage, send back."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Path

from app.api.deps import DbSession, IcaapEdit, IcaapReview, IcaapStageDecide, IcaapView
from app.schemas.common import ErrorResponse
from app.schemas.icaap import (
    IcaapReturnCreate,
    IcaapStageDecisionCreate,
    IcaapStagesRead,
    IcaapSubmitForReview,
)
from app.services.icaap import post_freeze, workflow

router = APIRouter(tags=["icaap"])

_ERRORS: dict[int | str, dict[str, Any]] = {
    403: {"model": ErrorResponse},
    404: {"model": ErrorResponse},
    409: {"model": ErrorResponse},
    422: {"model": ErrorResponse},
}
_CYCLE = "/banks/{bank_id}/icaap/cycles/{cycle_id}"


@router.get(
    f"{_CYCLE}/stages",
    response_model=IcaapStagesRead,
    operation_id="getIcaapCycleStages",
    responses=_ERRORS,
)
def get_icaap_cycle_stages(
    bank_id: str, cycle_id: UUID, db: DbSession, access: IcaapView
) -> IcaapStagesRead:
    """The review timeline: who has decided what, on which text, and what is next."""
    _ = bank_id
    return workflow.get_stages(db, access, cycle_id)


@router.post(
    f"{_CYCLE}/submit-for-review",
    response_model=IcaapStagesRead,
    operation_id="submitIcaapCycleForReview",
    responses=_ERRORS,
)
def submit_icaap_cycle_for_review(
    bank_id: str,
    cycle_id: UUID,
    payload: IcaapSubmitForReview,
    db: DbSession,
    access: IcaapEdit,
) -> IcaapStagesRead:
    """Lock the text and start the review. Uncommitted edits are refused."""
    _ = bank_id
    return workflow.submit_for_review(db, access, cycle_id, payload)


@router.post(
    f"{_CYCLE}/stages/{{seq}}/decisions",
    response_model=IcaapStagesRead,
    operation_id="decideIcaapStage",
    responses=_ERRORS,
)
def decide_icaap_stage(  # noqa: PLR0913 - a stage decision is addressed by three path parts
    bank_id: str,
    cycle_id: UUID,
    payload: IcaapStageDecisionCreate,
    db: DbSession,
    access: IcaapStageDecide,
    seq: int = Path(ge=1, le=20),
) -> IcaapStagesRead:
    """Record a review, an approval or a send-back at this stage of the chain."""
    _ = bank_id
    return workflow.decide_stage(db, access, cycle_id, seq, payload)


@router.post(
    f"{_CYCLE}/return",
    response_model=IcaapStagesRead,
    operation_id="returnIcaapCycle",
    responses=_ERRORS,
)
def return_icaap_cycle(
    bank_id: str,
    cycle_id: UUID,
    payload: IcaapReturnCreate,
    db: DbSession,
    access: IcaapReview,
) -> IcaapStagesRead:
    """Send a FROZEN ICAAP back to a review stage, unlinking its package."""
    _ = bank_id
    return post_freeze.return_cycle(db, access, cycle_id, payload)
