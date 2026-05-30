from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import Select, func, or_, select
from sqlalchemy import case as sql_case
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.domain.risk_constants import (
    CASE_DECISIONS,
    CASE_SORT_OPTIONS,
    CASE_STATUSES,
    OPEN_FINDING_STATUSES,
    RISK_LEVELS,
    CaseDecision,
    CaseStatus,
)
from app.models import RiskCase, RiskCaseDecision, RiskFinding, RiskScore, User
from app.schemas.common import JsonObject
from app.services.audit import record_event


@dataclass(frozen=True)
class CaseFilters:
    include_archived: bool = False
    status: str | None = None
    assigned_to_user_id: UUID | None = None
    decision: str | None = None
    risk_level: str | None = None
    q: str | None = None
    sort: str = "created_at_desc"
    limit: int = 50
    offset: int = 0


@dataclass(frozen=True)
class CreateCaseCommand:
    title: str
    case_type: str
    subject_type: str | None
    subject_name: str | None
    description: str | None
    status: str
    metadata: JsonObject


@dataclass(frozen=True)
class UpdateCaseCommand:
    update_data: dict[str, str | None]


@dataclass(frozen=True)
class RecordDecisionCommand:
    decision: str
    reason: str | None


@dataclass(frozen=True)
class CaseQueueItem:
    case: RiskCase
    findings_count: int
    open_findings_count: int
    assignee_display_name: str | None
    assignee_email: str | None


@dataclass(frozen=True)
class CaseListResult:
    items: list[CaseQueueItem]
    total: int
    limit: int
    offset: int
    page: int
    pages: int
    has_more: bool


@dataclass(frozen=True)
class CaseSummary:
    total_cases: int
    archived_cases: int
    unassigned_cases: int
    completed_cases: int
    open_findings: int
    by_status: dict[str, int]
    by_assignee: dict[str, int]
    by_decision: dict[str, int]
    by_risk_level: dict[str, int]


CASE_STATUS_TRANSITIONS = {
    CaseStatus.DRAFT.value: {
        CaseStatus.DRAFT.value,
        CaseStatus.ACTIVE.value,
        CaseStatus.IN_REVIEW.value,
    },
    CaseStatus.ACTIVE.value: {CaseStatus.ACTIVE.value, CaseStatus.IN_REVIEW.value},
    CaseStatus.IN_REVIEW.value: {CaseStatus.ACTIVE.value, CaseStatus.IN_REVIEW.value},
    CaseStatus.COMPLETED.value: {CaseStatus.COMPLETED.value},
    CaseStatus.ARCHIVED.value: {CaseStatus.ARCHIVED.value},
}


def get_case_or_404(db: Session, organization_id: UUID, case_id: UUID) -> RiskCase:
    case = db.scalar(
        select(RiskCase).where(RiskCase.id == case_id, RiskCase.organization_id == organization_id)
    )
    if case is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Case not found.")
    return case


def create_case(db: Session, ctx: TenantContext, command: CreateCaseCommand) -> RiskCase:
    case = RiskCase(
        organization_id=ctx.organization_id,
        title=command.title,
        case_type=command.case_type,
        subject_type=command.subject_type,
        subject_name=command.subject_name,
        description=command.description,
        status=command.status,
        metadata_=command.metadata,
        created_by=ctx.actor_user_id,
    )
    db.add(case)
    db.flush()
    record_event(db, ctx, event_type="case.created", entity_type="risk_case", entity_id=case.id)
    db.commit()
    db.refresh(case)
    return case


