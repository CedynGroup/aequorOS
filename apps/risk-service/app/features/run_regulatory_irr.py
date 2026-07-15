from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, status

from app.api.deps import DbSession, MutationTenant, Tenant
from app.schemas.regulatory_irr import IrrDashboardRead, IrrScenarioBatchCreate
from app.schemas.regulatory_liquidity import RegulatoryRunBatchRead
from app.services import regulatory_irr

router = APIRouter(tags=["regulatory-irr"])


@router.post(
    "/banks/{bank_id}/irr/run-all-scenarios",
    response_model=RegulatoryRunBatchRead,
    status_code=status.HTTP_201_CREATED,
    operation_id="runAllIrrScenarios",
)
def run_all_irr_scenarios(
    bank_id: UUID,
    payload: IrrScenarioBatchCreate,
    db: DbSession,
    ctx: MutationTenant,
) -> RegulatoryRunBatchRead:
    return regulatory_irr.run_all_irr_scenarios(db, ctx, bank_id, payload)


@router.get(
    "/banks/{bank_id}/irr/dashboard",
    response_model=IrrDashboardRead,
    operation_id="getIrrDashboard",
)
def get_irr_dashboard(
    bank_id: UUID,
    db: DbSession,
    ctx: Tenant,
    reporting_period_id: Annotated[UUID | None, Query()] = None,
) -> IrrDashboardRead:
    return regulatory_irr.get_irr_dashboard(db, ctx, bank_id, reporting_period_id)
