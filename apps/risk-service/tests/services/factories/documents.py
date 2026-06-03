from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.core.config import Settings
from app.integrations.storage.base import StoredObjectHead
from app.models import Document
from app.services import documents
from tests.factories import CreatesServiceCase, upload_payload
from tests.services.factories.shared import MutableObjectStorage


@dataclass(frozen=True)
class DocumentServiceFactory:
    db: Session
    storage: MutableObjectStorage
    settings: Settings
    ctx: TenantContext
    cases: CreatesServiceCase | None = None

    def _case_id(self, case_id: UUID | None) -> UUID:
        if case_id is not None:
            return case_id
        if self.cases is None:
            raise ValueError("case_id is required when DocumentServiceFactory has no CaseFactory.")
        return self.cases.create().id

    def request_upload(
        self,
        case_id: UUID | None = None,
        *,
        filename: str = "financials.pdf",
        content_type: str = "application/pdf",
        byte_size: int = 1234,
        sha256: str | None = None,
    ) -> documents.UploadRequestResult:
        resolved_case_id = self._case_id(case_id)
        return documents.request_upload(
            self.db,
            self.ctx,
            upload_payload(
                case_id=resolved_case_id,
                filename=filename,
                content_type=content_type,
                byte_size=byte_size,
                sha256=sha256,
            ),
            settings=self.settings,
            storage_client=self.storage,
        )

    def create_uploaded(self, case_id: UUID | None = None) -> Document:
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

    def create_parsed(self, case_id: UUID | None = None) -> Document:
        document = self.create_uploaded(case_id)
        documents.request_parse(self.db, self.ctx, document.id)
        return document
