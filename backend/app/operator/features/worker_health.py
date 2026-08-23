"""GET /operator/v1/worker-health — authenticated worker readiness evidence."""

from __future__ import annotations

from fastapi import APIRouter

from app.core.config import get_settings
from app.operator.deps import Operator, OperatorDb
from app.operator.services import operator_views
from app.schemas.operator import WorkerHealthRead

router = APIRouter(prefix="/worker-health", tags=["operator-worker-health"])


@router.get("", response_model=WorkerHealthRead)
def get_worker_health(db: OperatorDb, _operator: Operator) -> WorkerHealthRead:
    """Read persisted heartbeats using the configured operations threshold."""
    return operator_views.worker_health(
        db,
        stale_after_seconds=get_settings().worker.worker_heartbeat_stale_seconds,
    )