"""Reverse stress endpoints (Phase 2 item 4).

POST computes and persists the frontier — it mints an immutable run, so it
requires Forecasting confidential run; GET returns the latest frontier for a
period under Forecasting confidential view. The service re-checks run
authority before it searches.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter

from app.api.deps import DbSession, ForecastingConfidentialView, ForecastingRun
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
    access: ForecastingRun,
) -> ReverseStressRead:
    return reverse_stress.run_reverse_stress(db, access.ctx, bank_id, payload)


@router.get(
    "/banks/{bank_id}/reverse-stress/latest",
    response_model=ReverseStressRead,
    operation_id="getLatestReverseStress",
)
def get_latest_reverse_stress(
    bank_id: str,
    reporting_period_id: UUID,
    db: DbSession,
    access: ForecastingConfidentialView,
) -> ReverseStressRead:
    return reverse_stress.get_latest_reverse_stress(db, access.ctx, bank_id, reporting_period_id)
