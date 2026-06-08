from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query

from app.api.deps import DbSession, Tenant
from app.schemas.financial_workspace import (
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
