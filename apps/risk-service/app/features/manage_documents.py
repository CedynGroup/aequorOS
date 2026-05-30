from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter

from app.api.deps import DbSession, Storage, Tenant
from app.core.config import get_settings
from app.models import Document
from app.schemas.documents import (
    CompleteUploadResponse,
    DocumentRead,
    DownloadUrlResponse,
    ParseResponse,
    ParseStatusResponse,
    UploadRequest,
    UploadRequestResponse,
)
from app.services import documents as documents_service

router = APIRouter(tags=["documents"])


@router.post("/documents/upload-request", response_model=UploadRequestResponse)
def request_upload(
    payload: UploadRequest,
    db: DbSession,
    ctx: Tenant,
    storage_client: Storage,
) -> UploadRequestResponse:
    result = documents_service.request_upload(
        db,
        ctx,
        payload,
        settings=get_settings(),
        storage_client=storage_client,
    )
    return UploadRequestResponse(**result.__dict__)


@router.post("/documents/{document_id}/complete-upload", response_model=CompleteUploadResponse)
def complete_upload(
    document_id: UUID,
    db: DbSession,
    ctx: Tenant,
    storage_client: Storage,
) -> CompleteUploadResponse:
    document = documents_service.complete_upload(
        db,
        ctx,
        document_id,
        settings=get_settings(),
        storage_client=storage_client,
    )
    return CompleteUploadResponse(document_id=document.id, status=document.status)


@router.get("/documents/{document_id}", response_model=DocumentRead)
def get_document(document_id: UUID, db: DbSession, ctx: Tenant) -> Document:
    return documents_service.get_document_or_404(db, ctx.organization_id, document_id)


@router.get("/cases/{case_id}/documents", response_model=list[DocumentRead])
def list_case_documents(case_id: UUID, db: DbSession, ctx: Tenant) -> list[Document]:
    return documents_service.list_case_documents(db, ctx, case_id)


@router.get("/documents/{document_id}/download-url", response_model=DownloadUrlResponse)
def download_url(
    document_id: UUID,
    db: DbSession,
    ctx: Tenant,
    storage_client: Storage,
) -> DownloadUrlResponse:
    result = documents_service.create_download_url(
        db,
        ctx,
        document_id,
        settings=get_settings(),
        storage_client=storage_client,
    )
    return DownloadUrlResponse(**result.__dict__)


@router.post("/documents/{document_id}/parse", response_model=ParseResponse)
def parse_document(document_id: UUID, db: DbSession, ctx: Tenant) -> ParseResponse:
    result = documents_service.request_parse(db, ctx, document_id)
    return ParseResponse(**result.__dict__)


@router.get("/documents/{document_id}/parse-status", response_model=ParseStatusResponse)
def parse_status(document_id: UUID, db: DbSession, ctx: Tenant) -> ParseStatusResponse:
    document = documents_service.get_document_or_404(db, ctx.organization_id, document_id)
    return ParseStatusResponse(
        document_id=document.id,
        parse_status=document.parse_status,
        parse_error=document.parse_error,
    )


@router.delete("/documents/{document_id}", response_model=DocumentRead)
def delete_document(
    document_id: UUID,
    db: DbSession,
    ctx: Tenant,
    storage_client: Storage,
) -> Document:
    return documents_service.delete_document(
        db,
        ctx,
        document_id,
        storage_client=storage_client,
    )
