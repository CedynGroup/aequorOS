from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, status

from app.api.deps import DbSession, Tenant
from app.models import RiskAssessment, RiskAssessmentRun
from app.schemas.assessments import (
    AssessmentCreate,
    AssessmentRead,
    AssessmentRunRead,
    RunResponse,
)
from app.services import assessments as assessments_service

router = APIRouter(tags=["assessments"])


@router.post("/assessments", response_model=AssessmentRead, status_code=status.HTTP_201_CREATED)
def create_assessment(payload: AssessmentCreate, db: DbSession, ctx: Tenant) -> RiskAssessment:
    return assessments_service.create_assessment(db, ctx, payload)


@router.get("/assessments", response_model=list[AssessmentRead])
def list_assessments(
    db: DbSession, ctx: Tenant, case_id: UUID | None = None
) -> list[RiskAssessment]:
    return assessments_service.list_assessments(db, ctx, case_id=case_id)


@router.get("/assessments/{assessment_id}", response_model=AssessmentRead)
def get_assessment(assessment_id: UUID, db: DbSession, ctx: Tenant) -> RiskAssessment:
    return assessments_service.get_assessment_or_404(db, ctx.organization_id, assessment_id)


@router.post("/assessments/{assessment_id}/run", response_model=RunResponse)
def run_assessment(assessment_id: UUID, db: DbSession, ctx: Tenant) -> RunResponse:
    result = assessments_service.run_assessment(db, ctx, assessment_id)
    return RunResponse(**result.__dict__)


@router.get("/assessments/{assessment_id}/runs", response_model=list[AssessmentRunRead])
def list_assessment_runs(
    assessment_id: UUID, db: DbSession, ctx: Tenant
) -> list[RiskAssessmentRun]:
    return assessments_service.list_assessment_runs(db, ctx, assessment_id)


@router.get("/assessment-runs/{run_id}", response_model=AssessmentRunRead)
def get_assessment_run(run_id: UUID, db: DbSession, ctx: Tenant) -> RiskAssessmentRun:
    return assessments_service.get_run_or_404(db, ctx.organization_id, run_id)