def list_cases(db: Session, ctx: TenantContext, filters: CaseFilters) -> CaseListResult:
    validate_case_filters(filters)
    stmt = build_case_query(ctx, filters)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = list(
        db.scalars(sort_cases(stmt, filters.sort).limit(filters.limit).offset(filters.offset))
    )
    counts = finding_counts_for_cases(db, ctx, [case.id for case in rows])
    assignees = assignees_for_cases(db, ctx, rows)
    return CaseListResult(
        items=[
            CaseQueueItem(
                case=case,
                findings_count=counts.get(case.id, (0, 0))[0],
                open_findings_count=counts.get(case.id, (0, 0))[1],
                assignee_display_name=assignees.get(case.assigned_to_user_id, (None, None))[0],
                assignee_email=assignees.get(case.assigned_to_user_id, (None, None))[1],
            )
            for case in rows
        ],
        total=total,
        limit=filters.limit,
        offset=filters.offset,
        page=(filters.offset // filters.limit) + 1,
        pages=(total + filters.limit - 1) // filters.limit if total else 0,
        has_more=filters.offset + len(rows) < total,
    )


def build_case_query(ctx: TenantContext, filters: CaseFilters) -> Select[tuple[RiskCase]]:
    stmt = select(RiskCase).where(RiskCase.organization_id == ctx.organization_id)
    if not filters.include_archived:
        stmt = stmt.where(
            RiskCase.status != CaseStatus.ARCHIVED.value,
            RiskCase.archived_at.is_(None),
        )
    if filters.status is not None:
        stmt = stmt.where(RiskCase.status == filters.status)
    if filters.assigned_to_user_id is not None:
        stmt = stmt.where(RiskCase.assigned_to_user_id == filters.assigned_to_user_id)
    if filters.decision is not None:
        stmt = stmt.where(RiskCase.decision == filters.decision)
    if filters.risk_level is not None:
        stmt = stmt.where(RiskCase.risk_level == filters.risk_level)
    if filters.q:
        pattern = f"%{filters.q.strip()}%"
        stmt = stmt.where(
            or_(
                RiskCase.title.ilike(pattern),
                RiskCase.subject_name.ilike(pattern),
                RiskCase.description.ilike(pattern),
            )
        )
    return stmt


def validate_case_filters(filters: CaseFilters) -> None:
    if filters.limit < 1 or filters.limit > 200:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Limit must be 1-200.")
    if filters.offset < 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Offset must be >= 0.")
    if filters.status is not None and filters.status not in CASE_STATUSES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid case status.")
    if filters.decision is not None and filters.decision not in CASE_DECISIONS:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid decision.")
    if filters.risk_level is not None and filters.risk_level not in RISK_LEVELS:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid risk level.")
    if filters.sort not in CASE_SORT_OPTIONS:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid sort.")


def sort_cases(stmt: Select[tuple[RiskCase]], sort: str) -> Select[tuple[RiskCase]]:
    sort_map = {
        "created_at_desc": RiskCase.created_at.desc(),
        "created_at_asc": RiskCase.created_at.asc(),
        "updated_at_desc": RiskCase.updated_at.desc(),
        "updated_at_asc": RiskCase.updated_at.asc(),
        "risk_score_desc": RiskCase.risk_score.desc().nullslast(),
        "risk_score_asc": RiskCase.risk_score.asc().nullslast(),
        "title_asc": RiskCase.title.asc(),
    }
    return stmt.order_by(sort_map[sort], RiskCase.id.asc())


def finding_counts_for_cases(
    db: Session, ctx: TenantContext, case_ids: list[UUID]
) -> dict[UUID, tuple[int, int]]:
    if not case_ids:
        return {}
    rows = db.execute(
        select(
            RiskFinding.case_id,
            func.count(RiskFinding.id),
            func.sum(sql_case((RiskFinding.status.in_(OPEN_FINDING_STATUSES), 1), else_=0)),
        )
        .where(
            RiskFinding.organization_id == ctx.organization_id,
            RiskFinding.case_id.in_(case_ids),
        )
        .group_by(RiskFinding.case_id)
    ).all()
    return {case_id: (int(total or 0), int(open_count or 0)) for case_id, total, open_count in rows}


def assignees_for_cases(
    db: Session, ctx: TenantContext, cases: list[RiskCase]
) -> dict[UUID | None, tuple[str | None, str | None]]:
    assignee_ids = {case.assigned_to_user_id for case in cases if case.assigned_to_user_id}
    if not assignee_ids:
        return {}
    rows = db.execute(
        select(User.id, User.display_name, User.email).where(
            User.organization_id == ctx.organization_id,
            User.id.in_(assignee_ids),
        )
    ).all()
    return {user_id: (display_name, email) for user_id, display_name, email in rows}


def update_case(
    db: Session, ctx: TenantContext, case_id: UUID, command: UpdateCaseCommand
) -> RiskCase:
    case = get_case_or_404(db, ctx.organization_id, case_id)
    ensure_case_is_not_archived(case)
    before = {"status": case.status, "title": case.title}
    update_data = command.update_data
    status_value = update_data.get("status")
    if "status" in update_data and (
        not isinstance(status_value, str) or status_value not in CASE_STATUSES
    ):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid case status.")
    if status_value == CaseStatus.COMPLETED.value:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cases must be completed through the decision workflow.",
        )
    if status_value == CaseStatus.ARCHIVED.value:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cases must be archived through the archive workflow.",
        )
    if "status" in update_data:
        assert isinstance(status_value, str)
        ensure_status_transition_allowed(case.status, status_value)
    for key, value in update_data.items():
        setattr(case, key, value)
    record_event(
        db,
        ctx,
        event_type="case.updated",
        entity_type="risk_case",
        entity_id=case.id,
        details={"before": before, "after": update_data},
    )
    db.commit()
    db.refresh(case)
    return case


