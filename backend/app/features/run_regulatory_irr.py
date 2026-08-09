from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, status

from app.api.deps import DbSession, MutationTenant, Tenant
from app.schemas.regulatory_irr import (
    IrrDashboardRead,
    IrrEarAnalysisRead,
    IrrScenarioBatchCreate,
)
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
    bank_id: str,
    payload: IrrScenarioBatchCreate,
    db: DbSession,
    ctx: MutationTenant,
) -> RegulatoryRunBatchRead:
    return regulatory_irr.run_all_irr_scenarios(db, ctx, bank_id, payload)


@router.get(
    "/banks/{bank_id}/irr/ear-analysis",
    response_model=IrrEarAnalysisRead,
    operation_id="computeEarAnalysis",
)
def compute_ear_analysis(  # noqa: PLR0913 - the workbench seam names its full scope
    bank_id: str,
    db: DbSession,
    ctx: Tenant,
    reporting_period_id: Annotated[UUID, Query()],
    horizon_months: Annotated[int, Query()] = 12,
    delta_bp: Annotated[int, Query()] = 200,
) -> IrrEarAnalysisRead:
    """Pure desk analysis: generalized-horizon EaR on the canonical gap — writes nothing.

    The stored regulatory runs keep the 12-month ±200 bp figures; bounds are
    validated in the service (1..60 months, ±25..±500 bp on the 25 bp grid).
    """
    return regulatory_irr.compute_ear_analysis(
        db,
        ctx,
        bank_id,
        reporting_period_id,
        horizon_months=horizon_months,
        delta_bp=delta_bp,
    )


@router.get(
    "/banks/{bank_id}/irr/dashboard",
    response_model=IrrDashboardRead,
    operation_id="getIrrDashboard",
)
def get_irr_dashboard(
    bank_id: str,
    db: DbSession,
    ctx: Tenant,
    reporting_period_id: Annotated[UUID | None, Query()] = None,
) -> IrrDashboardRead:
    return regulatory_irr.get_irr_dashboard(db, ctx, bank_id, reporting_period_id)
