from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID

from fastapi.testclient import TestClient

from app.integrations.storage.base import StoredObjectHead
from tests.api.helpers import ORG_1, headers

JsonDict = dict[str, Any]


class MutableFakeStorage(Protocol):
    head: StoredObjectHead | None


@dataclass(frozen=True)
class CaseFactory:
    client: TestClient

    def create(  # noqa: PLR0913
        self,
        *,
        org_id: UUID = ORG_1,
        title: str = "Vendor case",
        case_type: str = "vendor",
        status: str = "active",
        subject_type: str | None = None,
        subject_name: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> JsonDict:
        response = self.client.post(
            "/api/v1/cases",
            headers=headers(org_id),
            json={
                "title": title,
                "case_type": case_type,
                "status": status,
                "subject_type": subject_type,
                "subject_name": subject_name,
                "metadata": metadata or {},
            },
        )
        assert response.status_code == 201, response.text
        return response.json()


@dataclass(frozen=True)
class DocumentFactory:
    client: TestClient
    fake_storage: MutableFakeStorage

    def request_upload(
        self,
        *,
        case_id: str,
        filename: str = "financials.pdf",
        content_type: str = "application/pdf",
        byte_size: int = 1234,
    ) -> JsonDict:
        response = self.client.post(
            "/api/v1/documents/upload-request",
            headers=headers(),
            json={
                "case_id": case_id,
                "filename": filename,
                "content_type": content_type,
                "byte_size": byte_size,
            },
        )
        assert response.status_code == 200, response.text
        return response.json()

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

    def create_uploaded(self, *, case_id: str) -> JsonDict:
        upload = self.request_upload(case_id=case_id)
        self.complete_upload(document_id=str(upload["document_id"]))
        return upload

    def create_parsed(self, *, case_id: str) -> JsonDict:
        upload = self.create_uploaded(case_id=case_id)
        response = self.client.post(
            f"/api/v1/documents/{upload['document_id']}/parse",
            headers=headers(),
        )
        assert response.status_code == 200, response.text
        return upload


@dataclass(frozen=True)
class AssessmentFactory:
    client: TestClient

    def create(
        self,
        *,
        case_id: str,
        assessment_type: str = "vendor_risk",
        name: str = "Initial vendor risk assessment",
    ) -> JsonDict:
        response = self.client.post(
            "/api/v1/assessments",
            headers=headers(),
            json={
                "case_id": case_id,
                "assessment_type": assessment_type,
                "name": name,
            },
        )
        assert response.status_code == 201, response.text
        return response.json()
