"""ICAAP frameworks, cycles and readiness."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, status

from app.api.deps import DbSession, IcaapCreate, IcaapEdit, IcaapView
from app.schemas.common import ErrorResponse
from app.schemas.icaap import (
    IcaapBlockTypeListRead,
    IcaapCycleArchive,
    IcaapCycleCreate,
    IcaapCycleListRead,
    IcaapCycleRead,
    IcaapCycleRebase,
    IcaapCycleUpdate,
    IcaapFrameworkListRead,
    IcaapFrameworkRead,
    IcaapReadinessRead,
)
from app.services.icaap import cycles, readiness

router = APIRouter(tags=["icaap"])

_NOT_FOUND = {
    "model": ErrorResponse,
    "description": (
        "The ICAAP workspace is not enabled, the institution is outside the ICAAP "
        "regime, or it does not exist in the caller's organization."
    ),
}
_FORBIDDEN = {
    "model": ErrorResponse,
    "description": "The caller holds no scoped ICAAP binding for the institution.",
}


@router.get(
    "/banks/{bank_id}/icaap/frameworks",
    response_model=IcaapFrameworkListRead,
    operation_id="listIcaapFrameworks",
    responses={403: _FORBIDDEN, 404: _NOT_FOUND},
)
def list_icaap_frameworks(bank_id: str, db: DbSession, access: IcaapView) -> IcaapFrameworkListRead:
    """The regulator's ICAAP instruments this institution may assess against."""
    _ = bank_id
    return cycles.list_frameworks(db, access)


@router.get(
    "/banks/{bank_id}/icaap/frameworks/{framework_code}/versions/{version}",
    response_model=IcaapFrameworkRead,
    operation_id="getIcaapFramework",
    responses={403: _FORBIDDEN, 404: _NOT_FOUND},
)
def get_icaap_framework(
    bank_id: str, framework_code: str, version: str, db: DbSession, access: IcaapView
) -> IcaapFrameworkRead:
    _ = bank_id
    return cycles.get_framework(db, access, framework_code, version)


@router.get(
    "/banks/{bank_id}/icaap/block-types",
    response_model=IcaapBlockTypeListRead,
    operation_id="listIcaapBlockTypes",
    responses={403: _FORBIDDEN, 404: _NOT_FOUND},
)
def list_icaap_block_types(
    bank_id: str, db: DbSession, access: IcaapView
) -> IcaapBlockTypeListRead:
    _ = (bank_id, db, access)
    return cycles.list_block_types()


@router.get(
    "/banks/{bank_id}/icaap/cycles",
    response_model=IcaapCycleListRead,
    operation_id="listIcaapCycles",
    responses={403: _FORBIDDEN, 404: _NOT_FOUND},
)
def list_icaap_cycles(
    bank_id: str,
    db: DbSession,
    access: IcaapView,
    fiscal_year: Annotated[int | None, Query()] = None,
    include_archived: Annotated[bool, Query()] = False,
) -> IcaapCycleListRead:
    _ = bank_id
    return cycles.list_cycles(
        db, access, fiscal_year=fiscal_year, include_archived=include_archived
    )


@router.post(
    "/banks/{bank_id}/icaap/cycles",
    response_model=IcaapCycleRead,
    status_code=status.HTTP_201_CREATED,
    operation_id="createIcaapCycle",
    responses={403: _FORBIDDEN, 404: _NOT_FOUND, 409: {"model": ErrorResponse}},
)
def create_icaap_cycle(
    bank_id: str, payload: IcaapCycleCreate, db: DbSession, access: IcaapCreate
) -> IcaapCycleRead:
    _ = bank_id
    return cycles.create_cycle(db, access, payload)


@router.get(
    "/banks/{bank_id}/icaap/cycles/{cycle_id}",
    response_model=IcaapCycleRead,
    operation_id="getIcaapCycle",
    responses={403: _FORBIDDEN, 404: _NOT_FOUND},
)
def get_icaap_cycle(
    bank_id: str, cycle_id: UUID, db: DbSession, access: IcaapView
) -> IcaapCycleRead:
    _ = bank_id
    return cycles.get_cycle(db, access, cycle_id)


@router.patch(
    "/banks/{bank_id}/icaap/cycles/{cycle_id}",
    response_model=IcaapCycleRead,
    operation_id="updateIcaapCycle",
    responses={403: _FORBIDDEN, 404: _NOT_FOUND, 409: {"model": ErrorResponse}},
)
def update_icaap_cycle(
    bank_id: str,
    cycle_id: UUID,
    payload: IcaapCycleUpdate,
    db: DbSession,
    access: IcaapEdit,
) -> IcaapCycleRead:
    _ = bank_id
    return cycles.update_cycle(db, access, cycle_id, payload)


@router.post(
    "/banks/{bank_id}/icaap/cycles/{cycle_id}/archive",
    response_model=IcaapCycleRead,
    operation_id="archiveIcaapCycle",
    responses={403: _FORBIDDEN, 404: _NOT_FOUND, 409: {"model": ErrorResponse}},
)
def archive_icaap_cycle(
    bank_id: str,
    cycle_id: UUID,
    payload: IcaapCycleArchive,
    db: DbSession,
    access: IcaapEdit,
) -> IcaapCycleRead:
    _ = bank_id
    return cycles.archive_cycle(db, access, cycle_id, payload)


@router.post(
    "/banks/{bank_id}/icaap/cycles/{cycle_id}/rebase",
    response_model=IcaapCycleRead,
    status_code=status.HTTP_201_CREATED,
    operation_id="rebaseIcaapCycle",
    responses={403: _FORBIDDEN, 404: _NOT_FOUND, 409: {"model": ErrorResponse}},
)
def rebase_icaap_cycle(
    bank_id: str,
    cycle_id: UUID,
    payload: IcaapCycleRebase,
    db: DbSession,
    access: IcaapCreate,
) -> IcaapCycleRead:
    """Move a cycle onto a newer version of the regulator's text."""
    _ = bank_id
    return cycles.rebase_cycle(db, access, cycle_id, payload)


@router.get(
    "/banks/{bank_id}/icaap/cycles/{cycle_id}/readiness",
    response_model=IcaapReadinessRead,
    operation_id="getIcaapReadiness",
    responses={403: _FORBIDDEN, 404: _NOT_FOUND},
)
def get_icaap_readiness(
    bank_id: str, cycle_id: UUID, db: DbSession, access: IcaapView
) -> IcaapReadinessRead:
    """Everything still standing between this ICAAP and a freeze."""
    _ = bank_id
    return readiness.get_readiness(db, access, cycle_id)
