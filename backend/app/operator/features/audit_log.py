"""GET /operator/v1/audit — the operator's OWN cross-tenant action log.

This is DISTINCT from ``features/activity.py`` (a single tenant's unified feed).
It reads ``operator_audit_log`` — the append-only record of what AequorOS staff
did across tenants — so it is gated to ``operator_admin`` and above (audit is
sensitive). Reading the audit log is deliberately NOT itself audited: a page-view
would flood the very log it reads.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Query

from app.operator.deps import OperatorAdmin, OperatorDb
from app.operator.services import operator_views
from app.schemas.operator import OperatorAuditLogListRead

router = APIRouter(prefix="/audit", tags=["operator-audit"])


@router.get("", response_model=OperatorAuditLogListRead)
def read_audit_log(  # noqa: PLR0913 - one argument per query parameter
    db: OperatorDb,
    _operator: OperatorAdmin,
    operator_email: str | None = None,
    target_org: str | None = None,
    action: Annotated[str | None, Query(description="Prefix match on the action")] = None,
    from_: Annotated[datetime | None, Query(alias="from")] = None,
    to: datetime | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> OperatorAuditLogListRead:
    """Filtered, paginated, newest-first. ``action`` is a prefix match (``desk.``
    matches every desk action); ``total`` counts all rows matching the filters
    before ``limit``/``offset``."""
    return operator_views.read_operator_audit_log(
        db,
        operator_email=operator_email,
        target_org=target_org,
        action=action,
        from_ts=from_,
        to_ts=to,
        limit=limit,
        offset=offset,
    )
