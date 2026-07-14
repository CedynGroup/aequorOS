from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter

from app.api.deps import DbSession, MutationTenant, Tenant
from app.models import RiskFinding
from app.schemas.findings import (
    EvidenceRead,
    FindingCreate,
    FindingRead,
    FindingUpdate,
)
from app.services import findings as findings_service

router = APIRouter(tags=["findings"])


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


@router.post("/cases/{case_id}/findings", response_model=FindingRead, status_code=201)
def create_case_finding(
    case_id: UUID, payload: FindingCreate, db: DbSession, ctx: Tenant
) -> RiskFinding:
    return findings_service.create_case_finding(db, ctx, case_id, payload.to_command())


@router.get("/assessments/{assessment_id}/findings", response_model=list[FindingRead])
def list_assessment_findings(assessment_id: UUID, db: DbSession, ctx: Tenant) -> list[RiskFinding]:
    return findings_service.list_findings(
        db,
        ctx,
        findings_service.FindingFilters(assessment_id=assessment_id),
    )


@router.patch("/findings/{finding_id}", response_model=FindingRead)
def update_finding(
    finding_id: UUID, payload: FindingUpdate, db: DbSession, ctx: MutationTenant
) -> RiskFinding:
    return findings_service.update_finding(db, ctx, finding_id, payload.to_command())


@router.get("/findings/{finding_id}/evidence", response_model=list[EvidenceRead])
def get_finding_evidence(finding_id: UUID, db: DbSession, ctx: Tenant) -> list[EvidenceRead]:
    return [
        EvidenceRead(**evidence.__dict__)
        for evidence in findings_service.list_finding_evidence(db, ctx, finding_id)
    ]
