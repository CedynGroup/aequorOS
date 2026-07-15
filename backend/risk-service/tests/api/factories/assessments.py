from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from fastapi.testclient import TestClient

from app.schemas.assessments import AssessmentRead
from tests.api.helpers import headers
from tests.factories import CreatesApiCase, assessment_payload


@dataclass(frozen=True)
class AssessmentFactory:
    client: TestClient
    cases: CreatesApiCase | None = None

    def _case_id(self, case_id: str | UUID | None) -> UUID:
        if case_id is not None:
            return UUID(str(case_id))
        if self.cases is None:
            raise ValueError("case_id is required when AssessmentFactory has no CaseFactory.")
        return self.cases.create().id

    def create(
        self,
        *,
        case_id: str | UUID | None = None,
        assessment_type: str = "vendor_risk",
        name: str = "Initial vendor risk assessment",
    ) -> AssessmentRead:
        resolved_case_id = self._case_id(case_id)
        response = self.client.post(
            "/api/v1/assessments",
            headers=headers(),
            json=assessment_payload(
                case_id=resolved_case_id,
                assessment_type=assessment_type,
                name=name,
            ).api_json(),
        )
        assert response.status_code == 201, response.text
        return AssessmentRead.model_validate(response.json())
