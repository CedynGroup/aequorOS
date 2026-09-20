from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, status

from app.api.deps import DbSession, ScopedMutationTenant, Tenant
from app.core.authorization import Module, Permission, Sensitivity
from app.schemas.regulatory_ftp import FtpDashboardRead, FtpScenarioBatchCreate
from app.schemas.regulatory_liquidity import RegulatoryRunBatchRead
from app.services import regulatory_ftp, scoped_authorization

router = APIRouter(tags=["regulatory-ftp"])


@router.post(
    "/banks/{bank_id}/ftp/run-all-scenarios",
    response_model=RegulatoryRunBatchRead,
    status_code=status.HTTP_201_CREATED,
    operation_id="runAllFtpScenarios",
)
def run_all_ftp_scenarios(
    bank_id: str,
    payload: FtpScenarioBatchCreate,
    db: DbSession,
    ctx: ScopedMutationTenant,
) -> RegulatoryRunBatchRead:
    bank = scoped_authorization.require_bank_permission(
        db,
        ctx,
        bank_id,
        permission=Permission.RUN,
        module=Module.FTP,
        sensitivity=Sensitivity.CONFIDENTIAL,
        surface="ftp_run_all_scenarios",
        denial_detail="Running FTP calculations requires an active scoped binding.",
    )
    return regulatory_ftp.run_all_ftp_scenarios(
        db,
        ctx,
        bank.id,
        payload,
        resolved_bank=bank,
    )


@router.get(
    "/banks/{bank_id}/ftp/dashboard",
    response_model=FtpDashboardRead,
    operation_id="getFtpDashboard",
)
def get_ftp_dashboard(
    bank_id: str,
    db: DbSession,
    ctx: Tenant,
    reporting_period_id: Annotated[UUID | None, Query()] = None,
) -> FtpDashboardRead:
    bank = scoped_authorization.require_bank_permission_prefetched(
        db,
        ctx,
        bank_id,
        permission=Permission.VIEW,
        module=Module.FTP,
        sensitivity=Sensitivity.AGGREGATED,
        surface="ftp_dashboard",
        denial_detail="FTP access requires an active scoped binding.",
    )
    return regulatory_ftp.get_ftp_dashboard(
        db,
        ctx,
        bank.id,
        reporting_period_id,
        resolved_bank=bank,
    )
