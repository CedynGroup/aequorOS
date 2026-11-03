from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from fastapi import HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.case.service.cases import get_case_or_404
from app.core.config import Settings
from app.core.content_digest import ContentReadOverrun, DeclaredDigestError, digest_matches
from app.db.base import utc_now
from app.domain.risk_constants import ALLOWED_UPLOAD_CONTENT_TYPES
from app.integrations.storage.base import ObjectStorage, StoredObjectHead
from app.integrations.storage.s3 import get_object_storage
from app.models import Document, DocumentChunk, Job, StoredObject
from app.services import job_queue
from app.services.audit import record_event


@dataclass(frozen=True)
class UploadRequestResult:
    document_id: UUID
    upload_url: str
    method: str
    headers: dict[str, str]
    expires_in_seconds: int


@dataclass(frozen=True)
class DownloadUrlResult:
    url: str
    expires_in_seconds: int


@dataclass(frozen=True)
class ParseResult:
    job_id: UUID
    document_id: UUID
    status: str


def get_document_or_404(
    db: Session, organization_id: str, document_id: UUID, *, for_update: bool = False
) -> Document:
    query = select(Document).where(
        Document.id == document_id,
        Document.organization_id == organization_id,
    )
    if for_update:
        query = query.with_for_update().execution_options(populate_existing=True)
    document = db.scalar(query)
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found.")
    return document


def get_stored_object_or_404(
    db: Session, organization_id: str, stored_object_id: UUID, *, for_update: bool = False
) -> StoredObject:
    query = select(StoredObject).where(
        StoredObject.id == stored_object_id,
        StoredObject.organization_id == organization_id,
    )
    if for_update:
        query = query.with_for_update().execution_options(populate_existing=True)
    stored_object = db.scalar(query)
    if stored_object is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Stored object not found."
        )
    return stored_object


def request_upload(
    db: Session,
    ctx: TenantContext,
    payload: Any,
    *,
    settings: Settings,
    storage_client: ObjectStorage,
) -> UploadRequestResult:
    get_case_or_404(db, ctx.organization_id, payload.case_id)
    if payload.content_type not in ALLOWED_UPLOAD_CONTENT_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported upload content type."
        )
    if payload.byte_size > settings.risk_max_upload_bytes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Upload exceeds maximum size."
        )

    document_id = uuid4()
    object_key = f"orgs/{ctx.organization_id}/documents/{document_id}/original"
    stored_object = StoredObject(
        organization_id=ctx.organization_id,
        provider=settings.risk_storage_backend,
        bucket=settings.risk_s3_bucket,
        object_key=object_key,
        content_type=payload.content_type,
        byte_size=payload.byte_size,
        sha256=payload.sha256,
        status="pending_upload",
        created_by=ctx.actor_user_id,
    )
    db.add(stored_object)
    db.flush()

    document = Document(
        id=document_id,
        organization_id=ctx.organization_id,
        case_id=payload.case_id,
        stored_object_id=stored_object.id,
        filename=payload.filename,
        source="upload",
        status="upload_requested",
        parse_status="not_started",
        uploaded_by=ctx.actor_user_id,
    )
    db.add(document)
    db.flush()

    upload = storage_client.create_presigned_upload_url(
        bucket=settings.risk_s3_bucket,
        object_key=object_key,
        content_type=payload.content_type,
        expires_seconds=settings.risk_s3_presign_expires_seconds,
    )
    record_event(
        db,
        ctx,
        event_type="document.upload_requested",
        entity_type="document",
        entity_id=str(document.id),
    )
    db.commit()
    return UploadRequestResult(
        document_id=document.id,
        upload_url=upload.url,
        method=upload.method,
        headers=upload.headers,
        expires_in_seconds=upload.expires_in_seconds,
    )


def _require_pending_upload(document: Document, stored_object: StoredObject) -> None:
    if (
        document.deleted_at is not None
        or document.status == "deleted"
        or stored_object.deleted_at is not None
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Deleted documents cannot complete upload.",
        )
    if document.status != "upload_requested" or stored_object.status != "pending_upload":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Document upload is not pending.",
        )


