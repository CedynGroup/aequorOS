from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query

from app.api.deps import DbSession, Tenant
from app.models import FinancialCashFlow
from app.schemas.financial_workspace import (
    FinancialCashFlowCreate,
    FinancialCashFlowRead,
    FinancialCashFlowUpdate,
    FinancialDataWorkspaceRead,
    FinancialValidationEntityType,
    FinancialValidationIssueRead,
    FinancialValidationRunResponse,
    FinancialValidationSeverity,
)
from app.schemas.financial_workspace_mapping import (
    FinancialWorkspaceMapRequest,
    FinancialWorkspaceMapResponse,
)
from app.services import financial_validation as financial_validation_service
from app.services import financial_workspace as financial_workspace_service
from app.services.financial_cash_flows import create_cash_flow, update_cash_flow
from app.services.financial_mapping.service import map_financial_workspace

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


@router.post(
    "/cases/{case_id}/financial-data/validate",
    response_model=FinancialValidationRunResponse,
)
def validate_case_financial_data(
    case_id: UUID,
    db: DbSession,
    ctx: Tenant,
) -> FinancialValidationRunResponse:
    return financial_validation_service.validate_financial_data(db, ctx, case_id)


@router.get(
    "/cases/{case_id}/financial-data/validation-issues",
    response_model=list[FinancialValidationIssueRead],
)
def list_case_financial_validation_issues(
    case_id: UUID,
    db: DbSession,
    ctx: Tenant,
    severity: Annotated[FinancialValidationSeverity | None, Query()] = None,
    entity_type: Annotated[FinancialValidationEntityType | None, Query()] = None,
) -> list[FinancialValidationIssueRead]:
    return financial_validation_service.list_validation_issues(
        db,
        ctx,
        case_id,
        severity=severity,
        entity_type=entity_type,
    )


@router.post(
    "/cases/{case_id}/financial-workspace/cash-flows",
    response_model=FinancialCashFlowRead,
)
def create_case_financial_cash_flow(
    case_id: UUID,
    payload: FinancialCashFlowCreate,
    db: DbSession,
    ctx: Tenant,
) -> FinancialCashFlow:
    return create_cash_flow(db, ctx, case_id, payload)


@router.patch(
    "/cases/{case_id}/financial-workspace/cash-flows/{cash_flow_id}",
    response_model=FinancialCashFlowRead,
)
def update_case_financial_cash_flow(
    case_id: UUID,
    cash_flow_id: UUID,
    payload: FinancialCashFlowUpdate,
    db: DbSession,
    ctx: Tenant,
) -> FinancialCashFlow:
    return update_cash_flow(db, ctx, case_id, cash_flow_id, payload)
