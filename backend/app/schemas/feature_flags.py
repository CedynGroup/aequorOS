"""Deployment feature flags projected to the signed-in dashboard.

Exactly the booleans the dashboard needs to decide what to render; nothing
here is a secret, a limit or a URL. ``extra="forbid"`` on the response model
is the contract that a future flag is added deliberately, here, and never by
spreading a settings object into the body.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class FeatureFlagsRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: The BI routers are mounted and the Insights / Explore navigation may show.
    bi_enabled: bool
    #: Ingestion and the pipelines enqueue mart builds (marts may be current).
    bi_mart_enqueue_enabled: bool
    #: The recovery sweep, retention and subscriptions ride the scheduler tick.
    bi_scheduler_enabled: bool
    #: Natural-language questions may be asked. INDEPENDENT of ``bi_enabled``: the
    #: ask routes answer 409 rather than 404 when it is off, deliberately, so the
    #: flag cannot be probed by a caller — which makes the navigation the only thing
    #: that can decline the door, and makes projecting this flag the difference
    #: between a built surface and an invisible one.
    bi_nlq_enabled: bool