def ensure_case_is_not_archived(case: RiskCase) -> None:
    if case.status == CaseStatus.ARCHIVED.value or case.archived_at is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Archived cases cannot be modified through this workflow.",
        )


def ensure_status_transition_allowed(current_status: str, next_status: str) -> None:
    if next_status not in CASE_STATUS_TRANSITIONS.get(current_status, set()):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Invalid case status transition from {current_status} to {next_status}.",
        )


def assign_case(
    db: Session, ctx: TenantContext, case_id: UUID, assigned_to_user_id: UUID | None
) -> RiskCase:
    case = get_case_or_404(db, ctx.organization_id, case_id)
    ensure_case_is_not_archived(case)
    if assigned_to_user_id is not None:
        user_id = db.scalar(
            select(User.id).where(
                User.id == assigned_to_user_id,
                User.organization_id == ctx.organization_id,
                User.is_active.is_(True),
            )
        )
        if user_id is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Assignee not found.")
    before = {
        "assigned_to_user_id": str(case.assigned_to_user_id) if case.assigned_to_user_id else None
    }
    case.assigned_to_user_id = assigned_to_user_id
    case.assigned_at = datetime.now(UTC) if assigned_to_user_id is not None else None
    record_event(
        db,
        ctx,
        event_type="case.assigned",
        entity_type="risk_case",
        entity_id=case.id,
        details={
            "before": before,
            "after": {
                "assigned_to_user_id": str(case.assigned_to_user_id)
                if case.assigned_to_user_id
                else None
            },
        },
    )
    db.commit()
    db.refresh(case)
    return case


def decide_case(
    db: Session, ctx: TenantContext, case_id: UUID, command: RecordDecisionCommand
) -> RiskCaseDecision:
    case = get_case_or_404(db, ctx.organization_id, case_id)
    ensure_case_is_not_archived(case)
    decision_value = command.decision
    if decision_value not in CASE_DECISIONS:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid decision.")
    if decision_value != CaseDecision.NEEDS_MORE_INFO.value and case.risk_score is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Case must be scored before a final decision.",
        )
    if decision_value != CaseDecision.NEEDS_MORE_INFO.value and has_unresolved_findings(
        db, ctx, case.id
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Open findings must be acknowledged, dismissed, or resolved before completion.",
        )
    now = datetime.now(UTC)
    decision = RiskCaseDecision(
        organization_id=ctx.organization_id,
        case_id=case.id,
        decision=decision_value,
        previous_decision=case.decision,
        reason=command.reason,
        decided_by=ctx.actor_user_id,
        created_at=now,
    )
    db.add(decision)
    case.decision = decision_value
    case.decided_at = now
    case.status = (
        CaseStatus.IN_REVIEW.value
        if decision_value == CaseDecision.NEEDS_MORE_INFO.value
        else CaseStatus.COMPLETED.value
    )
    record_event(
        db,
        ctx,
        event_type="case.decision_recorded",
        entity_type="risk_case",
        entity_id=case.id,
        details={
            "before": {"decision": decision.previous_decision},
            "after": {"decision": decision.decision, "status": case.status},
        },
    )
    db.commit()
    db.refresh(decision)
    return decision


