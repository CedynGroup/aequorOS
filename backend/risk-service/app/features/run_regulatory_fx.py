from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, status

from app.api.deps import DbSession, MutationTenant, Tenant
from app.schemas.regulatory_fx import FxDashboardRead, FxScenarioBatchCreate
from app.schemas.regulatory_liquidity import RegulatoryRunBatchRead
from app.services import regulatory_fx

router = APIRouter(tags=["regulatory-fx"])


@router.post(
    "/banks/{bank_id}/fx/run-all-scenarios",
    response_model=RegulatoryRunBatchRead,
    status_code=status.HTTP_201_CREATED,
    operation_id="runAllFxScenarios",
)
def run_all_fx_scenarios(
    bank_id: UUID,
    payload: FxScenarioBatchCreate,
    db: DbSession,
    ctx: MutationTenant,
) -> RegulatoryRunBatchRead:
    return regulatory_fx.run_all_fx_scenarios(db, ctx, bank_id, payload)


@router.get(
    "/banks/{bank_id}/fx/dashboard",
    response_model=FxDashboardRead,
    operation_id="getFxDashboard",
)
def get_fx_dashboard(
    bank_id: UUID,
    db: DbSession,
    ctx: Tenant,
    reporting_period_id: Annotated[UUID | None, Query()] = None,
) -> FxDashboardRead:
    return regulatory_fx.get_fx_dashboard(db, ctx, bank_id, reporting_period_id)
