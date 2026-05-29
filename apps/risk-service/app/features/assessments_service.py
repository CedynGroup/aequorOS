from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.db.base import utc_now
from app.features.audit import record_event
from app.features.cases_service import get_case_or_404
from app.features.constants import ASSESSMENT_TYPES
from app.models import (
    Document,
    DocumentChunk,
    Job,
    RiskAssessment,
    RiskAssessmentRun,
    RiskFinding,
    RiskFindingEvidence,
)


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
        status="draft",
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
    run = RiskAssessmentRun(
        organization_id=ctx.organization_id,
        assessment_id=assessment.id,
        status="queued",
        engine_version="phase_1_stub",
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
        run_assessment_stub(db, ctx, assessment, run, job)
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


def run_assessment_stub(
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
    chunk = db.scalar(
        select(DocumentChunk)
        .join(Document, Document.id == DocumentChunk.document_id)
        .where(
            DocumentChunk.organization_id == ctx.organization_id,
            Document.case_id == assessment.case_id,
            Document.parse_status == "parsed",
            Document.deleted_at.is_(None),
        )
        .order_by(DocumentChunk.created_at.asc())
    )
    findings_created = 0
    if chunk is not None:
        finding = RiskFinding(
            organization_id=ctx.organization_id,
            case_id=assessment.case_id,
            assessment_id=assessment.id,
            run_id=run.id,
            risk_type="documentation_gap",
            title="Documentation gap requires review",
            summary="Phase 1 stub identified parsed evidence that should be reviewed.",
            rationale="Generated by deterministic Phase 1 stub from parsed document evidence.",
            severity="medium",
            likelihood="medium",
            impact="medium",
            confidence=Decimal("0.50"),
            status="open",
        )
        db.add(finding)
        db.flush()
        db.add(
            RiskFindingEvidence(
                organization_id=ctx.organization_id,
                finding_id=finding.id,
                document_id=chunk.document_id,
                document_chunk_id=chunk.id,
                page_number=chunk.page_start,
                quote=chunk.text[:500],
                locator={"chunk_index": chunk.chunk_index},
                relevance=Decimal("0.50"),
            )
        )
        findings_created = 1
    run.status = "completed"
    run.completed_at = utc_now()
    run.summary = {"findings_created": findings_created}
    assessment.status = "completed"
    job.status = "completed"
    job.progress = {"findings_created": findings_created}
    job.completed_at = utc_now()
    record_event(
        db,
        ctx,
        event_type="assessment.completed",
        entity_type="risk_assessment",
        entity_id=assessment.id,
        details={"run_id": str(run.id), "findings_created": findings_created},
    )


def list_assessment_runs(
    db: Session, ctx: TenantContext, assessment_id: UUID
) -> list[RiskAssessmentRun]:
    get_assessment_or_404(db, ctx.organization_id, assessment_id)
    return list(
        db.scalars(
            select(RiskAssessmentRun)
            .where(
                RiskAssessmentRun.organization_id == ctx.organization_id,
                RiskAssessmentRun.assessment_id == assessment_id,
            )
            .order_by(RiskAssessmentRun.created_at.desc())
        )
    )
