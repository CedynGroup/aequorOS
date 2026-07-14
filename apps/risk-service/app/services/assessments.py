from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.db.base import utc_now
from app.domain.risk_constants import ASSESSMENT_TYPES, CaseStatus
from app.models import (
    Document,
    Job,
    RiskAssessment,
    RiskAssessmentRun,
)
from app.schemas.assessments import AssessmentRunRead
from app.services.assessment_references import assessment_run_references
from app.services.audit import record_event
from app.services.cases import ensure_case_is_not_archived, get_case_or_404
from app.services.scoring import SCORING_VERSION, run_scoring


@dataclass(frozen=True)
class RunAssessmentResult:
    run_id: UUID
    job_id: UUID
    status: str


def get_assessment_or_404(
    db: Session, organization_id: UUID, assessment_id: UUID
) -> RiskAssessment:
    assessment = db.scalar(
        select(RiskAssessment).where(
            RiskAssessment.id == assessment_id,
            RiskAssessment.organization_id == organization_id,
        )
    )
    if assessment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Assessment not found.")
    return assessment


def get_run_or_404(db: Session, organization_id: UUID, run_id: UUID) -> RiskAssessmentRun:
    run = db.scalar(
        select(RiskAssessmentRun).where(
            RiskAssessmentRun.id == run_id,
            RiskAssessmentRun.organization_id == organization_id,
        )
    )
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Assessment run not found."
        )
    return run


def assessment_run_read(run: RiskAssessmentRun, reference: str) -> AssessmentRunRead:
    return AssessmentRunRead(
        id=run.id,
        reference=reference,
        organization_id=run.organization_id,
        assessment_id=run.assessment_id,
        status=run.status,
        engine_version=run.engine_version,
        prompt_version=run.prompt_version,
        input_hash=run.input_hash,
        started_at=run.started_at,
        completed_at=run.completed_at,
        summary=run.summary,
        error=run.error,
        created_at=run.created_at,
    )


def get_assessment_run_read(db: Session, organization_id: UUID, run_id: UUID) -> AssessmentRunRead:
    run = get_run_or_404(db, organization_id, run_id)
    references = assessment_run_references(db, organization_id, {run.id})
    return assessment_run_read(run, references[run.id])


def create_assessment(db: Session, ctx: TenantContext, payload) -> RiskAssessment:
    if payload.assessment_type not in ASSESSMENT_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid assessment type."
        )
    get_case_or_404(db, ctx.organization_id, payload.case_id)
    document_ids = [
        str(document_id)
        for document_id in db.scalars(
            select(Document.id).where(
                Document.organization_id == ctx.organization_id,
                Document.case_id == payload.case_id,
                Document.deleted_at.is_(None),
            )
        )
    ]
    assessment = RiskAssessment(
        organization_id=ctx.organization_id,
        case_id=payload.case_id,
        name=payload.name,
        assessment_type=payload.assessment_type,
        status=CaseStatus.DRAFT.value,
        input_snapshot={"document_ids": document_ids},
        config_snapshot={},
        created_by=ctx.actor_user_id,
    )
    db.add(assessment)
    db.flush()
    record_event(
        db,
        ctx,
        event_type="assessment.created",
        entity_type="risk_assessment",
        entity_id=assessment.id,
    )
    db.commit()
    db.refresh(assessment)
    return assessment


def list_assessments(
    db: Session, ctx: TenantContext, *, case_id: UUID | None = None
) -> list[RiskAssessment]:
    stmt = select(RiskAssessment).where(RiskAssessment.organization_id == ctx.organization_id)
    if case_id is not None:
        stmt = stmt.where(RiskAssessment.case_id == case_id)
    return list(db.scalars(stmt.order_by(RiskAssessment.created_at.desc())))


def run_assessment(db: Session, ctx: TenantContext, assessment_id: UUID) -> RunAssessmentResult:
    assessment = get_assessment_or_404(db, ctx.organization_id, assessment_id)
    case = get_case_or_404(db, ctx.organization_id, assessment.case_id)
    ensure_case_is_not_archived(case)
    run = RiskAssessmentRun(
        organization_id=ctx.organization_id,
        assessment_id=assessment.id,
        status="queued",
        engine_version=SCORING_VERSION,
        summary={},
    )
    db.add(run)
    db.flush()
    job = Job(
        organization_id=ctx.organization_id,
        job_type="assessment_run",
        status="queued",
        entity_type="risk_assessment_run",
        entity_id=run.id,
    )
    db.add(job)
    assessment.status = "running"
    record_event(
        db,
        ctx,
        event_type="assessment.run_requested",
        entity_type="risk_assessment",
        entity_id=assessment.id,
        details={"run_id": str(run.id)},
    )
    try:
        run_deterministic_assessment(db, ctx, assessment, run, job)
    except Exception as exc:
        assessment.status = "failed"
        run.status = "failed"
        run.error = str(exc)
        run.completed_at = utc_now()
        job.status = "failed"
        job.error = str(exc)
        job.completed_at = utc_now()
        record_event(
            db,
            ctx,
            event_type="assessment.failed",
            entity_type="risk_assessment",
            entity_id=assessment.id,
            details={"run_id": str(run.id), "error": str(exc)},
        )
    db.commit()
    return RunAssessmentResult(run_id=run.id, job_id=job.id, status=run.status)


def run_deterministic_assessment(
    db: Session,
    ctx: TenantContext,
    assessment: RiskAssessment,
    run: RiskAssessmentRun,
    job: Job,
) -> None:
    job.status = "running"
    job.started_at = utc_now()
    run.status = "running"
    run.started_at = utc_now()
    case = get_case_or_404(db, ctx.organization_id, assessment.case_id)
    scoring = run_scoring(db, ctx, case, assessment, run_id=run.id)
    run.status = CaseStatus.COMPLETED.value
    run.completed_at = utc_now()
    run.input_hash = scoring.input_hash
    run.summary = {
        "findings_created": scoring.findings_created,
        "risk_score": scoring.risk_score,
        "risk_level": scoring.risk_level,
        "rules_evaluated": scoring.rules_evaluated,
        "scoring_version": scoring.scoring_version,
        "score_id": str(scoring.score_id),
        "input_hash": scoring.input_hash,
        "input_snapshot": scoring.input_snapshot,
    }
    assessment.status = CaseStatus.COMPLETED.value
    job.status = CaseStatus.COMPLETED.value
    job.progress = run.summary
    job.completed_at = utc_now()
    record_event(
        db,
        ctx,
        event_type="assessment.completed",
        entity_type="risk_assessment",
        entity_id=assessment.id,
        details={"run_id": str(run.id), **run.summary},
    )


def list_assessment_runs(
    db: Session, ctx: TenantContext, assessment_id: UUID
) -> list[AssessmentRunRead]:
    get_assessment_or_404(db, ctx.organization_id, assessment_id)
    runs = list(
        db.scalars(
            select(RiskAssessmentRun)
            .where(
                RiskAssessmentRun.organization_id == ctx.organization_id,
                RiskAssessmentRun.assessment_id == assessment_id,
            )
            .order_by(RiskAssessmentRun.created_at.desc())
        )
    )
    references = assessment_run_references(db, ctx.organization_id, {run.id for run in runs})
    return [assessment_run_read(run, references[run.id]) for run in runs]
