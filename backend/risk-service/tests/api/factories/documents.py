from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from fastapi.testclient import TestClient

from app.integrations.storage.base import StoredObjectHead
from app.schemas.documents import UploadRequestResponse
from tests.api.helpers import headers
from tests.factories import CreatesApiCase, upload_payload


class MutableFakeStorage(Protocol):
    head: StoredObjectHead | None


@dataclass(frozen=True)
class DocumentFactory:
    client: TestClient
    fake_storage: MutableFakeStorage
    cases: CreatesApiCase | None = None

    def _case_id(self, case_id: str | UUID | None) -> UUID:
        if case_id is not None:
            return UUID(str(case_id))
        if self.cases is None:
            raise ValueError("case_id is required when DocumentFactory has no CaseFactory.")
        return self.cases.create().id

    def request_upload(
        self,
        *,
        case_id: str | UUID | None = None,
        filename: str = "financials.pdf",
        content_type: str = "application/pdf",
        byte_size: int = 1234,
    ) -> UploadRequestResponse:
        resolved_case_id = self._case_id(case_id)
        response = self.client.post(
            "/api/v1/documents/upload-request",
            headers=headers(),
            json=upload_payload(
                case_id=resolved_case_id,
                filename=filename,
                content_type=content_type,
                byte_size=byte_size,
            ).api_json(),
        )
        assert response.status_code == 200, response.text
        return UploadRequestResponse.model_validate(response.json())

    def complete_upload(self, *, document_id: str) -> None:
        self.fake_storage.head = StoredObjectHead(
            content_type="application/pdf",
            byte_size=1234,
            etag='"etag"',
        )
        response = self.client.post(
            f"/api/v1/documents/{document_id}/complete-upload",
            headers=headers(),
        )
        assert response.status_code == 200, response.text

    def create_uploaded(self, *, case_id: str | UUID | None = None) -> UploadRequestResponse:
        upload = self.request_upload(case_id=case_id)
        self.complete_upload(document_id=str(upload.document_id))
        return upload

    def create_parsed(self, *, case_id: str | UUID | None = None) -> UploadRequestResponse:
        upload = self.create_uploaded(case_id=case_id)
        response = self.client.post(
            f"/api/v1/documents/{upload.document_id}/parse",
            headers=headers(),
        )
        assert response.status_code == 200, response.text
        return upload