def _digest_matches(declared: str, observed: str) -> bool:
    """Compare digests, refusing a stored declaration that is not one.

    A row written before the request schema validated declarations can hold
    anything. Treating an unparseable value as a match would accept it, and
    treating it as an error would surface as a server fault, so it is a
    mismatch: the object cannot be shown to be what was declared.
    """
    try:
        return digest_matches(declared, observed)
    except DeclaredDigestError:
        return False


@dataclass(frozen=True)
class _StagedSnapshot:
    """What storage reported for a staged upload and, once copied, its snapshot.

    ``finalized_key`` is set only when the copy was made, ``head`` is the report
    for that copy when it could be re-headed, and ``digest`` is filled only when
    a checksum was declared and the copy could still be accepted.
    """

    finalized_key: str | None
    head: StoredObjectHead | None
    digest: tuple[int, str] | None


@dataclass(frozen=True)
class _VerifiedUpload:
    """The snapshot completion records: its locator, metadata and measured size."""

    finalized_key: str
    head: StoredObjectHead
    byte_size: int


def _read_declared_digest(
    storage_client: ObjectStorage,
    *,
    bucket: str,
    finalized_key: str,
    declared_digest: str | None,
    observed_size: int | None,
) -> tuple[int, str] | None:
    """Hash the snapshot under its own reported size when a checksum was declared."""
    if declared_digest is None or observed_size is None:
        return None
    try:
        return storage_client.read_object_digest(
            bucket=bucket, object_key=finalized_key, max_bytes=observed_size
        )
    except ContentReadOverrun as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded object is larger than the size storage reported.",
        ) from exc


def _snapshot_staged_upload(
    storage_client: ObjectStorage,
    *,
    bucket: str,
    object_key: str,
    declared_digest: str | None,
    max_bytes: int,
) -> _StagedSnapshot:
    """Copy a staged object that could still be accepted to a server-only key.

    Only an object that is present, sized and within the cap is copied and read:
    an object that is already too large is refused against the cap a few lines
    later, so reading it first would spend the whole read before saying no —
    the cap exists to bound exactly this work. Nothing here refuses; completion
    owns the refusals and their messages.
    """
    head = storage_client.head_object(bucket=bucket, object_key=object_key)
    if head is None or head.byte_size is None or head.byte_size > max_bytes:
        return _StagedSnapshot(finalized_key=None, head=head, digest=None)
    finalized_key = storage_client.finalize_upload(bucket=bucket, object_key=object_key)
    if finalized_key is None:
        # The copy source is gone, so the head that found it no longer describes
        # anything that landed.
        return _StagedSnapshot(finalized_key=None, head=None, digest=None)
    snapshot_head = storage_client.head_object(bucket=bucket, object_key=finalized_key)
    return _StagedSnapshot(
        finalized_key=finalized_key,
        head=snapshot_head,
        digest=_read_declared_digest(
            storage_client,
            bucket=bucket,
            finalized_key=finalized_key,
            declared_digest=declared_digest,
            observed_size=snapshot_head.byte_size if snapshot_head is not None else None,
        ),
    )


def _verify_landed_upload(
    snapshot: _StagedSnapshot,
    *,
    declared_size: int | None,
    declared_digest: str | None,
    max_bytes: int,
) -> _VerifiedUpload:
    """Return the recorded snapshot, refusing anything the client did not declare.

    A declaration is provenance only once it has been compared with the object
    that landed, so every way of failing to prove a match is a refusal rather
    than a recorded declaration.
    """
    head = snapshot.head
    if head is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Uploaded object was not found."
        )
    observed_size = head.byte_size
    if observed_size is None:
        # Without an observed size there is nothing to compare the declaration
        # against, and recording it unverified is the defect this guard prevents.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Storage did not report the uploaded object's size.",
        )
    if observed_size > max_bytes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded object exceeds maximum size.",
        )
    if declared_size is not None and observed_size != declared_size:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded object does not match the declared size.",
        )
    finalized_key = snapshot.finalized_key
    if finalized_key is None:
        # Only an accepted object is copied, so a reported object without a
        # snapshot is not something completion can record.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Uploaded object was not found."
        )
    if declared_digest is not None:
        digest = snapshot.digest
        if (
            digest is None
            or digest[0] != observed_size
            or not _digest_matches(declared_digest, digest[1])
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Uploaded object does not match the declared checksum.",
            )
    return _VerifiedUpload(finalized_key=finalized_key, head=head, byte_size=observed_size)


