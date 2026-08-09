from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import DbSession, Tenant
from app.schemas.window_analytics import WindowAnalyticsRead
from app.services import window_analytics

router = APIRouter(tags=["window-analytics"])


@router.get(
    "/banks/{bank_id}/analytics/window",
    response_model=WindowAnalyticsRead,
    operation_id="computeWindowAnalytics",
)
def compute_window_analytics(
    bank_id: str,
    db: DbSession,
    ctx: Tenant,
    start_date: Annotated[date, Query()],
    end_date: Annotated[date, Query()],
) -> WindowAnalyticsRead:
    """Engine-computed analytics over [start_date, end_date]: per-period ratio
    series (stored baseline runs first, inline fallback), window statistics,
    and daily-snapshot aggregates per module."""
    return window_analytics.compute_window(
        db, ctx, bank_id, start_date=start_date, end_date=end_date
    )
