"""/operator/v1/operating-environment — the Operating-Environment desk console.

Spec: ``docs/internal/operating_environment_score.md``. The governed,
data-derived jurisdiction operating-environment score: compute-preview (writes
no assessment state — only the staff audit row lands), the maker-checker
lifecycle (stage draft → submit → approve[≠proposer] → publish), and list/get.
Publish fans ``GHANA_OPERATING_ENVIRONMENT_SCORE`` out to every tenant through
the desk-as-vendor pull path, so the implied-rating model picks it up unchanged.

Every mutation is recorded through ``record_operator_action`` — the desk's
audit trail is part of the governance story the spec commits to.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter

from app.models import DeskOperatingEnvironmentAssessment
from app.operator.deps import Operator, OperatorDb, record_operator_action
from app.schemas.market_desk_operating_environment import (
    DeskOperatingEnvironmentAssessmentListRead,
    DeskOperatingEnvironmentAssessmentRead,
    DeskOperatingEnvironmentComputeRequest,
    DeskOperatingEnvironmentPreview,
    DeskOperatingEnvironmentPublishResult,
)
from app.services.market_desk import operating_environment as oe

router = APIRouter(prefix="/operating-environment", tags=["operator-operating-environment"])


def _assessment_read(
    row: DeskOperatingEnvironmentAssessment,
) -> DeskOperatingEnvironmentAssessmentRead:
    return DeskOperatingEnvironmentAssessmentRead(
        id=row.id,
        jurisdiction_code=row.jurisdiction_code,
        cob_date=row.cob_date,
        methodology_version=row.methodology_version,
        score=row.score,
        status=row.status,
        input_digest=row.input_digest,
        inputs=row.input_snapshot,
        breakdown=row.computed_breakdown,
        proposed_by=row.proposed_by,
        approved_by=row.approved_by,
        approved_at=row.approved_at,
        published_at=row.published_at,
        created_at=row.created_at,
    )


@router.post("/compute-preview", response_model=DeskOperatingEnvironmentPreview)
def compute_preview(
    payload: DeskOperatingEnvironmentComputeRequest, db: OperatorDb, operator: Operator
) -> DeskOperatingEnvironmentPreview:
    """Resolve inputs, compute, and return the breakdown — persists no
    assessment state (only the staff audit row lands)."""
    preview = oe.compute_preview(
        db,
        jurisdiction_code=payload.jurisdiction_code,
        cob_date=payload.cob_date,
        inputs=payload.inputs.model_dump(),
    )
    record_operator_action(
        db,
        operator,
        action="desk.operating_environment.compute_preview",
        detail={
            "jurisdiction_code": payload.jurisdiction_code,
            "cob_date": payload.cob_date.isoformat(),
            "score": str(preview["score"]),
            "input_digest": preview["input_digest"],
        },
    )
    db.commit()
    return DeskOperatingEnvironmentPreview.model_validate(preview)


@router.post(
    "/assessments", response_model=DeskOperatingEnvironmentAssessmentRead, status_code=201
)
def stage_assessment(
    payload: DeskOperatingEnvironmentComputeRequest, db: OperatorDb, operator: Operator
) -> DeskOperatingEnvironmentAssessmentRead:
    """Stage a DRAFT assessment (writes a draft — never approved/published)."""
    row = oe.stage_draft(
        db,
        jurisdiction_code=payload.jurisdiction_code,
        cob_date=payload.cob_date,
        inputs=payload.inputs.model_dump(),
        proposed_by=operator.email,
    )
    record_operator_action(
        db,
        operator,
        action="desk.operating_environment.stage_draft",
        detail={
            "assessment_id": str(row.id),
            "jurisdiction_code": row.jurisdiction_code,
            "cob_date": row.cob_date.isoformat(),
            "score": str(row.score),
            "input_digest": row.input_digest,
        },
    )
    db.commit()
    return _assessment_read(row)


@router.get("/assessments", response_model=DeskOperatingEnvironmentAssessmentListRead)
def list_assessments(
    db: OperatorDb,
    _operator: Operator,
    jurisdiction_code: str | None = None,
    status: str | None = None,
) -> DeskOperatingEnvironmentAssessmentListRead:
    rows = oe.list_assessments(db, jurisdiction_code=jurisdiction_code, status_filter=status)
    return DeskOperatingEnvironmentAssessmentListRead(
        assessments=[_assessment_read(row) for row in rows], total=len(rows)
    )


@router.get(
    "/assessments/{assessment_id}", response_model=DeskOperatingEnvironmentAssessmentRead
)
def get_assessment(
    assessment_id: UUID, db: OperatorDb, _operator: Operator
) -> DeskOperatingEnvironmentAssessmentRead:
    return _assessment_read(oe.get(db, assessment_id))


@router.post(
    "/assessments/{assessment_id}/submit",
    response_model=DeskOperatingEnvironmentAssessmentRead,
)
def submit_assessment(
    assessment_id: UUID, db: OperatorDb, operator: Operator
) -> DeskOperatingEnvironmentAssessmentRead:
    """Maker step: draft → pending_review."""
    row = oe.submit(db, assessment_id)
    record_operator_action(
        db,
        operator,
        action="desk.operating_environment.submit",
        detail={"assessment_id": str(row.id)},
    )
    db.commit()
    return _assessment_read(row)


@router.post(
    "/assessments/{assessment_id}/approve",
    response_model=DeskOperatingEnvironmentAssessmentRead,
)
def approve_assessment(
    assessment_id: UUID, db: OperatorDb, operator: Operator
) -> DeskOperatingEnvironmentAssessmentRead:
    """Checker step: pending_review → approved. Four-eyes (approver ≠ proposer)
    is enforced in the service (422)."""
    row = oe.approve(db, assessment_id, approved_by=operator.email)
    record_operator_action(
        db,
        operator,
        action="desk.operating_environment.approve",
        detail={"assessment_id": str(row.id)},
    )
    db.commit()
    return _assessment_read(row)


@router.post(
    "/assessments/{assessment_id}/publish",
    response_model=DeskOperatingEnvironmentPublishResult,
)
def publish_assessment(
    assessment_id: UUID, db: OperatorDb, operator: Operator
) -> DeskOperatingEnvironmentPublishResult:
    """Publish step: an APPROVED assessment fans out to every tenant as the
    ``GHANA_OPERATING_ENVIRONMENT_SCORE`` index (bitemporal + lineage + audit)."""
    result = oe.publish(db, assessment_id, actor=operator.email)
    record_operator_action(
        db,
        operator,
        action="desk.operating_environment.publish",
        detail={
            "assessment_id": str(assessment_id),
            "status": result["status"],
            "banks": result["banks"],
        },
    )
    db.commit()
    return DeskOperatingEnvironmentPublishResult.model_validate(result)
