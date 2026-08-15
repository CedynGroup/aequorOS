"""GET /operator/v1/overview — the console-home fleet rollup.

A counts-only cross-tenant snapshot (tenants, ingestion, jobs, connections, desk
maker-checker queues) plus a bounded ``needs_attention`` list. No per-tenant
detail is returned here, so this read is not itself audited — it is a dashboard
poll, not an enumeration of tenant state.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.operator.deps import Operator, OperatorDb
from app.operator.services import operator_views
from app.schemas.operator import FleetOverviewRead

router = APIRouter(tags=["operator-overview"])


@router.get("/overview", response_model=FleetOverviewRead)
def get_overview(db: OperatorDb, _operator: Operator) -> FleetOverviewRead:
    return operator_views.fleet_overview(db)
