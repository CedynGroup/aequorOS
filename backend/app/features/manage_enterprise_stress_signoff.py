"""Enterprise-stress sign-off / Board attestation endpoints (docs/stress.md §3.8).

The stress-run governance workflow the directive's Part II requires (¶20, ¶57–63):
an analyst prepares the narrative + assumptions rationale on a succeeded
enterprise-stress run, submits it, and a Board/approver officer attests framework
+ results with a credibility rationale. Only an attested sign-off makes the run
eligible to feed the ICAAP Appendix II submission. RLS-scoped, maker-checker.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, status

from app.api.deps import ApproverTenant, DbSession, MutationTenant, Tenant
from app.schemas.enterprise_stress_signoff import (
    StressSignoffAttestation,
    StressSignoffCreate,
    StressSignoffListRead,
    StressSignoffRead,
    StressSignoffStatus,
    StressSignoffTransition,
    StressSignoffUpdate,
)
from app.services import enterprise_stress_signoff

router = APIRouter(tags=["enterprise-stress-signoff"])

_BASE = "/banks/{bank_id}/enterprise-stress/signoffs"


@router.post(
    _BASE,
    response_model=StressSignoffRead,
    status_code=status.HTTP_201_CREATED,
    operation_id="createEnterpriseStressSignoff",
)
def create_signoff(
    bank_id: str,
    payload: StressSignoffCreate,
    db: DbSession,
    ctx: MutationTenant,
) -> StressSignoffRead:
    return enterprise_stress_signoff.create_signoff(db, ctx, bank_id, payload)


@router.get(
    _BASE,
    response_model=StressSignoffListRead,
    operation_id="listEnterpriseStressSignoffs",
)
def list_signoffs(
    bank_id: str,
    db: DbSession,
    ctx: Tenant,
    reporting_period_id: Annotated[UUID | None, Query()] = None,
    status_filter: Annotated[StressSignoffStatus | None, Query(alias="status")] = None,
) -> StressSignoffListRead:
    return enterprise_stress_signoff.list_signoffs(
        db, ctx, bank_id, reporting_period_id, status_filter
    )


@router.get(
    _BASE + "/{signoff_id}",
    response_model=StressSignoffRead,
    operation_id="getEnterpriseStressSignoff",
)
def get_signoff(
    bank_id: str,
    signoff_id: UUID,
    db: DbSession,
    ctx: Tenant,
) -> StressSignoffRead:
    return enterprise_stress_signoff.get_signoff(db, ctx, bank_id, signoff_id)


@router.patch(
    _BASE + "/{signoff_id}",
    response_model=StressSignoffRead,
    operation_id="updateEnterpriseStressSignoff",
)
def update_signoff(
    bank_id: str,
    signoff_id: UUID,
    payload: StressSignoffUpdate,
    db: DbSession,
    ctx: MutationTenant,
) -> StressSignoffRead:
    return enterprise_stress_signoff.update_signoff(db, ctx, bank_id, signoff_id, payload)


@router.post(
    _BASE + "/{signoff_id}/submit",
    response_model=StressSignoffRead,
    operation_id="submitEnterpriseStressSignoff",
)
def submit_signoff(
    bank_id: str,
    signoff_id: UUID,
    payload: StressSignoffTransition,
    db: DbSession,
    ctx: MutationTenant,
) -> StressSignoffRead:
    return enterprise_stress_signoff.submit_signoff(db, ctx, bank_id, signoff_id, payload)


@router.post(
    _BASE + "/{signoff_id}/attest",
    response_model=StressSignoffRead,
    operation_id="attestEnterpriseStressSignoff",
)
def attest_signoff(
    bank_id: str,
    signoff_id: UUID,
    payload: StressSignoffAttestation,
    db: DbSession,
    ctx: ApproverTenant,
) -> StressSignoffRead:
    return enterprise_stress_signoff.attest_signoff(db, ctx, bank_id, signoff_id, payload)


@router.post(
    _BASE + "/{signoff_id}/withdraw",
    response_model=StressSignoffRead,
    operation_id="withdrawEnterpriseStressSignoff",
)
def withdraw_signoff(
    bank_id: str,
    signoff_id: UUID,
    payload: StressSignoffTransition,
    db: DbSession,
    ctx: MutationTenant,
) -> StressSignoffRead:
    return enterprise_stress_signoff.withdraw_signoff(db, ctx, bank_id, signoff_id, payload)
