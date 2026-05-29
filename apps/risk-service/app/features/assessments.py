from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, status
from pydantic import BaseModel, ConfigDict

from app.api.deps import DbSession, Tenant
from app.features import assessments_service
from app.models import RiskAssessment, RiskAssessmentRun

router = APIRouter(tags=["assessments"])


class AssessmentCreate(BaseModel):
    case_id: UUID
    assessment_type: str
    name: str


class AssessmentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    organization_id: UUID
    case_id: UUID
    name: str
    assessment_type: str
    status: str
    input_snapshot: dict[str, object]
    config_snapshot: dict[str, object]
    created_by: UUID | None
    created_at: datetime
    updated_at: datetime


class AssessmentRunRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    organization_id: UUID
    assessment_id: UUID
    status: str
    engine_version: str | None
    prompt_version: str | None
    input_hash: str | None
    started_at: datetime | None
    completed_at: datetime | None
    summary: dict[str, object]
    error: str | None
    created_at: datetime


class RunResponse(BaseModel):
    run_id: UUID
    job_id: UUID
    status: str


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
