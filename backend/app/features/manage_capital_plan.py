"""ICAAP capital-plan + quarterly ILAAP endpoints (Phase 2 item 10)."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, status

from app.api.deps import (
    CapitalConfidentialView,
    CapitalPlanApproveAccess,
    CapitalPlanWrite,
    DbSession,
    IlaapRefreshAccess,
)
from app.schemas.capital_plan import (
    CapitalPlanApprove,
    CapitalPlanPut,
    CapitalPlanRead,
    CapitalPlanSummaryRead,
    IlaapRefreshCreate,
    IlaapSnapshotListRead,
    IlaapSnapshotRead,
)
from app.services import capital_plan

router = APIRouter(tags=["capital-plan"])


@router.get(
    "/banks/{bank_id}/capital-plan",
    response_model=CapitalPlanSummaryRead,
    operation_id="getCapitalPlan",
)
def get_capital_plan(
    bank_id: str, db: DbSession, access: CapitalConfidentialView
) -> CapitalPlanSummaryRead:
    return capital_plan.get_capital_plan(db, access.ctx, bank_id)


@router.put(
    "/banks/{bank_id}/capital-plan",
    response_model=CapitalPlanRead,
    operation_id="putCapitalPlanDraft",
)
def put_capital_plan(
    bank_id: str, payload: CapitalPlanPut, db: DbSession, access: CapitalPlanWrite
) -> CapitalPlanRead:
    return capital_plan.put_capital_plan(db, access.ctx, bank_id, payload)


@router.post(
    "/banks/{bank_id}/capital-plan/approve",
    response_model=CapitalPlanRead,
    operation_id="approveCapitalPlan",
)
def approve_capital_plan(
    bank_id: str,
    payload: CapitalPlanApprove,
    db: DbSession,
    access: CapitalPlanApproveAccess,
) -> CapitalPlanRead:
    return capital_plan.approve_capital_plan(db, access.ctx, bank_id, payload)


@router.post(
    "/banks/{bank_id}/capital-plan/ilaap-refresh",
    response_model=IlaapSnapshotRead,
    status_code=status.HTTP_201_CREATED,
    operation_id="refreshIlaapComponent",
)
def refresh_ilaap(
    bank_id: str, payload: IlaapRefreshCreate, db: DbSession, access: IlaapRefreshAccess
) -> IlaapSnapshotRead:
    return capital_plan.refresh_ilaap(db, access.ctx, bank_id, payload)


@router.get(
    "/banks/{bank_id}/capital-plan/ilaap",
    response_model=IlaapSnapshotListRead,
    operation_id="listIlaapSnapshots",
)
def list_ilaap_snapshots(
    bank_id: str,
    db: DbSession,
    access: CapitalConfidentialView,
    reporting_period_id: Annotated[UUID | None, Query()] = None,
) -> IlaapSnapshotListRead:
    return capital_plan.list_ilaap_snapshots(db, access.ctx, bank_id, reporting_period_id)
