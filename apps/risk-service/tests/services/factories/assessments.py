from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.models import RiskAssessment
from app.services import assessments
from tests.factories import CreatesServiceCase, assessment_payload


@dataclass(frozen=True)
class AssessmentServiceFactory:
    db: Session
    ctx: TenantContext
    cases: CreatesServiceCase | None = None

    def _case_id(self, case_id: UUID | None) -> UUID:
        if case_id is not None:
            return case_id
        if self.cases is None:
            raise ValueError(
                "case_id is required when AssessmentServiceFactory has no CaseFactory."
            )
        return self.cases.create().id

    def create(self, case_id: UUID | None = None) -> RiskAssessment:
        return assessments.create_assessment(
            self.db,
            self.ctx,
            assessment_payload(case_id=self._case_id(case_id)),
        )
