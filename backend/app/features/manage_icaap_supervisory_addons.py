"""Supervisory capital add-ons: record the letter, confirm it, download it.

Bank-scoped rather than cycle-scoped: a letter in force spans ICAAP cycles.
Never public: there is no flag to set, and the disclosure regime refuses the
block that carries these.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import StreamingResponse

from app.api.deps import DbSession, IcaapAddonApprove, IcaapCreate, IcaapView
from app.core.config import get_settings
from app.schemas.common import ErrorResponse
from app.schemas.icaap_risk_capital import (
    IcaapReason,
    IcaapSupervisoryAddonListRead,
    IcaapSupervisoryAddonRead,
)
from app.services.icaap import supervisory_addons
from app.storage import StorageClient, StorageLocation, StorageValidationError
from app.storage.config import StorageRetiredError
from app.storage.factory import get_storage_client

router = APIRouter(tags=["icaap"])

_ERRORS: dict[int | str, dict[str, Any]] = {
    403: {"model": ErrorResponse},
    404: {"model": ErrorResponse},
    409: {"model": ErrorResponse},
    413: {"model": ErrorResponse},
    415: {"model": ErrorResponse},
    422: {"model": ErrorResponse},
    503: {"model": ErrorResponse},
}
_ADDONS = "/banks/{bank_id}/icaap/supervisory-addons"
_BINARY = {"schema": {"type": "string", "format": "binary"}}


def get_icaap_storage() -> StorageClient:
    """The storage engine, or 503 when it is unconfigured or retired."""
    try:
        return get_storage_client()
    except (StorageValidationError, StorageRetiredError, NotImplementedError) as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Storage is unavailable: {exc}",
        ) from exc


IcaapStorage = Annotated[StorageClient, Depends(get_icaap_storage)]


@router.get(
    _ADDONS,
    response_model=IcaapSupervisoryAddonListRead,
    operation_id="listIcaapSupervisoryAddons",
    responses=_ERRORS,
)
def list_icaap_supervisory_addons(
    bank_id: str,
    db: DbSession,
    access: IcaapView,
    as_of: Annotated[date | None, Query()] = None,
    include_inactive: Annotated[bool, Query()] = False,
) -> IcaapSupervisoryAddonListRead:
    _ = bank_id
    return supervisory_addons.list_addons(
        db, access, as_of=as_of, include_inactive=include_inactive
    )


@router.post(
    _ADDONS,
    response_model=IcaapSupervisoryAddonRead,
    status_code=status.HTTP_201_CREATED,
    operation_id="createIcaapSupervisoryAddon",
    responses=_ERRORS,
)
async def create_icaap_supervisory_addon(  # noqa: PLR0913 - a multipart record is its parts
    bank_id: str,
    db: DbSession,
    access: IcaapCreate,
    storage: IcaapStorage,
    letter: UploadFile,
    letter_reference: Annotated[str, Form(max_length=120)],
    letter_date: Annotated[date, Form()],
    effective_from: Annotated[date, Form()],
    applies_to_basis: Annotated[str, Form(pattern=r"^(solo|consolidated|both)$")],
    basis: Annotated[str, Form(max_length=32)],
    basis_value: Annotated[Decimal, Form()],
    table5_row: Annotated[str | None, Form(max_length=40)] = None,
    component_key: Annotated[str | None, Form(max_length=60)] = None,
    description: Annotated[str | None, Form(max_length=4000)] = None,
    supersedes_addon_id: Annotated[UUID | None, Form()] = None,
) -> IcaapSupervisoryAddonRead:
    """Record the supervisor's letter as a DRAFT. A second person confirms it."""
    _ = bank_id
    limit = get_settings().icaap.max_attachment_bytes
    content = await letter.read(limit + 1)
    if len(content) > limit:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail={
                "error_code": "attachment_too_large",
                "message": f"The letter is larger than the {limit // 1_000_000} MB limit.",
            },
        )
    if not content:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"error_code": "attachment_empty", "message": "The letter is empty."},
        )
    return supervisory_addons.create_addon(
        db,
        access,
        storage,
        letter_reference=letter_reference,
        letter_date=letter_date,
        effective_from=effective_from,
        applies_to_basis=applies_to_basis,
        basis=basis,
        basis_value=basis_value,
        table5_row=table5_row,
        component_key=component_key,
        description=description,
        filename=letter.filename or "letter",
        content=content,
        supersedes_addon_id=supersedes_addon_id,
    )


@router.post(
    f"{_ADDONS}/{{addon_id}}/confirm",
    response_model=IcaapSupervisoryAddonRead,
    operation_id="confirmIcaapSupervisoryAddon",
    responses=_ERRORS,
)
def confirm_icaap_supervisory_addon(
    bank_id: str,
    addon_id: UUID,
    payload: IcaapReason,
    db: DbSession,
    access: IcaapAddonApprove,
) -> IcaapSupervisoryAddonRead:
    """Confirm the add-on. The person who recorded it cannot."""
    _ = bank_id
    return supervisory_addons.confirm_addon(db, access, addon_id, payload)


@router.post(
    f"{_ADDONS}/{{addon_id}}/withdraw",
    response_model=IcaapSupervisoryAddonRead,
    operation_id="withdrawIcaapSupervisoryAddon",
    responses=_ERRORS,
)
def withdraw_icaap_supervisory_addon(
    bank_id: str,
    addon_id: UUID,
    payload: IcaapReason,
    db: DbSession,
    access: IcaapAddonApprove,
) -> IcaapSupervisoryAddonRead:
    _ = bank_id
    return supervisory_addons.withdraw_addon(db, access, addon_id, payload)


@router.get(
    f"{_ADDONS}/{{addon_id}}/letter",
    operation_id="downloadIcaapSupervisoryAddonLetter",
    response_class=StreamingResponse,
    responses={**_ERRORS, 200: {"content": {"application/octet-stream": _BINARY}}},
)
def download_icaap_supervisory_addon_letter(
    bank_id: str,
    addon_id: UUID,
    db: DbSession,
    access: IcaapView,
    storage: IcaapStorage,
) -> StreamingResponse:
    _ = bank_id
    row, slug = supervisory_addons.prepare_letter_download(db, access, addon_id)
    _object, stream = storage.read(
        StorageLocation(slug, row.letter_storage_tier, row.letter_object_path),  # pyright: ignore[reportArgumentType]
        row.letter_storage_version_id,
    )
    return StreamingResponse(
        stream,
        media_type=row.letter_media_type,
        headers={"Content-Disposition": f'attachment; filename="{row.letter_original_filename}"'},
    )
