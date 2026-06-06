from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter

from app.api.deps import DbSession, Tenant
from app.schemas.financial_workspace import FinancialDataWorkspaceRead
from app.schemas.financial_workspace_mapping import (
    FinancialWorkspaceMapRequest,
    FinancialWorkspaceMapResponse,
)
from app.services import financial_workspace as financial_workspace_service
from app.services.financial_mapping import map_financial_workspace

router = APIRouter(tags=["financial-data"])


@router.get("/cases/{case_id}/financial-workspace", response_model=FinancialDataWorkspaceRead)
def get_case_financial_workspace(
    case_id: UUID, db: DbSession, ctx: Tenant
) -> FinancialDataWorkspaceRead:
    return financial_workspace_service.get_financial_workspace(db, ctx, case_id)


@router.post(
    "/cases/{case_id}/financial-workspace/map",
    response_model=FinancialWorkspaceMapResponse,
)
def map_case_financial_workspace(
    case_id: UUID,
    payload: FinancialWorkspaceMapRequest,
    db: DbSession,
    ctx: Tenant,
) -> FinancialWorkspaceMapResponse:
    return map_financial_workspace(db, ctx, case_id, payload)
