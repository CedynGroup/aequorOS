"""Tenant Inspector enforcement: gate deep tenant reads on an ACTIVE session.

Opening an inspector session (``app.operator.features.inspector``) records WHO
is viewing WHICH tenant, WHY, and under which mode — but the row alone grants
nothing. This module is the teeth: every deep per-tenant read endpoint calls
``require_active_inspection`` first, so a look at a tenant's diagnostic data is
possible ONLY inside a session the CALLER opened, and every such look is then
written to ``operator_audit_log`` by the endpoint. No session → 403.

The fleet-metadata surfaces (``GET /tenants`` board, ``GET /tenants/{org}``
header) deliberately do NOT call this — they are the operator's directory of
tenants, not a look inside one.
"""

from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.base import utc_now
from app.models import OperatorInspectorSession
from app.operator.deps import OperatorContext

#: Typed 403 body (lands in the error envelope's ``details``): the console keys
#: on ``code`` to prompt the operator to start a session rather than treating it
#: as a generic authorization failure.
INSPECTION_REQUIRED_DETAIL: dict[str, str] = {
    "code": "inspection_required",
    "message": (
        "Start a Tenant Inspector session for this organization to view its data."
    ),
}


def require_active_inspection(
    session: Session, ctx: OperatorContext, org_id: str
) -> OperatorInspectorSession:
    """Return the caller's ACTIVE inspector session for ``org_id`` or raise 403.

    Active means: same ``organization_id``, opened by this operator
    (``started_by == ctx.email``), not ended (``ended_at IS NULL``), and not
    past its ``expires_at``. Break-glass sessions count exactly like consent
    ones — mode is not consulted here; the stricter role gate lives at session
    creation. An expired or ended session (or none at all) is a 403 carrying the
    typed ``inspection_required`` detail. ``org_id`` must already be normalized.

    Never leaks whether the org exists: without a session there can be no
    session for the org (start refuses unknown orgs with a 404), so an unknown
    org and a session-less known org are indistinguishable here — both 403.
    """
    now = utc_now()
    row = session.scalar(
        select(OperatorInspectorSession)
        .where(
            OperatorInspectorSession.organization_id == org_id,
            OperatorInspectorSession.started_by == ctx.email,
            OperatorInspectorSession.ended_at.is_(None),
            OperatorInspectorSession.expires_at > now,
        )
        .order_by(OperatorInspectorSession.started_at.desc())
    )
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=dict(INSPECTION_REQUIRED_DETAIL),
        )
    return row
