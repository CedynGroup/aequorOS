"""EWI dashboard + CFP lifecycle endpoints (LRMD 2026 ¶28(e)–(f), ¶70–77).

The EWI GET is the server-side replacement for the dashboard's illustrative
CFP page: indicator values, Board trigger levels, RAG states and the
escalation state are computed here, never in the frontend. Register and plan
writes are audited. The binding requirements and held register-update gate
are documented in docs/liquidity_enforcement_rollout.md.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, status

from app.api.deps import (
    ApproverTenant,
    DbSession,
    LiquidityConfidentialResource,
    ScopedMutationTenant,
)
from app.schemas.liquidity_cfp import (
    CfpActivationCreate,
    CfpApprove,
    CfpEventListRead,
    CfpEventRead,
    CfpPut,
    CfpRead,
    CfpSummaryRead,
    EwiDashboardRead,
    EwiRegisterPut,
)
from app.services import liquidity_cfp, liquidity_ewi

router = APIRouter(tags=["liquidity-cfp"])


@router.get(
    "/banks/{bank_id}/liquidity/ewis",
    response_model=EwiDashboardRead,
    operation_id="getLiquidityEwiDashboard",
)
def get_ewi_dashboard(
    bank_id: str,
    db: DbSession,
    access: LiquidityConfidentialResource,
    reporting_period_id: Annotated[UUID | None, Query()] = None,
) -> EwiDashboardRead:
    return liquidity_cfp.ewi_dashboard(
        db,
        access.ctx,
        access.bank.id,
        reporting_period_id,
    )


@router.put(
    "/banks/{bank_id}/liquidity/ewis",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="updateLiquidityEwiRegister",
)
def update_ewi_register(
    bank_id: str,
    payload: EwiRegisterPut,
    db: DbSession,
    ctx: ApproverTenant,
) -> None:
    liquidity_ewi.update_register(db, ctx, bank_id, payload)


@router.get(
    "/banks/{bank_id}/liquidity/cfp",
    response_model=CfpSummaryRead,
    operation_id="getContingencyFundingPlan",
)
def get_cfp(
    bank_id: str,
    db: DbSession,
    access: LiquidityConfidentialResource,
) -> CfpSummaryRead:
    return liquidity_cfp.get_cfp(db, access.ctx, access.bank.id)


@router.put(
    "/banks/{bank_id}/liquidity/cfp",
    response_model=CfpRead,
    operation_id="putContingencyFundingPlanDraft",
)
def put_cfp(
    bank_id: str,
    payload: CfpPut,
    db: DbSession,
    ctx: ScopedMutationTenant,
) -> CfpRead:
    return liquidity_cfp.put_cfp(db, ctx, bank_id, payload)


@router.post(
    "/banks/{bank_id}/liquidity/cfp/approve",
    response_model=CfpRead,
    operation_id="approveContingencyFundingPlan",
)
def approve_cfp(
    bank_id: str,
    payload: CfpApprove,
    db: DbSession,
    ctx: ScopedMutationTenant,
) -> CfpRead:
    return liquidity_cfp.approve_cfp(db, ctx, bank_id, payload)


@router.post(
    "/banks/{bank_id}/liquidity/cfp/activate",
    response_model=CfpEventRead,
    status_code=status.HTTP_201_CREATED,
    operation_id="activateContingencyFundingPlan",
)
def activate_cfp(
    bank_id: str,
    payload: CfpActivationCreate,
    db: DbSession,
    ctx: ScopedMutationTenant,
) -> CfpEventRead:
    return liquidity_cfp.activate_cfp(db, ctx, bank_id, payload)


@router.post(
    "/banks/{bank_id}/liquidity/cfp/de-escalate",
    response_model=CfpEventRead,
    status_code=status.HTTP_201_CREATED,
    operation_id="deEscalateContingencyFundingPlan",
)
def de_escalate_cfp(
    bank_id: str,
    payload: CfpActivationCreate,
    db: DbSession,
    ctx: ScopedMutationTenant,
) -> CfpEventRead:
    return liquidity_cfp.de_escalate_cfp(db, ctx, bank_id, payload)


@router.get(
    "/banks/{bank_id}/liquidity/cfp/events",
    response_model=CfpEventListRead,
    operation_id="listContingencyFundingPlanEvents",
)
def list_cfp_events(
    bank_id: str,
    db: DbSession,
    access: LiquidityConfidentialResource,
) -> CfpEventListRead:
    return liquidity_cfp.list_events(db, access.ctx, access.bank.id)
