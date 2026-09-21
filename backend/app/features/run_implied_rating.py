from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, status

from app.api.deps import DbSession, MarketsConfidentialView, MarketsRun
from app.schemas.implied_rating import (
    ImpliedRatingRunCreate,
    ImpliedRatingRunListRead,
    ImpliedRatingRunRead,
)
from app.services import implied_rating

router = APIRouter(tags=["implied-rating"])


def _read(row: object) -> ImpliedRatingRunRead:
    return ImpliedRatingRunRead.model_validate(row, from_attributes=True)


@router.post(
    "/banks/{bank_id}/implied-rating/runs",
    response_model=ImpliedRatingRunRead,
    status_code=status.HTTP_201_CREATED,
    operation_id="runImpliedBankRating",
)
def run_implied_bank_rating(
    bank_id: str,
    payload: ImpliedRatingRunCreate,
    db: DbSession,
    access: MarketsRun,
) -> ImpliedRatingRunRead:
    return _read(
        implied_rating.run(
            db,
            access.ctx,
            access.bank.id,
            payload.reporting_period_id,
            support_uplift_notches=payload.support_uplift_notches,
        )
    )


@router.get(
    "/banks/{bank_id}/implied-rating/runs",
    response_model=ImpliedRatingRunListRead,
    operation_id="listImpliedBankRatingRuns",
)
def list_implied_bank_rating_runs(
    bank_id: str, db: DbSession, access: MarketsConfidentialView
) -> ImpliedRatingRunListRead:
    return ImpliedRatingRunListRead(
        bank_id=access.bank.id,
        runs=[_read(row) for row in implied_rating.list_runs(db, access.ctx, access.bank.id)],
    )


@router.get(
    "/banks/{bank_id}/implied-rating/runs/{run_id}",
    response_model=ImpliedRatingRunRead,
    operation_id="getImpliedBankRatingRun",
)
def get_implied_bank_rating_run(
    bank_id: str, run_id: UUID, db: DbSession, access: MarketsConfidentialView
) -> ImpliedRatingRunRead:
    return _read(implied_rating.get_run(db, access.ctx, access.bank.id, run_id))
