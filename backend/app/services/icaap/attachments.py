"""ICAAP evidence: uploading a file, proving what it is, and withdrawing it.

The media type is SNIFFED from the bytes, never taken from the client. A file
called ``board-resolution.pdf`` that is actually a macro-enabled workbook is the
attack this closes, and the client's ``Content-Type`` header is not evidence of
anything.

Withdrawal is a separate append-only row rather than a flag on the upload, so
the original upload record — who, when, which bytes — stays exactly as it was
written. The stored object is kept: the tier is retained, and an ICAAP that was
filed with a document must still be able to show what was filed.

Uploads stay possible while a cycle is in review. A reviewer asking for the
Board minutes is the normal path to getting them, and forcing a send-back just
to attach a file would make the review trail worse, not better.
"""

from __future__ import annotations

import hashlib
from io import BytesIO
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import IcaapAccess
from app.db.base import utc_now
from app.models.icaap import (
    IcaapAttachment,
    IcaapAttachmentWithdrawal,
    IcaapBlockBinding,
    IcaapCycle,
    IcaapDataBlock,
)
from app.schemas.icaap import (
    IcaapAttachmentListRead,
    IcaapAttachmentRead,
    IcaapAttachmentRequirementStatusRead,
    IcaapAttachmentWithdraw,
)
from app.services.audit import record_event
from app.services.document_uploads import (
    ALLOWED_MEDIA,
    DOCX_MEDIA_TYPE,
    PDF_MEDIA_TYPE,
    XLSX_MEDIA_TYPE,
    sniff_media_type,
)
from app.services.icaap import guards
from app.storage import ObjectMetadata, StorageClient, StorageLocation

# The sniffer moved to ``app/services/document_uploads.py`` when the package
# attachment plane (ICAAP P3) needed the identical decision: one implementation,
# so a format refused here cannot be smuggled in through the package upload.
# These names stay exported for this module's existing callers and tests.
_PDF = PDF_MEDIA_TYPE
_DOCX = DOCX_MEDIA_TYPE
_XLSX = XLSX_MEDIA_TYPE
_STORAGE_TIER = "outputs"


def _withdrawals(
    db: Session, access: IcaapAccess, cycle: IcaapCycle
) -> dict[UUID, IcaapAttachmentWithdrawal]:
    return {
        row.attachment_id: row
        for row in db.scalars(
            select(IcaapAttachmentWithdrawal).where(
                IcaapAttachmentWithdrawal.organization_id == access.ctx.organization_id,
                IcaapAttachmentWithdrawal.cycle_id == cycle.id,
            )
        )
    }


def _rows(db: Session, access: IcaapAccess, cycle: IcaapCycle) -> list[IcaapAttachment]:
    return list(
        db.scalars(
            select(IcaapAttachment)
            .where(
                IcaapAttachment.organization_id == access.ctx.organization_id,
                IcaapAttachment.cycle_id == cycle.id,
            )
            .order_by(IcaapAttachment.created_at.asc())
        )
    )


def _read(
    row: IcaapAttachment, withdrawal: IcaapAttachmentWithdrawal | None
) -> IcaapAttachmentRead:
    return IcaapAttachmentRead(
        id=row.id,
        cycle_id=row.cycle_id,
        kind=row.kind,
        title=row.title,
        original_filename=row.original_filename,
        media_type=row.media_type,
        byte_size=row.byte_size,
        sha256=row.sha256,
        section_key=row.section_key,
        uploaded_by=row.uploaded_by,
        created_at=row.created_at,
        withdrawn=withdrawal is not None,
        withdrawn_at=None if withdrawal is None else withdrawal.created_at,
        withdrawal_reason=None if withdrawal is None else withdrawal.reason,
    )


def active_attachment_ids(db: Session, access: IcaapAccess, cycle: IcaapCycle) -> set[UUID]:
    withdrawn = set(_withdrawals(db, access, cycle))
    return {row.id for row in _rows(db, access, cycle) if row.id not in withdrawn}


def active_counts(db: Session, access: IcaapAccess, cycle: IcaapCycle) -> dict[str, int]:
    withdrawn = set(_withdrawals(db, access, cycle))
    counts: dict[str, int] = {}
    for row in _rows(db, access, cycle):
        if row.id in withdrawn:
            continue
        counts[row.kind] = counts.get(row.kind, 0) + 1
    return counts


