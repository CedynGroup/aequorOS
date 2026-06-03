from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter

from app.api.deps import DbSession, Tenant
from app.models import RiskCaseDecision
from app.schemas.cases import CaseDecisionCreate, CaseDecisionRead
from app.services import cases as cases_service

router = APIRouter(prefix="/cases", tags=["cases"])


@router.post("/{case_id}/decisions", response_model=CaseDecisionRead)
def create_case_decision(
    case_id: UUID, payload: CaseDecisionCreate, db: DbSession, ctx: Tenant
) -> RiskCaseDecision:
    return cases_service.decide_case(db, ctx, case_id, payload.to_command())


@router.get("/{case_id}/decisions", response_model=list[CaseDecisionRead])
def list_case_decisions(case_id: UUID, db: DbSession, ctx: Tenant) -> list[RiskCaseDecision]:
    return cases_service.list_case_decisions(db, ctx, case_id)
