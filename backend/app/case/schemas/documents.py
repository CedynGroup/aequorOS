from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, field_validator

from app.core.content_digest import DeclaredDigestError, normalize_sha256


class UploadRequest(BaseModel):
    case_id: UUID
    filename: str
    content_type: str
    byte_size: int
    sha256: str | None = None

    @field_validator("sha256")
    @classmethod
    def _declared_digest(cls, value: str | None) -> str | None:
        """Refuse a declaration that could never be verified.

        An upload's digest is an attestation the client makes before sending
        anything. Storing a value that is not a SHA-256 would record provenance
        that nothing can ever match, so the shape is checked where the request
        is shaped rather than when the object arrives.
        """
        if value is None:
            return None
        try:
            return normalize_sha256(value)
        except DeclaredDigestError as error:
            raise ValueError(str(error)) from error


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
    organization_id: str
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
