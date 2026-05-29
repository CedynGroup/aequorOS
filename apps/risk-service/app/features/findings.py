from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from app.api.deps import DbSession, Tenant
from app.features import findings_service
from app.models import RiskFinding

router = APIRouter(tags=["findings"])


class FindingRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    organization_id: UUID
    case_id: UUID
    assessment_id: UUID | None
    run_id: UUID | None
    risk_type: str
    title: str
    summary: str
    rationale: str | None
    severity: str
    likelihood: str | None
    impact: str | None
    confidence: Decimal | None
    status: str
    disposition_reason: str | None
    created_at: datetime
    updated_at: datetime


class FindingUpdate(BaseModel):
    status: str | None = None
    disposition_reason: str | None = None
    title: str | None = Field(default=None, exclude=True)
    summary: str | None = Field(default=None, exclude=True)
    severity: str | None = Field(default=None, exclude=True)
    rationale: str | None = Field(default=None, exclude=True)


class EvidenceRead(BaseModel):
    id: UUID
    finding_id: UUID
    document_id: UUID | None
    document_chunk_id: UUID | None
    page_number: int | None
    quote: str | None
    locator: dict[str, object]
    relevance: Decimal | None
    created_at: datetime
    document: dict[str, object] | None
    chunk: dict[str, object] | None


@router.get("/findings", response_model=list[FindingRead])
def list_findings(  # noqa: PLR0913
    db: DbSession,
    ctx: Tenant,
    case_id: UUID | None = None,
    assessment_id: UUID | None = None,
    status: str | None = None,
    severity: str | None = None,
    risk_type: str | None = None,
) -> list[RiskFinding]:
    return findings_service.list_findings(
        db,
        ctx,
        findings_service.FindingFilters(
            case_id=case_id,
            assessment_id=assessment_id,
            status=status,
            severity=severity,
            risk_type=risk_type,
        ),
    )


@router.get("/findings/{finding_id}", response_model=FindingRead)
def get_finding(finding_id: UUID, db: DbSession, ctx: Tenant) -> RiskFinding:
    return findings_service.get_finding_or_404(db, ctx.organization_id, finding_id)


@router.get("/cases/{case_id}/findings", response_model=list[FindingRead])
def list_case_findings(case_id: UUID, db: DbSession, ctx: Tenant) -> list[RiskFinding]:
    return findings_service.list_case_findings(db, ctx, case_id)


@router.get("/assessments/{assessment_id}/findings", response_model=list[FindingRead])
def list_assessment_findings(assessment_id: UUID, db: DbSession, ctx: Tenant) -> list[RiskFinding]:
    return findings_service.list_findings(
        db,
        ctx,
        findings_service.FindingFilters(assessment_id=assessment_id),
    )


@router.patch("/findings/{finding_id}", response_model=FindingRead)
def update_finding(
    finding_id: UUID, payload: FindingUpdate, db: DbSession, ctx: Tenant
) -> RiskFinding:
    return findings_service.update_finding(db, ctx, finding_id, payload)


@router.get("/findings/{finding_id}/evidence", response_model=list[EvidenceRead])
def get_finding_evidence(finding_id: UUID, db: DbSession, ctx: Tenant) -> list[EvidenceRead]:
    return [
        EvidenceRead(**evidence.__dict__)
        for evidence in findings_service.list_finding_evidence(db, ctx, finding_id)
    ]
