"""ECL assumption, CRM haircut, concentration-limit and credit-threshold registers.

Reads require one complete active CREDIT / ``restricted`` / ``view`` binding for
the exact institution, covering the whole institution: the registers are the
Board's adopted assumptions and limits, and a concentration-limit row can name a
counterparty group. Capital authority does not admit them.

Approver-gated PUTs are unchanged and still on the scalar approver gate; moving
them to a dedicated Configurator authority is issue 184, not this cutover.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import ApproverTenant, CreditRestrictedView, DbSession
from app.credit.schemas.credit_params import (
    CrmHaircutRegisterRead,
    CrmHaircutUpdate,
    EclAssumptionRegisterRead,
    EclAssumptionUpdate,
)
from app.credit.schemas.regulatory_credit import (
    ConcentrationLimitRegisterRead,
    ConcentrationLimitUpdate,
    CreditThresholdRegisterRead,
    CreditThresholdUpdate,
)
from app.credit.service import credit_params

router = APIRouter(tags=["credit-params"])


@router.get(
    "/banks/{bank_id}/ecl-assumptions",
    response_model=EclAssumptionRegisterRead,
    operation_id="getEclAssumptionRegister",
)
def get_ecl_assumption_register(
    bank_id: str,
    db: DbSession,
    access: CreditRestrictedView,
    as_of: Annotated[date | None, Query()] = None,
) -> EclAssumptionRegisterRead:
    resolved_as_of = as_of or date.today()  # noqa: DTZ011 - date-only business resolution
    return credit_params.get_ecl_register(db, access.ctx, bank_id, resolved_as_of)


@router.put(
    "/banks/{bank_id}/ecl-assumptions",
    response_model=EclAssumptionRegisterRead,
    operation_id="updateEclAssumptionRegister",
)
def update_ecl_assumption_register(
    bank_id: str,
    payload: EclAssumptionUpdate,
    db: DbSession,
    ctx: ApproverTenant,
) -> EclAssumptionRegisterRead:
    return credit_params.update_ecl_register(db, ctx, bank_id, payload)


@router.get(
    "/banks/{bank_id}/crm-haircuts",
    response_model=CrmHaircutRegisterRead,
    operation_id="getCrmHaircutRegister",
)
def get_crm_haircut_register(
    bank_id: str,
    db: DbSession,
    access: CreditRestrictedView,
    as_of: Annotated[date | None, Query()] = None,
) -> CrmHaircutRegisterRead:
    resolved_as_of = as_of or date.today()  # noqa: DTZ011 - date-only business resolution
    return credit_params.get_crm_register(db, access.ctx, bank_id, resolved_as_of)


@router.put(
    "/banks/{bank_id}/crm-haircuts",
    response_model=CrmHaircutRegisterRead,
    operation_id="updateCrmHaircutRegister",
)
def update_crm_haircut_register(
    bank_id: str,
    payload: CrmHaircutUpdate,
    db: DbSession,
    ctx: ApproverTenant,
) -> CrmHaircutRegisterRead:
    return credit_params.update_crm_register(db, ctx, bank_id, payload)


@router.get(
    "/banks/{bank_id}/concentration-limits",
    response_model=ConcentrationLimitRegisterRead,
    operation_id="getConcentrationLimitRegister",
)
def get_concentration_limit_register(
    bank_id: str,
    db: DbSession,
    access: CreditRestrictedView,
    as_of: Annotated[date | None, Query()] = None,
) -> ConcentrationLimitRegisterRead:
    resolved_as_of = as_of or date.today()  # noqa: DTZ011 - date-only business resolution
    return credit_params.get_concentration_limit_register(db, access.ctx, bank_id, resolved_as_of)


@router.put(
    "/banks/{bank_id}/concentration-limits",
    response_model=ConcentrationLimitRegisterRead,
    operation_id="updateConcentrationLimitRegister",
)
def update_concentration_limit_register(
    bank_id: str,
    payload: ConcentrationLimitUpdate,
    db: DbSession,
    ctx: ApproverTenant,
) -> ConcentrationLimitRegisterRead:
    return credit_params.update_concentration_limit_register(db, ctx, bank_id, payload)


@router.get(
    "/banks/{bank_id}/credit-thresholds",
    response_model=CreditThresholdRegisterRead,
    operation_id="getCreditThresholdRegister",
)
def get_credit_threshold_register(
    bank_id: str,
    db: DbSession,
    access: CreditRestrictedView,
    as_of: Annotated[date | None, Query()] = None,
) -> CreditThresholdRegisterRead:
    resolved_as_of = as_of or date.today()  # noqa: DTZ011 - date-only business resolution
    return credit_params.get_credit_threshold_register(db, access.ctx, bank_id, resolved_as_of)


@router.put(
    "/banks/{bank_id}/credit-thresholds",
    response_model=CreditThresholdRegisterRead,
    operation_id="updateCreditThresholdRegister",
)
def update_credit_threshold_register(
    bank_id: str,
    payload: CreditThresholdUpdate,
    db: DbSession,
    ctx: ApproverTenant,
) -> CreditThresholdRegisterRead:
    return credit_params.update_credit_threshold_register(db, ctx, bank_id, payload)
