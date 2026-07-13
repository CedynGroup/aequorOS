from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status

from app.api.deps import DbSession, MutationTenant, Tenant
from app.models import FinancialCashFlow
from app.schemas.financial_workspace import (
    FinancialAccountCreate,
    FinancialAccountMutationResponse,
    FinancialAccountUpdate,
    FinancialBalanceCreate,
    FinancialBalanceMutationResponse,
    FinancialBalanceUpdate,
    FinancialCashFlowCreate,
    FinancialCashFlowRead,
    FinancialCashFlowUpdate,
    FinancialCovenantCreate,
    FinancialCovenantMutationResponse,
    FinancialCovenantUpdate,
    FinancialDataWorkspaceRead,
    FinancialInstitutionCreate,
    FinancialInstitutionMutationResponse,
    FinancialInstitutionUpdate,
    FinancialObligationCreate,
    FinancialObligationMutationResponse,
    FinancialObligationUpdate,
    FinancialReportingPeriodCreate,
    FinancialReportingPeriodMutationResponse,
    FinancialReportingPeriodUpdate,
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
from app.services.financial_canonical_edits import (
    create_account,
    create_balance,
    create_institution,
    create_obligation,
    create_reporting_period,
    update_account,
    update_balance,
    update_institution,
    update_obligation,
    update_reporting_period,
)
from app.services.financial_cash_flows import create_cash_flow, update_cash_flow
from app.services.financial_covenants import create_covenant, update_covenant
from app.services.financial_mapping.service import map_financial_workspace

router = APIRouter(tags=["financial-data"])

MUTATION_DESCRIPTION = (
    "Resource-specific canonical mutation contract selected for AEQ-18/AEQ-20. "
    "Only fields declared by this resource schema are accepted. The response includes "
    "the updated record and validation refreshed after the write."
)


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


@router.post(
    "/cases/{case_id}/financial-workspace/institutions",
    response_model=FinancialInstitutionMutationResponse,
    description=MUTATION_DESCRIPTION,
)
def create_case_financial_institution(
    case_id: UUID, payload: FinancialInstitutionCreate, db: DbSession, ctx: MutationTenant
) -> FinancialInstitutionMutationResponse:
    return create_institution(db, ctx, case_id, payload)


@router.patch(
    "/cases/{case_id}/financial-workspace/institutions/{institution_id}",
    response_model=FinancialInstitutionMutationResponse,
    description=MUTATION_DESCRIPTION,
)
def update_case_financial_institution(
    case_id: UUID,
    institution_id: UUID,
    payload: FinancialInstitutionUpdate,
    db: DbSession,
    ctx: MutationTenant,
) -> FinancialInstitutionMutationResponse:
    return update_institution(db, ctx, case_id, institution_id, payload)


@router.post(
    "/cases/{case_id}/financial-workspace/accounts",
    response_model=FinancialAccountMutationResponse,
    description=MUTATION_DESCRIPTION,
)
def create_case_financial_account(
    case_id: UUID, payload: FinancialAccountCreate, db: DbSession, ctx: MutationTenant
) -> FinancialAccountMutationResponse:
    return create_account(db, ctx, case_id, payload)


@router.patch(
    "/cases/{case_id}/financial-workspace/accounts/{account_id}",
    response_model=FinancialAccountMutationResponse,
    description=MUTATION_DESCRIPTION,
)
def update_case_financial_account(
    case_id: UUID,
    account_id: UUID,
    payload: FinancialAccountUpdate,
    db: DbSession,
    ctx: MutationTenant,
) -> FinancialAccountMutationResponse:
    return update_account(db, ctx, case_id, account_id, payload)


@router.post(
    "/cases/{case_id}/financial-workspace/reporting-periods",
    response_model=FinancialReportingPeriodMutationResponse,
    description=MUTATION_DESCRIPTION,
)
def create_case_financial_reporting_period(
    case_id: UUID,
    payload: FinancialReportingPeriodCreate,
    db: DbSession,
    ctx: MutationTenant,
) -> FinancialReportingPeriodMutationResponse:
    return create_reporting_period(db, ctx, case_id, payload)


@router.patch(
    "/cases/{case_id}/financial-workspace/reporting-periods/{reporting_period_id}",
    response_model=FinancialReportingPeriodMutationResponse,
    description=MUTATION_DESCRIPTION,
)
def update_case_financial_reporting_period(
    case_id: UUID,
    reporting_period_id: UUID,
    payload: FinancialReportingPeriodUpdate,
    db: DbSession,
    ctx: MutationTenant,
) -> FinancialReportingPeriodMutationResponse:
    return update_reporting_period(db, ctx, case_id, reporting_period_id, payload)


@router.post(
    "/cases/{case_id}/financial-workspace/balances",
    response_model=FinancialBalanceMutationResponse,
    description=MUTATION_DESCRIPTION,
)
def create_case_financial_balance(
    case_id: UUID, payload: FinancialBalanceCreate, db: DbSession, ctx: MutationTenant
) -> FinancialBalanceMutationResponse:
    return create_balance(db, ctx, case_id, payload)


@router.patch(
    "/cases/{case_id}/financial-workspace/balances/{balance_id}",
    response_model=FinancialBalanceMutationResponse,
    description=MUTATION_DESCRIPTION,
)
def update_case_financial_balance(
    case_id: UUID,
    balance_id: UUID,
    payload: FinancialBalanceUpdate,
    db: DbSession,
    ctx: MutationTenant,
) -> FinancialBalanceMutationResponse:
    return update_balance(db, ctx, case_id, balance_id, payload)


@router.post(
    "/cases/{case_id}/financial-workspace/obligations",
    response_model=FinancialObligationMutationResponse,
    description=MUTATION_DESCRIPTION,
)
def create_case_financial_obligation(
    case_id: UUID, payload: FinancialObligationCreate, db: DbSession, ctx: MutationTenant
) -> FinancialObligationMutationResponse:
    return create_obligation(db, ctx, case_id, payload)


@router.patch(
    "/cases/{case_id}/financial-workspace/obligations/{obligation_id}",
    response_model=FinancialObligationMutationResponse,
    description=MUTATION_DESCRIPTION,
)
def update_case_financial_obligation(
    case_id: UUID,
    obligation_id: UUID,
    payload: FinancialObligationUpdate,
    db: DbSession,
    ctx: MutationTenant,
) -> FinancialObligationMutationResponse:
    return update_obligation(db, ctx, case_id, obligation_id, payload)


@router.post(
    "/cases/{case_id}/financial-workspace/covenants",
    response_model=FinancialCovenantMutationResponse,
    description=MUTATION_DESCRIPTION,
)
def create_case_financial_covenant(
    case_id: UUID, payload: FinancialCovenantCreate, db: DbSession, ctx: MutationTenant
) -> FinancialCovenantMutationResponse:
    return create_covenant(db, ctx, case_id, payload)


@router.patch(
    "/cases/{case_id}/financial-workspace/covenants/{covenant_id}",
    response_model=FinancialCovenantMutationResponse,
    description=MUTATION_DESCRIPTION,
)
def update_case_financial_covenant(
    case_id: UUID,
    covenant_id: UUID,
    payload: FinancialCovenantUpdate,
    db: DbSession,
    ctx: MutationTenant,
) -> FinancialCovenantMutationResponse:
    return update_covenant(db, ctx, case_id, covenant_id, payload)


@router.post(
    "/cases/{case_id}/financial-workspace/{unsupported_entity_type}", include_in_schema=False
)
def reject_unsupported_financial_entity_create(case_id: UUID, unsupported_entity_type: str) -> None:
    _ = case_id
    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail={
            "code": "unsupported_financial_entity_type",
            "entity_type": unsupported_entity_type,
        },
    )


@router.patch(
    "/cases/{case_id}/financial-workspace/{unsupported_entity_type}/{entity_id}",
    include_in_schema=False,
)
def reject_unsupported_financial_entity_update(
    case_id: UUID, unsupported_entity_type: str, entity_id: UUID
) -> None:
    _ = (case_id, entity_id)
    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail={
            "code": "unsupported_financial_entity_type",
            "entity_type": unsupported_entity_type,
        },
    )
