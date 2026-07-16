"""Shared helper: the live_metrics row for one module, as a dashboard sub-block.

Each ``get_*_dashboard`` calls this to attach the always-fresh live view beside
its (immutable-run-or-inline) detail, so a dashboard can show "live" numbers
without recomputing. Returns ``None`` when no refresh has run yet.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.models import LiveMetric
from app.schemas.live import LiveModuleView


def live_block(
    db: Session, ctx: TenantContext, bank_id: UUID, period_id: UUID, module: str
) -> LiveModuleView | None:
    row = db.scalar(
        select(LiveMetric).where(
            LiveMetric.organization_id == ctx.organization_id,
            LiveMetric.bank_id == bank_id,
            LiveMetric.reporting_period_id == period_id,
            LiveMetric.module == module,
        )
    )
    if row is None:
        return None
    return LiveModuleView(
        module=row.module,  # type: ignore[arg-type]
        status=row.status,  # type: ignore[arg-type]
        metrics=row.metrics,
        computed_at=row.computed_at,
        computed_from_input_hash=row.computed_from_input_hash,
    )
