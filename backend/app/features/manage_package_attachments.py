"""Documents filed WITH a return: list, upload, download, withdraw.

The endpoint that makes ``return_signing_policies.required_attachments``
enforceable. Until it existed a policy could name a Board resolution and nothing
could satisfy it, so the requirement passed silently on every submission
(audit C-6).

Authorization is the package plane's, not a new one: reads take
``require_package_view`` (404 hides a gated family's existence) and writes take
``require_package_edit``. An ICAAP package therefore admits only a holder of an
exact CAPITAL/CONFIDENTIAL binding; every other family keeps its scalar ladder.
"""

from __future__ import annotations

import json
from pathlib import PurePosixPath
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Form, HTTPException, UploadFile, status
from fastapi.responses import StreamingResponse

from app.api.deps import DbSession, PackageEdit, PackageView
from app.core.config import get_settings
from app.features.ingest_data import IngestionStorage
from app.features.manage_icaap_attachments import IcaapStorage
from app.schemas.common import ErrorResponse
from app.schemas.regulatory_reporting import (
    PackageAttachmentListRead,
    PackageAttachmentRead,
    PackageAttachmentWithdraw,
)
from app.services.regulatory_reporting import attachments as package_attachments
from app.storage.client import StorageLocation, StorageNotFoundError

router = APIRouter(tags=["regulatory-reporting"])

_ERRORS: dict[int | str, dict[str, Any]] = {
    403: {"model": ErrorResponse},
    404: {"model": ErrorResponse},
    409: {"model": ErrorResponse},
    413: {"model": ErrorResponse},
    415: {"model": ErrorResponse},
    422: {"model": ErrorResponse},
    503: {"model": ErrorResponse},
}
_ATTACHMENTS = "/banks/{bank_id}/regulatory-packages/{package_id}/attachments"


def _parse_attributes(raw: str | None) -> dict[str, Any]:
    """The kind's own facts, sent as a JSON object in a multipart field.

    ``multipart/form-data`` carries no types, so a structured field arrives as
    text. Malformed text is refused rather than silently dropped: a Board
    resolution whose date did not survive the wire is not a Board resolution.
    """
    if raw is None or not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "error_code": "attachment_attributes_invalid",
                "message": "The document's details must be a JSON object.",
            },
        ) from exc
    if not isinstance(parsed, dict):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "error_code": "attachment_attributes_invalid",
                "message": "The document's details must be a JSON object.",
            },
        )
    return parsed


@router.get(
    _ATTACHMENTS,
    response_model=PackageAttachmentListRead,
    operation_id="listPackageAttachments",
    responses=_ERRORS,
)
def list_package_attachments(
    bank_id: str, package_id: UUID, db: DbSession, access: PackageView
) -> PackageAttachmentListRead:
    """What is attached, and which documents this return still needs."""
    _ = (bank_id, package_id)
    from app.services.attestation.workflow import package_policy  # noqa: PLC0415

    policy = package_policy(db, access.ctx, access.package)
    return package_attachments.list_attachments(db, access.ctx, access.package, policy)


@router.post(
    _ATTACHMENTS,
    response_model=PackageAttachmentRead,
    status_code=status.HTTP_201_CREATED,
    operation_id="uploadPackageAttachment",
    responses=_ERRORS,
)
async def upload_package_attachment(  # noqa: PLR0913 - a multipart upload is its parts
    bank_id: str,
    package_id: UUID,
    db: DbSession,
    access: PackageEdit,
    storage: IcaapStorage,
    file: UploadFile,
    kind: Annotated[str, Form(max_length=40)],
    title: Annotated[str, Form(max_length=200)],
    attributes: Annotated[str | None, Form()] = None,
) -> PackageAttachmentRead:
    """Attach one document, identified from its own bytes.

    The body is read to ONE byte past the limit and refused there, the same way
    both ICAAP upload routes do it: a cap applied after the whole body is in
    memory is not a cap, it is a description of what was already allocated. The
    service keeps its own check as the invariant — this route is not the only
    caller — but nothing beyond ``limit + 1`` bytes is ever held here.
    """
    _ = (bank_id, package_id)
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
    return package_attachments.upload_package_attachment(
        db,
        access.ctx,
        access.bank,
        access.package,
        storage,
        kind=kind,
        title=title,
        filename=file.filename or "document",
        content=content,
        attributes=_parse_attributes(attributes),
        max_bytes=limit,
    )


@router.get(
    _ATTACHMENTS + "/{attachment_id}/download",
    response_class=StreamingResponse,
    operation_id="downloadPackageAttachment",
    responses=_ERRORS,
)
def download_package_attachment(  # noqa: PLR0913 - path + session + access + storage
    bank_id: str,
    package_id: UUID,
    attachment_id: UUID,
    db: DbSession,
    access: PackageView,
    storage: IngestionStorage,
) -> StreamingResponse:
    """Stream one filed document from the outputs tier."""
    _ = (bank_id, package_id)
    from app.services.ingestion import bank_slug  # noqa: PLC0415 - avoid an import cycle

    row = next(
        (
            entry
            for entry in package_attachments.active_attachments(db, access.ctx, access.package)
            if entry.id == attachment_id
        ),
        None,
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not Found")
    location = StorageLocation(
        institution_slug=bank_slug(db, access.bank),
        tier="outputs",
        object_path=row.object_path,
    )
    try:
        _descriptor, stream = storage.read(location)
    except StorageNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="The document's stored object was not found in the outputs tier.",
        ) from exc
    filename = PurePosixPath(row.original_filename).name or "document"
    return StreamingResponse(
        stream,
        media_type=row.media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post(
    _ATTACHMENTS + "/{attachment_id}/withdraw",
    response_model=PackageAttachmentRead,
    operation_id="withdrawPackageAttachment",
    responses=_ERRORS,
)
def withdraw_package_attachment(  # noqa: PLR0913 - path + payload + session + access
    bank_id: str,
    package_id: UUID,
    attachment_id: UUID,
    payload: PackageAttachmentWithdraw,
    db: DbSession,
    access: PackageEdit,
) -> PackageAttachmentRead:
    """Record that a document should not have been filed. Never an edit."""
    _ = (bank_id, package_id)
    return package_attachments.withdraw_package_attachment(
        db, access.ctx, access.package, attachment_id, reason=payload.reason
    )
