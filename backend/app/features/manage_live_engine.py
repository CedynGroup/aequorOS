from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, status

from app.api.deps import DbSession, MutationTenant, Tenant
from app.schemas.live import (
    BankAlertsRead,
    BankFreshnessRead,
    JobEnqueuedRead,
    LiveSummaryRead,
    OfficialRunRequest,
    RefreshRequest,
)
from app.services import alerts, freshness, live_view

router = APIRouter(tags=["live-engine"])


@router.get(
    "/banks/{bank_id}/live-summary",
    response_model=LiveSummaryRead,
    operation_id="getLiveSummary",
)
def get_live_summary(bank_id: UUID, db: DbSession, ctx: Tenant) -> LiveSummaryRead:
    return live_view.get_live_summary(db, ctx, bank_id)


@router.get(
    "/banks/{bank_id}/freshness",
    response_model=BankFreshnessRead,
    operation_id="getBankFreshness",
)
def get_bank_freshness(
    bank_id: UUID,
    db: DbSession,
    ctx: Tenant,
    reporting_period_id: Annotated[UUID | None, Query()] = None,
) -> BankFreshnessRead:
    return freshness.get_bank_freshness(db, ctx, bank_id, reporting_period_id)


@router.get(
    "/banks/{bank_id}/alerts",
    response_model=BankAlertsRead,
    operation_id="getBankAlerts",
)
def get_bank_alerts(
    bank_id: UUID,
    db: DbSession,
    ctx: Tenant,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> BankAlertsRead:
    return alerts.get_bank_alerts(db, ctx, bank_id, limit=limit)


@router.post(
    "/banks/{bank_id}/refresh",
    response_model=JobEnqueuedRead,
    status_code=status.HTTP_202_ACCEPTED,
    operation_id="refreshBankData",
)
def refresh_bank_data(
    bank_id: UUID,
    payload: RefreshRequest,
    db: DbSession,
    ctx: MutationTenant,
) -> JobEnqueuedRead:
    return live_view.refresh_bank_data(db, ctx, bank_id, payload)


@router.post(
    "/banks/{bank_id}/official-runs",
    response_model=JobEnqueuedRead,
    status_code=status.HTTP_202_ACCEPTED,
    operation_id="mintOfficialRun",
)
def mint_official_run(
    bank_id: UUID,
    payload: OfficialRunRequest,
    db: DbSession,
    ctx: MutationTenant,
) -> JobEnqueuedRead:
    return live_view.mint_official_run(db, ctx, bank_id, payload)
