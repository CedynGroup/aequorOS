"""Deployment feature flags for the signed-in dashboard.

``GET /api/v1/feature-flags`` is how the dashboard learns whether BI is on in
THIS deployment, so it is mounted unconditionally — the one BI route that does
not sit behind ``BI_ENABLED``, because a flag nobody can read cannot gate
anything. Authenticated tenant route, no bank: the flags are deployment-wide,
and a caller with no institution coverage still needs to know which
navigation exists. It projects three booleans from ``BiSettings`` and nothing
else (no limits, no URLs, no keys — D-030 retired the grid licence field).
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import Tenant
from app.core.config import get_settings
from app.schemas.feature_flags import FeatureFlagsRead

router = APIRouter(tags=["feature-flags"])


@router.get(
    "/feature-flags",
    response_model=FeatureFlagsRead,
    operation_id="readFeatureFlags",
)
def read_feature_flags(ctx: Tenant) -> FeatureFlagsRead:
    _ = ctx  # authentication is the whole gate; the flags are not per-tenant
    bi = get_settings().bi
    return FeatureFlagsRead(
        bi_enabled=bi.enabled,
        bi_mart_enqueue_enabled=bi.mart_enqueue_enabled,
        bi_scheduler_enabled=bi.scheduler_enabled,
    )
