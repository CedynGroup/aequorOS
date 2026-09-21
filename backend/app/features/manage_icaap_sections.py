"""ICAAP section text, versions and the requirement checklist."""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Path, status

from app.api.deps import DbSession, IcaapEdit, IcaapView
from app.schemas.common import ErrorResponse
from app.schemas.icaap import (
    IcaapRequirementStateUpdate,
    IcaapSectionCommit,
    IcaapSectionListRead,
    IcaapSectionRead,
    IcaapSectionVersionListRead,
    IcaapSectionVersionRead,
    IcaapSectionWorkingSave,
)
from app.services.icaap import sections

router = APIRouter(tags=["icaap"])

_SECTION_KEY = Annotated[str, Path(pattern=r"^[a-z][a-z0-9_]{1,59}$")]
_ITEM_ID = Annotated[str, Path(pattern=r"^[a-z0-9_]{2,40}$")]
_ERRORS: dict[int | str, dict[str, Any]] = {
    403: {"model": ErrorResponse},
    404: {"model": ErrorResponse},
    409: {"model": ErrorResponse},
    422: {"model": ErrorResponse},
}


@router.get(
    "/banks/{bank_id}/icaap/cycles/{cycle_id}/sections",
    response_model=IcaapSectionListRead,
    operation_id="listIcaapSections",
    responses=_ERRORS,
)
def list_icaap_sections(
    bank_id: str, cycle_id: UUID, db: DbSession, access: IcaapView
) -> IcaapSectionListRead:
    _ = bank_id
    return sections.list_sections(db, access, cycle_id)


@router.get(
    "/banks/{bank_id}/icaap/cycles/{cycle_id}/sections/{section_key}",
    response_model=IcaapSectionRead,
    operation_id="getIcaapSection",
    responses=_ERRORS,
)
def get_icaap_section(
    bank_id: str, cycle_id: UUID, section_key: _SECTION_KEY, db: DbSession, access: IcaapView
) -> IcaapSectionRead:
    _ = bank_id
    return sections.get_section(db, access, cycle_id, section_key)


@router.put(
    "/banks/{bank_id}/icaap/cycles/{cycle_id}/sections/{section_key}/working",
    response_model=IcaapSectionRead,
    operation_id="saveIcaapSectionWorking",
    responses=_ERRORS,
)
def save_icaap_section_working(  # noqa: PLR0913 - the addressed section is five parts
    bank_id: str,
    cycle_id: UUID,
    section_key: _SECTION_KEY,
    payload: IcaapSectionWorkingSave,
    db: DbSession,
    access: IcaapEdit,
) -> IcaapSectionRead:
    """Autosave. A save based on a stale revision is refused, never merged."""
    _ = bank_id
    return sections.save_working(db, access, cycle_id, section_key, payload)


@router.post(
    "/banks/{bank_id}/icaap/cycles/{cycle_id}/sections/{section_key}/versions",
    response_model=IcaapSectionVersionRead,
    status_code=status.HTTP_201_CREATED,
    operation_id="commitIcaapSectionVersion",
    responses=_ERRORS,
)
def commit_icaap_section_version(  # noqa: PLR0913 - the addressed section is five parts
    bank_id: str,
    cycle_id: UUID,
    section_key: _SECTION_KEY,
    payload: IcaapSectionCommit,
    db: DbSession,
    access: IcaapEdit,
) -> IcaapSectionVersionRead:
    _ = bank_id
    return sections.commit_version(db, access, cycle_id, section_key, payload)


@router.get(
    "/banks/{bank_id}/icaap/cycles/{cycle_id}/sections/{section_key}/versions",
    response_model=IcaapSectionVersionListRead,
    operation_id="listIcaapSectionVersions",
    responses=_ERRORS,
)
def list_icaap_section_versions(
    bank_id: str, cycle_id: UUID, section_key: _SECTION_KEY, db: DbSession, access: IcaapView
) -> IcaapSectionVersionListRead:
    _ = bank_id
    return sections.list_versions(db, access, cycle_id, section_key)


@router.get(
    "/banks/{bank_id}/icaap/cycles/{cycle_id}/sections/{section_key}/versions/{version_no}",
    response_model=IcaapSectionVersionRead,
    operation_id="getIcaapSectionVersion",
    responses=_ERRORS,
)
def get_icaap_section_version(  # noqa: PLR0913 - the addressed version is five path parts
    bank_id: str,
    cycle_id: UUID,
    section_key: _SECTION_KEY,
    version_no: int,
    db: DbSession,
    access: IcaapView,
) -> IcaapSectionVersionRead:
    _ = bank_id
    return sections.get_version(db, access, cycle_id, section_key, version_no)


@router.put(
    "/banks/{bank_id}/icaap/cycles/{cycle_id}/sections/{section_key}/requirements/{item_id}",
    response_model=IcaapSectionRead,
    operation_id="setIcaapRequirementState",
    responses=_ERRORS,
)
def set_icaap_requirement_state(  # noqa: PLR0913 - the addressed item is six path parts
    bank_id: str,
    cycle_id: UUID,
    section_key: _SECTION_KEY,
    item_id: _ITEM_ID,
    payload: IcaapRequirementStateUpdate,
    db: DbSession,
    access: IcaapEdit,
) -> IcaapSectionRead:
    _ = bank_id
    return sections.set_requirement_state(db, access, cycle_id, section_key, item_id, payload)
