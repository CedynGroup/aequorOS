"""ICAAP evidence: upload, list, download, withdraw."""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile, status
from fastapi.responses import StreamingResponse

from app.api.deps import DbSession, IcaapEdit, IcaapView
from app.core.config import get_settings
from app.schemas.common import ErrorResponse
from app.schemas.icaap import (
    IcaapAttachmentListRead,
    IcaapAttachmentRead,
    IcaapAttachmentWithdraw,
)
from app.services.icaap import attachments
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
_ATTACHMENTS = "/banks/{bank_id}/icaap/cycles/{cycle_id}/attachments"
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
    _ATTACHMENTS,
    response_model=IcaapAttachmentListRead,
    operation_id="listIcaapAttachments",
    responses=_ERRORS,
)
def list_icaap_attachments(
    bank_id: str, cycle_id: UUID, db: DbSession, access: IcaapView
) -> IcaapAttachmentListRead:
    """The files attached, and which of the framework's documents are still missing."""
    _ = bank_id
    return attachments.list_attachments(db, access, cycle_id)


@router.post(
    _ATTACHMENTS,
    response_model=IcaapAttachmentRead,
    status_code=status.HTTP_201_CREATED,
    operation_id="uploadIcaapAttachment",
    responses=_ERRORS,
)
async def upload_icaap_attachment(  # noqa: PLR0913 - a multipart upload is its parts
    bank_id: str,
    cycle_id: UUID,
    db: DbSession,
    access: IcaapEdit,
    storage: IcaapStorage,
    file: UploadFile,
    kind: Annotated[str, Form(max_length=40)],
    title: Annotated[str, Form(max_length=200)],
    section_key: Annotated[str | None, Form(max_length=60)] = None,
) -> IcaapAttachmentRead:
    """Upload evidence. The file type is read from the bytes, not the filename."""
    _ = bank_id
    limit = get_settings().icaap.max_attachment_bytes
    content = await file.read(limit + 1)
    if len(content) > limit:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail={
                "error_code": "attachment_too_large",
                "message": f"The file is larger than the {limit // 1_000_000} MB limit.",
            },
        )
    if not content:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"error_code": "attachment_empty", "message": "The file is empty."},
        )
    return attachments.upload_attachment(
        db,
        access,
        cycle_id,
        storage,
        kind=kind,
        title=title,
        filename=file.filename or "upload",
        content=content,
        section_key=section_key,
    )


@router.get(
    f"{_ATTACHMENTS}/{{attachment_id}}/download",
    operation_id="downloadIcaapAttachment",
    response_class=StreamingResponse,
    responses={
        **_ERRORS,
        200: {"content": {"application/octet-stream": _BINARY}},
    },
)
def download_icaap_attachment(  # noqa: PLR0913 - the addressed file is five path parts
    bank_id: str,
    cycle_id: UUID,
    attachment_id: UUID,
    db: DbSession,
    access: IcaapView,
    storage: IcaapStorage,
) -> StreamingResponse:
    _ = bank_id
    row, slug = attachments.prepare_download(db, access, cycle_id, attachment_id)
    _object, stream = storage.read(
        StorageLocation(slug, row.storage_tier, row.object_path),  # pyright: ignore[reportArgumentType]
        row.storage_version_id,
    )
    return StreamingResponse(
        stream,
        media_type=row.media_type,
        headers={"Content-Disposition": f'attachment; filename="{row.original_filename}"'},
    )


@router.post(
    f"{_ATTACHMENTS}/{{attachment_id}}/withdraw",
    response_model=IcaapAttachmentRead,
    operation_id="withdrawIcaapAttachment",
    responses=_ERRORS,
)
def withdraw_icaap_attachment(  # noqa: PLR0913 - the addressed file is five path parts
    bank_id: str,
    cycle_id: UUID,
    attachment_id: UUID,
    payload: IcaapAttachmentWithdraw,
    db: DbSession,
    access: IcaapEdit,
) -> IcaapAttachmentRead:
    """Withdraw a file. The upload record and the stored object are kept."""
    _ = bank_id
    return attachments.withdraw_attachment(db, access, cycle_id, attachment_id, payload)
