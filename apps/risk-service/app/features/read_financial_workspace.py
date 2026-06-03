from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter

from app.api.deps import DbSession, Tenant
from app.schemas.financial_workspace import FinancialDataWorkspaceRead
from app.services import financial_workspace as financial_workspace_service

router = APIRouter(tags=["financial-data"])


@router.get("/cases/{case_id}/financial-workspace", response_model=FinancialDataWorkspaceRead)
def get_case_financial_workspace(
    case_id: UUID, db: DbSession, ctx: Tenant
) -> FinancialDataWorkspaceRead:
    return financial_workspace_service.get_financial_workspace(db, ctx, case_id)
