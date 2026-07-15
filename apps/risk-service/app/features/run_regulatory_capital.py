from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, status

from app.api.deps import DbSession, MutationTenant, Tenant
from app.schemas.regulatory_capital import (
    Bsd2PreviewRead,
    CapitalDashboardRead,
    CapitalScenarioBatchCreate,
    CapitalStructureRead,
    RwaBreakdownRead,
)
from app.schemas.regulatory_liquidity import RegulatoryRunBatchRead
from app.services import regulatory_capital

router = APIRouter(tags=["regulatory-capital"])


@router.post(
    "/banks/{bank_id}/capital/run-all-scenarios",
    response_model=RegulatoryRunBatchRead,
    status_code=status.HTTP_201_CREATED,
    operation_id="runAllCapitalScenarios",
)
def run_all_capital_scenarios(
    bank_id: UUID,
    payload: CapitalScenarioBatchCreate,
    db: DbSession,
    ctx: MutationTenant,
) -> RegulatoryRunBatchRead:
    return regulatory_capital.run_all_capital_scenarios(db, ctx, bank_id, payload)


@router.get(
    "/banks/{bank_id}/capital/dashboard",
    response_model=CapitalDashboardRead,
    operation_id="getCapitalDashboard",
)
def get_capital_dashboard(
    bank_id: UUID,
    db: DbSession,
    ctx: Tenant,
    reporting_period_id: Annotated[UUID | None, Query()] = None,
) -> CapitalDashboardRead:
    return regulatory_capital.get_capital_dashboard(db, ctx, bank_id, reporting_period_id)


@router.get(
    "/banks/{bank_id}/capital/rwa",
    response_model=RwaBreakdownRead,
    operation_id="getRwaBreakdown",
)
def get_rwa_breakdown(
    bank_id: UUID,
    db: DbSession,
    ctx: Tenant,
    reporting_period_id: Annotated[UUID | None, Query()] = None,
) -> RwaBreakdownRead:
    return regulatory_capital.get_rwa_breakdown(db, ctx, bank_id, reporting_period_id)


@router.get(
    "/banks/{bank_id}/capital/structure",
    response_model=CapitalStructureRead,
    operation_id="getCapitalStructure",
)
def get_capital_structure(
    bank_id: UUID,
    db: DbSession,
    ctx: Tenant,
    reporting_period_id: Annotated[UUID | None, Query()] = None,
) -> CapitalStructureRead:
    return regulatory_capital.get_capital_structure(db, ctx, bank_id, reporting_period_id)


@router.get(
    "/banks/{bank_id}/submissions/bsd2",
    response_model=Bsd2PreviewRead,
    operation_id="getBsd2Preview",
)
def get_bsd2_preview(
    bank_id: UUID,
    reporting_period_id: Annotated[UUID, Query()],
    db: DbSession,
    ctx: Tenant,
) -> Bsd2PreviewRead:
    return regulatory_capital.get_bsd2_preview(db, ctx, bank_id, reporting_period_id)
