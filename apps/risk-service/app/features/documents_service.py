from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.core.config import Settings
from app.db.base import utc_now
from app.features.audit import record_event
from app.features.cases_service import get_case_or_404
from app.features.constants import ALLOWED_UPLOAD_CONTENT_TYPES
from app.integrations.storage.base import ObjectStorage
from app.models import Document, DocumentChunk, Job, StoredObject


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


def get_document_or_404(db: Session, organization_id: UUID, document_id: UUID) -> Document:
    document = db.scalar(
        select(Document).where(
            Document.id == document_id,
            Document.organization_id == organization_id,
        )
    )
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found.")
    return document


def get_stored_object_or_404(
    db: Session, organization_id: UUID, stored_object_id: UUID
) -> StoredObject:
    stored_object = db.scalar(
        select(StoredObject).where(
            StoredObject.id == stored_object_id,
            StoredObject.organization_id == organization_id,
        )
    )
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
        entity_id=document.id,
    )
    db.commit()
    return UploadRequestResult(
        document_id=document.id,
        upload_url=upload.url,
        method=upload.method,
        headers=upload.headers,
        expires_in_seconds=upload.expires_in_seconds,
    )


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
    if document.deleted_at is not None or document.status == "deleted":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Deleted documents cannot complete upload.",
        )
    if document.status != "upload_requested" or stored_object.status != "pending_upload":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Document upload is not pending.",
        )
    head = storage_client.head_object(
        bucket=stored_object.bucket, object_key=stored_object.object_key
    )
    if head is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Uploaded object was not found."
        )
    if head.byte_size is not None and head.byte_size > settings.risk_max_upload_bytes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded object exceeds maximum size.",
        )
    stored_object.status = "available"
    stored_object.etag = head.etag
    stored_object.byte_size = head.byte_size or stored_object.byte_size
    stored_object.content_type = head.content_type or stored_object.content_type
    stored_object.version_id = head.version_id
    document.status = "uploaded"
    document.uploaded_at = datetime.now(UTC)
    record_event(
        db,
        ctx,
        event_type="document.upload_completed",
        entity_type="document",
        entity_id=document.id,
    )
    db.commit()
    return document


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
    if document.status != "uploaded" or stored_object.status != "available":
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
        entity_id=document.id,
    )
    db.add(job)
    document.parse_status = "pending"
    db.flush()
    record_event(
        db,
        ctx,
        event_type="document.parse_requested",
        entity_type="document",
        entity_id=document.id,
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
    *,
    storage_client: ObjectStorage,
) -> Document:
    document = get_document_or_404(db, ctx.organization_id, document_id)
    stored_object = get_stored_object_or_404(db, ctx.organization_id, document.stored_object_id)
    document.status = "deleted"
    document.deleted_at = utc_now()
    stored_object.status = "deleted"
    stored_object.deleted_at = utc_now()
    storage_client.delete_object(bucket=stored_object.bucket, object_key=stored_object.object_key)
    record_event(
        db, ctx, event_type="document.deleted", entity_type="document", entity_id=document.id
    )
    db.commit()
    db.refresh(document)
    return document
