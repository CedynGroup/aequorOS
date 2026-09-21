"""Freezing an ICAAP, and the one view of whether it can be filed."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, status

from app.api.deps import DbSession, IcaapCreate, IcaapFreeze, IcaapView
from app.schemas.common import ErrorResponse
from app.schemas.icaap import (
    IcaapCloneCreate,
    IcaapCycleRead,
    IcaapFilingRead,
    IcaapFreezeCreate,
    IcaapFreezeRead,
    IcaapPreflightRead,
)
from app.services.icaap import clone, filing, freeze

router = APIRouter(tags=["icaap"])

_ERRORS: dict[int | str, dict[str, Any]] = {
    403: {"model": ErrorResponse},
    404: {"model": ErrorResponse},
    409: {"model": ErrorResponse},
    422: {"model": ErrorResponse},
}
_CYCLE = "/banks/{bank_id}/icaap/cycles/{cycle_id}"


@router.get(
    f"{_CYCLE}/freeze-preflight",
    response_model=IcaapPreflightRead,
    operation_id="getIcaapFreezePreflight",
    responses=_ERRORS,
)
def get_icaap_freeze_preflight(
    bank_id: str, cycle_id: UUID, db: DbSession, access: IcaapView
) -> IcaapPreflightRead:
    """Everything between this ICAAP and a sealed filing, each with its reason."""
    _ = bank_id
    return freeze.get_preflight(db, access, cycle_id)


@router.post(
    f"{_CYCLE}/freeze",
    response_model=IcaapFreezeRead,
    status_code=status.HTTP_201_CREATED,
    operation_id="freezeIcaapCycle",
    responses=_ERRORS,
)
def freeze_icaap_cycle(
    bank_id: str,
    cycle_id: UUID,
    payload: IcaapFreezeCreate,
    db: DbSession,
    access: IcaapFreeze,
) -> IcaapFreezeRead:
    """Seal the report and mint its filing package. One transaction, or nothing."""
    _ = bank_id
    return freeze.freeze_cycle(db, access, cycle_id, payload)


@router.get(
    f"{_CYCLE}/filing",
    response_model=IcaapFilingRead,
    operation_id="getIcaapFiling",
    responses=_ERRORS,
)
def get_icaap_filing(
    bank_id: str, cycle_id: UUID, db: DbSession, access: IcaapView
) -> IcaapFilingRead:
    """Signatures, documents and whether this report can be submitted."""
    _ = bank_id
    return filing.get_filing(db, access, cycle_id)


@router.post(
    f"{_CYCLE}/clone",
    response_model=IcaapCycleRead,
    status_code=status.HTTP_201_CREATED,
    operation_id="cloneIcaapCycle",
    responses=_ERRORS,
)
def clone_icaap_cycle(
    bank_id: str,
    cycle_id: UUID,
    payload: IcaapCloneCreate,
    db: DbSession,
    access: IcaapCreate,
) -> IcaapCycleRead:
    """¶74: a correction of a filed ICAAP, or an update after a material change."""
    _ = bank_id
    return clone.clone_cycle(db, access, cycle_id, payload)
