from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class UploadRequest(BaseModel):
    case_id: UUID
    filename: str
    content_type: str
    byte_size: int
    sha256: str | None = None


class UploadRequestResponse(BaseModel):
    document_id: UUID
    upload_url: str
    method: str
    headers: dict[str, str]
    expires_in_seconds: int


class CompleteUploadResponse(BaseModel):
    document_id: UUID
    status: str


class DocumentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    organization_id: UUID
    case_id: UUID
    stored_object_id: UUID
    filename: str
    document_type: str | None
    source: str
    status: str
    parse_status: str
    parse_error: str | None
    uploaded_by: UUID | None
    uploaded_at: datetime | None
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None


class DownloadUrlResponse(BaseModel):
    url: str
    expires_in_seconds: int


class ParseResponse(BaseModel):
    job_id: UUID
    document_id: UUID
    status: str


class ParseStatusResponse(BaseModel):
    document_id: UUID
    parse_status: str
    parse_error: str | None
