from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, status

from app.api.deps import DbSession, MutationTenant, Tenant
from app.schemas.regulatory_ftp import FtpDashboardRead, FtpScenarioBatchCreate
from app.schemas.regulatory_liquidity import RegulatoryRunBatchRead
from app.services import regulatory_ftp

router = APIRouter(tags=["regulatory-ftp"])


@router.post(
    "/banks/{bank_id}/ftp/run-all-scenarios",
    response_model=RegulatoryRunBatchRead,
    status_code=status.HTTP_201_CREATED,
    operation_id="runAllFtpScenarios",
)
def run_all_ftp_scenarios(
    bank_id: UUID,
    payload: FtpScenarioBatchCreate,
    db: DbSession,
    ctx: MutationTenant,
) -> RegulatoryRunBatchRead:
    return regulatory_ftp.run_all_ftp_scenarios(db, ctx, bank_id, payload)


@router.get(
    "/banks/{bank_id}/ftp/dashboard",
    response_model=FtpDashboardRead,
    operation_id="getFtpDashboard",
)
def get_ftp_dashboard(
    bank_id: UUID,
    db: DbSession,
    ctx: Tenant,
    reporting_period_id: Annotated[UUID | None, Query()] = None,
) -> FtpDashboardRead:
    return regulatory_ftp.get_ftp_dashboard(db, ctx, bank_id, reporting_period_id)
