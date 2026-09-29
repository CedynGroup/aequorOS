"""Deployment feature flags for the signed-in dashboard.

``GET /api/v1/feature-flags`` is how the dashboard learns whether BI is on in
THIS deployment, so it is mounted unconditionally — the one BI route that does
not sit behind ``BI_ENABLED``, because a flag nobody can read cannot gate
anything. Authenticated tenant route, no bank: the flags are deployment-wide,
and a caller with no institution coverage still needs to know which
navigation exists. It projects the BI booleans from ``BiSettings`` and nothing
else (no limits, no URLs, no keys — D-030 retired the grid licence field).

Every flag here is one the dashboard needs in order to decline a door or to say
plainly why something is not happening. ``bi_alerts_enabled`` and
``bi_subscriptions_enabled`` were added for the second reason: with them
unprojected, an alert that could never be evaluated read "Waiting for figures"
and a report that could never be sent read "Sending" — the deployment's switch
reported as the bank's problem (audit A360-2 M3).
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
        bi_nlq_enabled=bi.nlq_enabled,
        bi_alerts_enabled=bi.alerts_enabled,
        bi_subscriptions_enabled=bi.subscriptions_enabled,
    )
