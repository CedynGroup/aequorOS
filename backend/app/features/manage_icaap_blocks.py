"""ICAAP data blocks: linking figures, refreshing them, keeping them deliberately."""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Query, status

from app.api.deps import DbSession, IcaapEdit, IcaapView
from app.schemas.common import ErrorResponse
from app.schemas.icaap import (
    IcaapBlockBindingListRead,
    IcaapBlockRefreshRead,
    IcaapDataBlockCreate,
    IcaapDataBlockListRead,
    IcaapDataBlockPin,
    IcaapDataBlockRead,
    IcaapDataBlockRefresh,
    IcaapDataBlockRetire,
    IcaapManualTablePut,
)
from app.services.icaap import blocks

router = APIRouter(tags=["icaap"])

_ERRORS: dict[int | str, dict[str, Any]] = {
    403: {"model": ErrorResponse},
    404: {"model": ErrorResponse},
    409: {"model": ErrorResponse},
    422: {"model": ErrorResponse},
}
_BLOCKS = "/banks/{bank_id}/icaap/cycles/{cycle_id}/blocks"


@router.get(
    _BLOCKS,
    response_model=IcaapDataBlockListRead,
    operation_id="listIcaapDataBlocks",
    responses=_ERRORS,
)
def list_icaap_data_blocks(
    bank_id: str,
    cycle_id: UUID,
    db: DbSession,
    access: IcaapView,
    include_payload: Annotated[bool, Query()] = False,
) -> IcaapDataBlockListRead:
    _ = bank_id
    return blocks.list_blocks(db, access, cycle_id, include_payload=include_payload)


@router.post(
    _BLOCKS,
    response_model=IcaapDataBlockRead,
    status_code=status.HTTP_201_CREATED,
    operation_id="createIcaapDataBlock",
    responses=_ERRORS,
)
def create_icaap_data_block(
    bank_id: str,
    cycle_id: UUID,
    payload: IcaapDataBlockCreate,
    db: DbSession,
    access: IcaapEdit,
) -> IcaapDataBlockRead:
    _ = bank_id
    return blocks.create_block(db, access, cycle_id, payload)


@router.get(
    f"{_BLOCKS}/{{block_id}}",
    response_model=IcaapDataBlockRead,
    operation_id="getIcaapDataBlock",
    responses=_ERRORS,
)
def get_icaap_data_block(
    bank_id: str, cycle_id: UUID, block_id: UUID, db: DbSession, access: IcaapView
) -> IcaapDataBlockRead:
    _ = bank_id
    return blocks.get_block(db, access, cycle_id, block_id)


@router.post(
    f"{_BLOCKS}/{{block_id}}/refresh",
    response_model=IcaapBlockRefreshRead,
    operation_id="refreshIcaapDataBlock",
    responses=_ERRORS,
)
def refresh_icaap_data_block(  # noqa: PLR0913 - the addressed block is five path parts
    bank_id: str,
    cycle_id: UUID,
    block_id: UUID,
    payload: IcaapDataBlockRefresh,
    db: DbSession,
    access: IcaapEdit,
) -> IcaapBlockRefreshRead:
    """Re-read the source. Identical figures write nothing."""
    _ = bank_id
    return blocks.refresh_block(db, access, cycle_id, block_id, payload)


@router.get(
    f"{_BLOCKS}/{{block_id}}/bindings",
    response_model=IcaapBlockBindingListRead,
    operation_id="listIcaapBlockBindings",
    responses=_ERRORS,
)
def list_icaap_block_bindings(
    bank_id: str, cycle_id: UUID, block_id: UUID, db: DbSession, access: IcaapView
) -> IcaapBlockBindingListRead:
    _ = bank_id
    return blocks.list_bindings(db, access, cycle_id, block_id)


@router.post(
    f"{_BLOCKS}/{{block_id}}/pin",
    response_model=IcaapDataBlockRead,
    operation_id="pinIcaapDataBlock",
    responses=_ERRORS,
)
def pin_icaap_data_block(  # noqa: PLR0913 - the addressed block is five path parts
    bank_id: str,
    cycle_id: UUID,
    block_id: UUID,
    payload: IcaapDataBlockPin,
    db: DbSession,
    access: IcaapEdit,
) -> IcaapDataBlockRead:
    """Keep the current figures although newer ones exist, with the reason recorded."""
    _ = bank_id
    return blocks.pin_block(db, access, cycle_id, block_id, payload)


@router.delete(
    f"{_BLOCKS}/{{block_id}}/pin",
    response_model=IcaapDataBlockRead,
    operation_id="unpinIcaapDataBlock",
    responses=_ERRORS,
)
def unpin_icaap_data_block(
    bank_id: str, cycle_id: UUID, block_id: UUID, db: DbSession, access: IcaapEdit
) -> IcaapDataBlockRead:
    _ = bank_id
    return blocks.unpin_block(db, access, cycle_id, block_id)


@router.put(
    f"{_BLOCKS}/{{block_id}}/manual-table",
    response_model=IcaapDataBlockRead,
    operation_id="putIcaapManualTable",
    responses=_ERRORS,
)
def put_icaap_manual_table(  # noqa: PLR0913 - the addressed block is five path parts
    bank_id: str,
    cycle_id: UUID,
    block_id: UUID,
    payload: IcaapManualTablePut,
    db: DbSession,
    access: IcaapEdit,
) -> IcaapDataBlockRead:
    _ = bank_id
    return blocks.put_manual_table(db, access, cycle_id, block_id, payload)


@router.post(
    f"{_BLOCKS}/{{block_id}}/retire",
    response_model=IcaapDataBlockRead,
    operation_id="retireIcaapDataBlock",
    responses=_ERRORS,
)
def retire_icaap_data_block(  # noqa: PLR0913 - the addressed block is five path parts
    bank_id: str,
    cycle_id: UUID,
    block_id: UUID,
    payload: IcaapDataBlockRetire,
    db: DbSession,
    access: IcaapEdit,
) -> IcaapDataBlockRead:
    _ = bank_id
    return blocks.retire_block(db, access, cycle_id, block_id, payload)
