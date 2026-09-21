"""¶82: choosing what the bank publishes of its ICAAP, and approving it."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter

from app.api.deps import DbSession, IcaapDisclosureApprove, IcaapEdit, IcaapView
from app.schemas.common import ErrorResponse
from app.schemas.icaap import (
    IcaapDisclosureDecision,
    IcaapDisclosurePut,
    IcaapDisclosureRead,
    IcaapDisclosureSubmit,
)
from app.services.icaap import disclosure

router = APIRouter(tags=["icaap"])

_ERRORS: dict[int | str, dict[str, Any]] = {
    403: {"model": ErrorResponse},
    404: {"model": ErrorResponse},
    409: {"model": ErrorResponse},
    422: {"model": ErrorResponse},
}
_DISCLOSURE = "/banks/{bank_id}/icaap/cycles/{cycle_id}/disclosure"


@router.get(
    _DISCLOSURE,
    response_model=IcaapDisclosureRead,
    operation_id="getIcaapDisclosure",
    responses=_ERRORS,
)
def get_icaap_disclosure(
    bank_id: str, cycle_id: UUID, db: DbSession, access: IcaapView
) -> IcaapDisclosureRead:
    """What is selected for publication, and what was withheld from it."""
    _ = bank_id
    return disclosure.get_disclosure(db, access, cycle_id)


@router.put(
    _DISCLOSURE,
    response_model=IcaapDisclosureRead,
    operation_id="putIcaapDisclosure",
    responses=_ERRORS,
)
def put_icaap_disclosure(
    bank_id: str,
    cycle_id: UUID,
    payload: IcaapDisclosurePut,
    db: DbSession,
    access: IcaapEdit,
) -> IcaapDisclosureRead:
    """Choose which sections to publish. Nothing is public unless chosen."""
    _ = bank_id
    return disclosure.put_disclosure(db, access, cycle_id, payload)


@router.post(
    f"{_DISCLOSURE}/submit",
    response_model=IcaapDisclosureRead,
    operation_id="submitIcaapDisclosure",
    responses=_ERRORS,
)
def submit_icaap_disclosure(
    bank_id: str,
    cycle_id: UUID,
    payload: IcaapDisclosureSubmit,
    db: DbSession,
    access: IcaapEdit,
) -> IcaapDisclosureRead:
    _ = bank_id
    return disclosure.submit_disclosure(db, access, cycle_id, payload)


@router.post(
    f"{_DISCLOSURE}/decision",
    response_model=IcaapDisclosureRead,
    operation_id="decideIcaapDisclosure",
    responses=_ERRORS,
)
def decide_icaap_disclosure(
    bank_id: str,
    cycle_id: UUID,
    payload: IcaapDisclosureDecision,
    db: DbSession,
    access: IcaapDisclosureApprove,
) -> IcaapDisclosureRead:
    """Approve or reject the publication. Never by whoever chose it."""
    _ = bank_id
    return disclosure.decide_disclosure(db, access, cycle_id, payload)
