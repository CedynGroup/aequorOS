from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from fastapi.testclient import TestClient

from app.schemas.cases import CaseRead
from app.schemas.common import JsonObject
from tests.api.helpers import ORG_1, headers
from tests.factories import case_payload


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
        description: str | None = None,
        metadata: JsonObject | None = None,
    ) -> CaseRead:
        payload = case_payload(
            title=title,
            case_type=case_type,
            status=status,
            subject_type=subject_type,
            subject_name=subject_name,
            description=description,
            metadata=metadata,
        )
        response = self.client.post(
            "/api/v1/cases",
            headers=headers(org_id),
            json=payload.api_json(),
        )
        assert response.status_code == 201, response.text
        return CaseRead.model_validate(response.json())
