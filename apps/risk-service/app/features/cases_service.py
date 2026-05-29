from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.features.audit import record_event
from app.features.constants import CASE_STATUSES
from app.models import RiskCase


def get_case_or_404(db: Session, organization_id: UUID, case_id: UUID) -> RiskCase:
    case = db.scalar(
        select(RiskCase).where(RiskCase.id == case_id, RiskCase.organization_id == organization_id)
    )
    if case is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Case not found.")
    return case


def create_case(db: Session, ctx: TenantContext, payload: Any) -> RiskCase:
    case = RiskCase(
        organization_id=ctx.organization_id,
        title=payload.title,
        case_type=payload.case_type,
        subject_type=payload.subject_type,
        subject_name=payload.subject_name,
        description=payload.description,
        status=payload.status,
        metadata_=payload.metadata,
        created_by=ctx.actor_user_id,
    )
    db.add(case)
    db.flush()
    record_event(db, ctx, event_type="case.created", entity_type="risk_case", entity_id=case.id)
    db.commit()
    db.refresh(case)
    return case


def list_cases(
    db: Session, ctx: TenantContext, *, include_archived: bool = False
) -> list[RiskCase]:
    stmt = select(RiskCase).where(RiskCase.organization_id == ctx.organization_id)
    if not include_archived:
        stmt = stmt.where(RiskCase.status != "archived", RiskCase.archived_at.is_(None))
    return list(db.scalars(stmt.order_by(RiskCase.created_at.desc())))


def update_case(db: Session, ctx: TenantContext, case_id: UUID, payload: Any) -> RiskCase:
    case = get_case_or_404(db, ctx.organization_id, case_id)
    before = {"status": case.status, "title": case.title}
    update_data = payload.model_dump(exclude_unset=True)
    if "status" in update_data and update_data["status"] not in CASE_STATUSES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid case status.")
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


def archive_case(db: Session, ctx: TenantContext, case_id: UUID) -> RiskCase:
    case = get_case_or_404(db, ctx.organization_id, case_id)
    before = {
        "status": case.status,
        "archived_at": case.archived_at.isoformat() if case.archived_at else None,
    }
    case.status = "archived"
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