def list_attachments(db: Session, access: IcaapAccess, cycle_id: UUID) -> IcaapAttachmentListRead:
    cycle = guards.get_cycle_or_404(db, access, cycle_id)
    framework = guards.require_framework(cycle)
    from app.services.icaap import cycles as cycles_service  # noqa: PLC0415 - mutual read

    conditions = cycles_service.cycle_conditions(db, access, cycle)
    withdrawals = _withdrawals(db, access, cycle)
    rows = _rows(db, access, cycle)
    counts = active_counts(db, access, cycle)
    requirements = []
    for requirement in framework.attachments:
        applicable = requirement.applies_when is None or requirement.applies_when in conditions
        active = counts.get(requirement.kind, 0)
        requirements.append(
            IcaapAttachmentRequirementStatusRead(
                kind=requirement.kind,
                title=requirement.title,
                gate=requirement.gate,
                min_count=requirement.min_count,
                active_count=active,
                applicable=applicable,
                satisfied=(not applicable) or active >= requirement.min_count,
                media_types=list(requirement.media_types),
            )
        )
    return IcaapAttachmentListRead(
        attachments=[_read(row, withdrawals.get(row.id)) for row in rows],
        requirements=requirements,
    )


def upload_attachment(  # noqa: PLR0913 - an upload is its seven named parts
    db: Session,
    access: IcaapAccess,
    cycle_id: UUID,
    storage: StorageClient,
    *,
    kind: str,
    title: str,
    filename: str,
    content: bytes,
    section_key: str | None = None,
) -> IcaapAttachmentRead:
    cycle = guards.get_cycle_or_404(db, access, cycle_id, for_update=True)
    guards.require_attachable(cycle)
    framework = guards.require_framework(cycle)
    requirement = next((entry for entry in framework.attachments if entry.kind == kind), None)
    if requirement is None:
        raise guards.unprocessable(
            "attachment_kind_unknown",
            "This framework does not ask for that kind of document.",
            kind=kind,
        )
    media_type = sniff_media_type(content)
    if media_type is None or media_type not in requirement.media_types:
        allowed = ", ".join(
            ALLOWED_MEDIA[entry] for entry in requirement.media_types if entry in ALLOWED_MEDIA
        )
        from fastapi import HTTPException, status  # noqa: PLC0415

        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail={
                "error_code": "unsupported_media_type",
                "message": (
                    f"{requirement.title} must be one of: {allowed}."
                    if allowed
                    else f"{requirement.title} accepts no document format."
                ),
                "detected": media_type,
            },
        )
    digest = hashlib.sha256(content).hexdigest()
    withdrawn = set(_withdrawals(db, access, cycle))
    duplicate = next(
        (
            row
            for row in _rows(db, access, cycle)
            if row.kind == kind and row.sha256 == digest and row.id not in withdrawn
        ),
        None,
    )
    if duplicate is not None:
        raise guards.conflict(
            "attachment_duplicate",
            "That exact file is already attached to this ICAAP.",
            attachment_id=str(duplicate.id),
        )

    from app.services.ingestion import bank_slug  # noqa: PLC0415 - avoid an import cycle

    slug = bank_slug(db, access.bank)
    storage.ensure_institution(slug)
    extension = ALLOWED_MEDIA[media_type]
    object_path = f"icaap/{cycle.fiscal_year}/{cycle.id}/{digest}.{extension}"
    location = StorageLocation(slug, _STORAGE_TIER, object_path)
    stored = storage.write(
        location,
        BytesIO(content),
        ObjectMetadata(
            institution_slug=slug,
            tier=_STORAGE_TIER,
            checksum_sha256=digest,
            written_at=utc_now(),
            written_by=str(guards.actor_id(access)),
            as_of_date=cycle.as_of_date.isoformat(),
            source_reference=filename[:200],
        ),
        content_type=media_type,
    )
    row = IcaapAttachment(
        organization_id=cycle.organization_id,
        bank_id=cycle.bank_id,
        cycle_id=cycle.id,
        kind=kind,
        title=title,
        original_filename=filename[:255],
        media_type=media_type,
        byte_size=len(content),
        sha256=digest,
        storage_tier=_STORAGE_TIER,
        object_path=object_path,
        storage_version_id=stored.version_id,
        section_key=section_key,
        uploaded_by=guards.actor_id(access),
    )
    db.add(row)
    db.flush()
    record_event(
        db,
        access.ctx,
        event_type="icaap.attachment.uploaded",
        entity_type="icaap_attachment",
        entity_id=row.id,
        details={
            "cycle_id": str(cycle.id),
            "kind": kind,
            "sha256": digest,
            "media_type": media_type,
            "byte_size": len(content),
            "object_path": object_path,
        },
    )
    db.commit()
    db.refresh(row)
    return _read(row, None)


