"""GET /operator/v1/jobs — cross-tenant worker-attribution board.

Also carries the stranded-work board (``/jobs/stuck-dedup``): batches whose
out-of-band dedup pass exhausted every attempt and which the queue will never
touch again. Reading is fleet-wide; ACTING on one is per-tenant, session-gated
and audited through ``POST /tenants/{org}/fix/redrive-dedup``.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from app.operator.deps import Operator, OperatorDb
from app.operator.services import operator_views
from app.schemas.operator import OperatorJobsRead, StuckDedupBatchesRead

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

@router.get("/stuck-dedup", response_model=StuckDedupBatchesRead)
def list_stuck_dedup_batches(
    db: OperatorDb,
    _operator: Operator,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> StuckDedupBatchesRead:
    """Batches whose deduplication pass exhausted every attempt, newest first.

    The backlog nobody could see: a batch stuck on ``dedup_status="deferred"``
    reads identically to one queued a minute ago, and the queue stops retrying
    at ``max_attempts``. This pairs each such batch with the terminal job and
    the error that stranded it, so an operator diagnoses BEFORE re-driving —
    the four batches on the primary failed for three unrelated reasons.
    """
    return operator_views.stuck_dedup_batches(db, limit=limit)