def complete_upload(
    db: Session,
    ctx: TenantContext,
    document_id: UUID,
    *,
    settings: Settings,
    storage_client: ObjectStorage,
) -> Document:
    document = get_document_or_404(db, ctx.organization_id, document_id)
    stored_object = get_stored_object_or_404(db, ctx.organization_id, document.stored_object_id)
    _require_pending_upload(document, stored_object)
    bucket, object_key = stored_object.bucket, stored_object.object_key
    declared_size, declared_digest = stored_object.byte_size, stored_object.sha256
    db.commit()
    snapshot = _StagedSnapshot(finalized_key=None, head=None, digest=None)
    completed = False
    try:
        # Every storage call runs with no transaction open, so a slow head, copy
        # or digest read cannot spend the idle-in-transaction budget.
        snapshot = _snapshot_staged_upload(
            storage_client,
            bucket=bucket,
            object_key=object_key,
            declared_digest=declared_digest,
            max_bytes=settings.risk_max_upload_bytes,
        )
        document = get_document_or_404(db, ctx.organization_id, document_id, for_update=True)
        stored_object = get_stored_object_or_404(
            db, ctx.organization_id, document.stored_object_id, for_update=True
        )
        _require_pending_upload(document, stored_object)
        verified = _verify_landed_upload(
            snapshot,
            declared_size=declared_size,
            declared_digest=declared_digest,
            max_bytes=settings.risk_max_upload_bytes,
        )
        stored_object.object_key = verified.finalized_key
        stored_object.status = "available"
        stored_object.etag = verified.head.etag
        stored_object.byte_size = verified.byte_size
        stored_object.content_type = verified.head.content_type or stored_object.content_type
        stored_object.version_id = verified.head.version_id
        document.status = "uploaded"
        document.uploaded_at = datetime.now(UTC)
        record_event(
            db,
            ctx,
            event_type="document.upload_completed",
            entity_type="document",
            entity_id=str(document.id),
        )
        db.commit()
        completed = True
        return document
    finally:
        if snapshot.finalized_key is not None and not completed:
            db.rollback()
            storage_client.delete_object(bucket=bucket, object_key=snapshot.finalized_key)


def list_case_documents(db: Session, ctx: TenantContext, case_id: UUID) -> list[Document]:
    get_case_or_404(db, ctx.organization_id, case_id)
    return list(
        db.scalars(
            select(Document).where(
                Document.organization_id == ctx.organization_id,
                Document.case_id == case_id,
                Document.deleted_at.is_(None),
            )
        )
    )


def create_download_url(
    db: Session,
    ctx: TenantContext,
    document_id: UUID,
    *,
    settings: Settings,
    storage_client: ObjectStorage,
) -> DownloadUrlResult:
    document = get_document_or_404(db, ctx.organization_id, document_id)
    stored_object = get_stored_object_or_404(db, ctx.organization_id, document.stored_object_id)
    if (
        document.deleted_at is not None
        or stored_object.deleted_at is not None
        or document.status != "uploaded"
        or stored_object.status != "available"
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Document is not available."
        )
    url = storage_client.create_presigned_download_url(
        bucket=stored_object.bucket,
        object_key=stored_object.object_key,
        expires_seconds=settings.risk_s3_presign_expires_seconds,
    )
    return DownloadUrlResult(url=url, expires_in_seconds=settings.risk_s3_presign_expires_seconds)


def request_parse(db: Session, ctx: TenantContext, document_id: UUID) -> ParseResult:
    document = get_document_or_404(db, ctx.organization_id, document_id)
    if document.deleted_at is not None or document.status == "deleted":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Deleted documents cannot be parsed."
        )
    if document.status != "uploaded":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Document must be uploaded first."
        )
    job = Job(
        organization_id=ctx.organization_id,
        job_type="document_parse",
        status="queued",
        entity_type="document",
        entity_id=str(document.id),
    )
    db.add(job)
    document.parse_status = "pending"
    db.flush()
    record_event(
        db,
        ctx,
        event_type="document.parse_requested",
        entity_type="document",
        entity_id=str(document.id),
    )
    try:
        run_parse_stub(db, ctx, document, job)
    except Exception as exc:
        document.parse_status = "failed"
        document.parse_error = str(exc)
        job.status = "failed"
        job.error = str(exc)
        job.completed_at = utc_now()
        record_event(
            db,
            ctx,
            event_type="document.parse_failed",
            entity_type="document",
            entity_id=document.id,
        )
    db.commit()
    return ParseResult(job_id=job.id, document_id=document.id, status=job.status)