def withdraw_attachment(
    db: Session,
    access: IcaapAccess,
    cycle_id: UUID,
    attachment_id: UUID,
    payload: IcaapAttachmentWithdraw,
) -> IcaapAttachmentRead:
    cycle = guards.get_cycle_or_404(db, access, cycle_id, for_update=True)
    guards.require_attachable(cycle)
    row = db.scalar(
        select(IcaapAttachment).where(
            IcaapAttachment.id == attachment_id,
            IcaapAttachment.organization_id == access.ctx.organization_id,
            IcaapAttachment.cycle_id == cycle.id,
        )
    )
    if row is None:
        guards.not_found()
    if row.id in _withdrawals(db, access, cycle):
        raise guards.conflict("attachment_withdrawn", "That file has already been withdrawn.")
    in_use = db.scalar(
        select(IcaapBlockBinding)
        .join(IcaapDataBlock, IcaapDataBlock.id == IcaapBlockBinding.block_id)
        .where(
            IcaapBlockBinding.organization_id == access.ctx.organization_id,
            IcaapBlockBinding.cycle_id == cycle.id,
            IcaapBlockBinding.evidence_attachment_id == row.id,
            IcaapDataBlock.retired_at.is_(None),
        )
        .limit(1)
    )
    if in_use is not None:
        raise guards.conflict(
            "attachment_in_use",
            "This file is the evidence for a table in the report. Attach the "
            "replacement to that table first.",
            block_id=str(in_use.block_id),
        )
    db.add(
        IcaapAttachmentWithdrawal(
            organization_id=cycle.organization_id,
            bank_id=cycle.bank_id,
            cycle_id=cycle.id,
            attachment_id=row.id,
            reason=payload.reason,
            withdrawn_by=guards.actor_id(access),
        )
    )
    record_event(
        db,
        access.ctx,
        event_type="icaap.attachment.withdrawn",
        entity_type="icaap_attachment",
        entity_id=row.id,
        details={"cycle_id": str(cycle.id), "kind": row.kind, "reason": payload.reason},
    )
    db.commit()
    db.refresh(row)
    return _read(row, _withdrawals(db, access, cycle).get(row.id))


def prepare_download(
    db: Session, access: IcaapAccess, cycle_id: UUID, attachment_id: UUID
) -> tuple[IcaapAttachment, str]:
    """The row and the institution's storage slug, with the read recorded."""
    cycle = guards.get_cycle_or_404(db, access, cycle_id)
    row = db.scalar(
        select(IcaapAttachment).where(
            IcaapAttachment.id == attachment_id,
            IcaapAttachment.organization_id == access.ctx.organization_id,
            IcaapAttachment.cycle_id == cycle.id,
        )
    )
    if row is None:
        guards.not_found()

    from app.services.ingestion import bank_slug  # noqa: PLC0415 - avoid an import cycle

    slug = bank_slug(db, access.bank)
    record_event(
        db,
        access.ctx,
        event_type="icaap.attachment.downloaded",
        entity_type="icaap_attachment",
        entity_id=row.id,
        details={"cycle_id": str(cycle.id), "kind": row.kind, "sha256": row.sha256},
    )
    db.commit()
    return row, slug


__all__ = [
    "ALLOWED_MEDIA",
    "active_attachment_ids",
    "active_counts",
    "list_attachments",
    "prepare_download",
    "sniff_media_type",
    "upload_attachment",
    "withdraw_attachment",
]