def list_case_decisions(db: Session, ctx: TenantContext, case_id: UUID) -> list[RiskCaseDecision]:
    get_case_or_404(db, ctx.organization_id, case_id)
    return list(
        db.scalars(
            select(RiskCaseDecision)
            .where(
                RiskCaseDecision.organization_id == ctx.organization_id,
                RiskCaseDecision.case_id == case_id,
            )
            .order_by(RiskCaseDecision.created_at.desc())
        )
    )


def list_case_scores(db: Session, ctx: TenantContext, case_id: UUID) -> list[RiskScore]:
    get_case_or_404(db, ctx.organization_id, case_id)
    return list(
        db.scalars(
            select(RiskScore)
            .where(
                RiskScore.organization_id == ctx.organization_id,
                RiskScore.case_id == case_id,
            )
            .order_by(RiskScore.created_at.desc())
        )
    )


def has_unresolved_findings(db: Session, ctx: TenantContext, case_id: UUID) -> bool:
    finding_id = db.scalar(
        select(RiskFinding.id).where(
            RiskFinding.organization_id == ctx.organization_id,
            RiskFinding.case_id == case_id,
            RiskFinding.status.in_(OPEN_FINDING_STATUSES),
        )
    )
    return finding_id is not None


def case_summary(db: Session, ctx: TenantContext) -> CaseSummary:
    base = (
        RiskCase.organization_id == ctx.organization_id,
        RiskCase.status != CaseStatus.ARCHIVED.value,
        RiskCase.archived_at.is_(None),
    )
    archived_base = (
        RiskCase.organization_id == ctx.organization_id,
        or_(RiskCase.status == CaseStatus.ARCHIVED.value, RiskCase.archived_at.is_not(None)),
    )
    total_cases = db.scalar(select(func.count(RiskCase.id)).where(*base)) or 0
    archived_cases = db.scalar(select(func.count(RiskCase.id)).where(*archived_base)) or 0
    unassigned_cases = (
        db.scalar(
            select(func.count(RiskCase.id)).where(*base, RiskCase.assigned_to_user_id.is_(None))
        )
        or 0
    )
    completed_cases = (
        db.scalar(
            select(func.count(RiskCase.id)).where(
                *base,
                RiskCase.status == CaseStatus.COMPLETED.value,
            )
        )
        or 0
    )
    open_findings = (
        db.scalar(
            select(func.count(RiskFinding.id))
            .join(RiskCase, RiskCase.id == RiskFinding.case_id)
            .where(
                RiskFinding.organization_id == ctx.organization_id,
                RiskFinding.status.in_(OPEN_FINDING_STATUSES),
                *base,
            )
        )
        or 0
    )
    return CaseSummary(
        total_cases=int(total_cases),
        archived_cases=int(archived_cases),
        unassigned_cases=int(unassigned_cases),
        completed_cases=int(completed_cases),
        open_findings=int(open_findings),
        by_status=count_by_column(db, ctx, RiskCase.status, base),
        by_assignee=count_by_column(db, ctx, RiskCase.assigned_to_user_id, base),
        by_decision=count_by_column(db, ctx, RiskCase.decision, base),
        by_risk_level=count_by_column(db, ctx, RiskCase.risk_level, base),
    )


def count_by_column(db: Session, ctx: TenantContext, column, base) -> dict[str, int]:
    rows = db.execute(select(column, func.count(RiskCase.id)).where(*base).group_by(column)).all()
    return {str(key) if key is not None else "unassigned": int(count) for key, count in rows}


def archive_case(db: Session, ctx: TenantContext, case_id: UUID) -> RiskCase:
    case = get_case_or_404(db, ctx.organization_id, case_id)
    before = {
        "status": case.status,
        "archived_at": case.archived_at.isoformat() if case.archived_at else None,
    }
    case.status = CaseStatus.ARCHIVED.value
    case.archived_at = datetime.now(UTC)
    record_event(
        db,
        ctx,
        event_type="case.archived",
        entity_type="risk_case",
        entity_id=case.id,
        details={"before": before, "after": {"status": case.status}},
    )
    db.commit()
    db.refresh(case)
    return case
