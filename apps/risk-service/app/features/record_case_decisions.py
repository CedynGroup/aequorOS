from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter

from app.api.deps import DbSession, Tenant
from app.schemas.cases import CaseDecisionCreate, CaseDecisionRead
from app.services import cases as cases_service

router = APIRouter(prefix="/cases", tags=["cases"])


@router.post("/{case_id}/decisions", response_model=CaseDecisionRead)
def create_case_decision(
    case_id: UUID, payload: CaseDecisionCreate, db: DbSession, ctx: Tenant
) -> CaseDecisionRead:
    decision = cases_service.decide_case(db, ctx, case_id, payload.to_command())
    names = cases_service.user_display_names(db, ctx.organization_id, {decision.decided_by})
    return CaseDecisionRead.model_validate(decision).model_copy(
        update={
            "decided_by_display_name": (
                names.get(decision.decided_by) if decision.decided_by is not None else None
            )
        }
    )


@router.get("/{case_id}/decisions", response_model=list[CaseDecisionRead])
def list_case_decisions(case_id: UUID, db: DbSession, ctx: Tenant) -> list[CaseDecisionRead]:
    decisions = cases_service.list_case_decisions(db, ctx, case_id)
    names = cases_service.user_display_names(
        db, ctx.organization_id, {decision.decided_by for decision in decisions}
    )
    return [
        CaseDecisionRead.model_validate(decision).model_copy(
            update={
                "decided_by_display_name": (
                    names.get(decision.decided_by) if decision.decided_by is not None else None
                )
            }
        )
        for decision in decisions
    ]
