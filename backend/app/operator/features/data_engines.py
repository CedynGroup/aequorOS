"""GET /operator/v1/data-engines — cross-tenant connection health wall."""

from __future__ import annotations

from fastapi import APIRouter

from app.operator.deps import Operator, OperatorDb
from app.operator.services import operator_views
from app.schemas.operator import DataEnginesRead

router = APIRouter(prefix="/data-engines", tags=["operator-data-engines"])


@router.get("", response_model=DataEnginesRead)
def list_data_engines(db: OperatorDb, _operator: Operator) -> DataEnginesRead:
    """Every market-data / Database-Direct / T24 connection's lifecycle
    status and last-activity metadata. Credential material never appears —
    the vault does not return it and this view does not ask."""
    return operator_views.list_data_engine_connections(db)
