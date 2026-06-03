from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.models import RiskCase
from app.schemas.common import JsonObject
from app.services import cases
from tests.factories import case_payload


@dataclass(frozen=True)
class CaseServiceFactory:
    db: Session
    ctx: TenantContext

    def create(  # noqa: PLR0913
        self,
        *,
        title: str = "Vendor case",
        case_type: str = "vendor",
        status: str = "active",
        subject_type: str | None = None,
        subject_name: str | None = None,
        description: str | None = None,
        metadata: JsonObject | None = None,
    ) -> RiskCase:
        payload = case_payload(
            title=title,
            case_type=case_type,
            status=status,
            subject_type=subject_type,
            subject_name=subject_name,
            description=description,
            metadata=metadata,
        )
        command = cases.CreateCaseCommand(
            title=payload.title,
            case_type=payload.case_type,
            subject_type=payload.subject_type,
            subject_name=payload.subject_name,
            description=payload.description,
            status=payload.status,
            metadata=payload.metadata,
        )
        return cases.create_case(self.db, self.ctx, command)