def run_parse_stub(db: Session, ctx: TenantContext, document: Document, job: Job) -> None:
    job.status = "running"
    job.started_at = utc_now()
    document.parse_status = "parsing"
    existing_chunk = db.scalar(
        select(DocumentChunk).where(
            DocumentChunk.organization_id == ctx.organization_id,
            DocumentChunk.document_id == document.id,
            DocumentChunk.chunk_index == 0,
        )
    )
    if existing_chunk is None:
        db.add(
            DocumentChunk(
                organization_id=ctx.organization_id,
                document_id=document.id,
                chunk_index=0,
                page_start=1,
                page_end=1,
                text=f"Phase 1 parsed placeholder for {document.filename}",
                token_count=8,
                metadata_={"parser": "phase_1_stub"},
            )
        )
    document.parse_status = "parsed"
    document.parse_error = None
    job.status = "completed"
    job.progress = {"parsed_chunks": 1}
    job.completed_at = utc_now()
    record_event(
        db, ctx, event_type="document.parsed", entity_type="document", entity_id=document.id
    )


def delete_document(
    db: Session,
    ctx: TenantContext,
    document_id: UUID,
) -> Document:
    """Commit logical deletion and its cleanup job together, before any storage wait.

    The job is the outbox: storage failures cannot restore a live document, and
    a failed database commit cannot destroy its object. Physical cleanup retries
    through the worker; the API's existing deleted response means logical deletion.
    """
    document = get_document_or_404(db, ctx.organization_id, document_id, for_update=True)
    if document.deleted_at is not None:
        db.commit()
        return document
    stored_object = get_stored_object_or_404(db, ctx.organization_id, document.stored_object_id)
    document.status = "deleted"
    document.deleted_at = utc_now()
    stored_object.status = "pending_deletion"
    stored_object.deleted_at = document.deleted_at
    job_queue.enqueue(
        db,
        ctx.organization_id,
        "document_delete",
        entity_type="document",
        entity_id=document.id,
    )
    record_event(
        db, ctx, event_type="document.deleted", entity_type="document", entity_id=document.id
    )
    db.commit()
    return document


def run_document_delete(session: Session, job: Job) -> None:
    """Delete only a committed pending object belonging to this job's tenant."""
    if job.entity_id is None:
        raise ValueError("Document deletion job requires a document id.")
    organization_id = job.organization_id
    target = (
        session.execute(
            select(StoredObject.id, StoredObject.bucket, StoredObject.object_key)
            .join(Document, Document.stored_object_id == StoredObject.id)
            .where(
                Document.id == UUID(job.entity_id),
                Document.organization_id == organization_id,
                Document.status == "deleted",
                Document.deleted_at.is_not(None),
                StoredObject.organization_id == organization_id,
                StoredObject.status == "pending_deletion",
                StoredObject.deleted_at.is_not(None),
            )
        )
        .tuples()
        .one_or_none()
    )
    # Capture only immutable locator values; commit must not leave an ORM lazy
    # read reopening a transaction during storage construction or deletion.
    session.commit()
    if target is None:
        return
    object_id, bucket, object_key = target
    storage_client = get_object_storage()
    storage_client.delete_object(bucket=bucket, object_key=object_key)
    staging_key, finalized, _ = object_key.partition("/finalized/")
    if finalized:
        storage_client.delete_object(bucket=bucket, object_key=staging_key)
    session.execute(
        update(StoredObject)
        .where(
            StoredObject.id == object_id,
            StoredObject.organization_id == organization_id,
            StoredObject.status == "pending_deletion",
        )
        .values(status="deleted")
    )
    # The worker commits this acknowledgement with the job's succeeded state.
