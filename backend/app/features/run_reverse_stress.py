"""Reverse stress endpoints (Phase 2 item 4).

POST computes and persists the frontier (analyst+ — it mints an immutable
run); GET returns the latest frontier for a period.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter

from app.api.deps import DbSession, MutationTenant, Tenant
from app.schemas.reverse_stress import ReverseStressRead, ReverseStressRunCreate
from app.services import reverse_stress

router = APIRouter(tags=["reverse-stress"])


@router.post(
    "/banks/{bank_id}/reverse-stress/runs",
    response_model=ReverseStressRead,
    status_code=201,
    operation_id="runReverseStress",
)
def run_reverse_stress(
    bank_id: str,
    payload: ReverseStressRunCreate,
    db: DbSession,
    ctx: MutationTenant,
) -> ReverseStressRead:
    return reverse_stress.run_reverse_stress(db, ctx, bank_id, payload)


@router.get(
    "/banks/{bank_id}/reverse-stress/latest",
    response_model=ReverseStressRead,
    operation_id="getLatestReverseStress",
)
def get_latest_reverse_stress(
    bank_id: str,
    reporting_period_id: UUID,
    db: DbSession,
    ctx: Tenant,
) -> ReverseStressRead:
    return reverse_stress.get_latest_reverse_stress(db, ctx, bank_id, reporting_period_id)
