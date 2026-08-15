"""GET /operator/v1/jobs — cross-tenant worker-attribution board."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from app.operator.deps import Operator, OperatorDb
from app.operator.services import operator_views
from app.schemas.operator import OperatorJobsRead

router = APIRouter(prefix="/jobs", tags=["operator-jobs"])


@router.get("", response_model=OperatorJobsRead)
def list_jobs(
    db: OperatorDb,
    _operator: Operator,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    status: Annotated[str | None, Query(min_length=1, max_length=40)] = None,
) -> OperatorJobsRead:
    """All queued/running/terminal jobs, including their claiming runtime."""
    return operator_views.list_jobs(db, limit=limit, status_filter=status)