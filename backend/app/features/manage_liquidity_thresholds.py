"""Board threshold register endpoints (LMTD 2026 ¶11(b)–(e)).

GET resolves the thresholds active on a date (Board register over regulatory
defaults) plus the full generation history — the artifact behind "show me
your Board-approved thresholds." PUT records a new Board-approved generation;
approver-gated because adopting Board thresholds is a risk-configuration act,
and audited like every configuration change.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import ApproverTenant, DbSession, Tenant
from app.schemas.liquidity_thresholds import (
    LiquidityHaircutScheduleRead,
    LiquidityHaircutUpdate,
    LiquidityThresholdRegisterRead,
    LiquidityThresholdUpdate,
)
from app.services import liquidity_thresholds

router = APIRouter(tags=["liquidity-thresholds"])


@router.get(
    "/banks/{bank_id}/liquidity-thresholds",
    response_model=LiquidityThresholdRegisterRead,
    operation_id="getLiquidityThresholdRegister",
)
def get_liquidity_threshold_register(
    bank_id: str,
    db: DbSession,
    ctx: Tenant,
    as_of: Annotated[date | None, Query()] = None,
) -> LiquidityThresholdRegisterRead:
    resolved_as_of = as_of or date.today()  # noqa: DTZ011 - date-only business resolution
    return liquidity_thresholds.get_register(db, ctx, bank_id, resolved_as_of)


@router.put(
    "/banks/{bank_id}/liquidity-thresholds",
    response_model=LiquidityThresholdRegisterRead,
    operation_id="updateLiquidityThresholdRegister",
)
def update_liquidity_threshold_register(
    bank_id: str,
    payload: LiquidityThresholdUpdate,
    db: DbSession,
    ctx: ApproverTenant,
) -> LiquidityThresholdRegisterRead:
    return liquidity_thresholds.update_register(db, ctx, bank_id, payload)


@router.get(
    "/banks/{bank_id}/liquidity-haircuts",
    response_model=LiquidityHaircutScheduleRead,
    operation_id="getLiquidityHaircutSchedule",
)
def get_liquidity_haircut_schedule(
    bank_id: str,
    db: DbSession,
    ctx: Tenant,
    as_of: Annotated[date | None, Query()] = None,
) -> LiquidityHaircutScheduleRead:
    resolved_as_of = as_of or date.today()  # noqa: DTZ011 - date-only business resolution
    return liquidity_thresholds.get_haircut_schedule(db, ctx, bank_id, resolved_as_of)


@router.put(
    "/banks/{bank_id}/liquidity-haircuts",
    response_model=LiquidityHaircutScheduleRead,
    operation_id="updateLiquidityHaircutSchedule",
)
def update_liquidity_haircut_schedule(
    bank_id: str,
    payload: LiquidityHaircutUpdate,
    db: DbSession,
    ctx: ApproverTenant,
) -> LiquidityHaircutScheduleRead:
    return liquidity_thresholds.update_haircut_schedule(db, ctx, bank_id, payload)
