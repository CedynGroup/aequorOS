from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Protocol
from uuid import UUID

from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.core.config import Settings
from app.integrations.storage.base import ObjectStorage, PresignedUpload, StoredObjectHead
from app.models import Document, RiskAssessment, RiskCase
from app.services import assessments, cases, documents


def upload_payload(
    *,
    case_id: UUID,
    filename: str = "financials.pdf",
    content_type: str = "application/pdf",
    byte_size: int = 1234,
):
    return SimpleNamespace(
        case_id=case_id,
        filename=filename,
        content_type=content_type,
        byte_size=byte_size,
        sha256=None,
    )


def assessment_payload(
    *,
    case_id: UUID,
    assessment_type: str = "vendor_risk",
    name: str = "Initial vendor risk assessment",
):
    return SimpleNamespace(case_id=case_id, assessment_type=assessment_type, name=name)


class MutableObjectStorage(ObjectStorage, Protocol):
    head: StoredObjectHead | None

    def create_presigned_upload_url(
        self,
        *,
        bucket: str,
        object_key: str,
        content_type: str,
        expires_seconds: int,
    ) -> PresignedUpload: ...

    def create_presigned_download_url(
        self,
        *,
        bucket: str,
        object_key: str,
        expires_seconds: int,
    ) -> str: ...

    def head_object(self, *, bucket: str, object_key: str) -> StoredObjectHead | None: ...

    def delete_object(self, *, bucket: str, object_key: str) -> None: ...


@dataclass(frozen=True)
class ServiceFactories:
    db: Session
    storage: MutableObjectStorage
    settings: Settings
    ctx: TenantContext

    def create_case(self, metadata: dict | None = None) -> RiskCase:
        command = cases.CreateCaseCommand(
            title="Vendor case",
            case_type="vendor",
            subject_type=None,
            subject_name=None,
            description=None,
            status="active",
            metadata=metadata or {},
        )
        return cases.create_case(self.db, self.ctx, command)

    def request_upload(self, case_id: UUID, **overrides) -> documents.UploadRequestResult:
        return documents.request_upload(
            self.db,
            self.ctx,
            upload_payload(case_id=case_id, **overrides),
            settings=self.settings,
            storage_client=self.storage,
        )

    def create_uploaded_document(self, case_id: UUID) -> Document:
        upload = self.request_upload(case_id)
        self.storage.head = StoredObjectHead(
            content_type="application/pdf",
            byte_size=1234,
            etag='"etag"',
        )
        return documents.complete_upload(
            self.db,
            self.ctx,
            upload.document_id,
            settings=self.settings,
            storage_client=self.storage,
        )

    def create_parsed_document(self, case_id: UUID) -> Document:
        document = self.create_uploaded_document(case_id)
        documents.request_parse(self.db, self.ctx, document.id)
        return document

    def create_assessment(self, case_id: UUID) -> RiskAssessment:
        return assessments.create_assessment(
            self.db,
            self.ctx,
            assessment_payload(case_id=case_id),
        )
