from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.models import AuditEvent


def record_event(  # noqa: PLR0913
    db: Session,
    ctx: TenantContext,
    *,
    event_type: str,
    entity_type: str,
    entity_id: UUID | str,
    details: dict[str, Any] | None = None,
) -> AuditEvent:
    merged_details: dict[str, Any] = dict(details or {})
    if ctx.impersonation_context is not None:
        # Best-effort provenance: an event produced under an operator
        # act-as-examiner session has no tenant actor (actor_user_id is None),
        # so record WHICH operator and WHICH inspector session stood behind it.
        # setdefault so an explicit caller value is never clobbered.
        merged_details.setdefault("act_operator", ctx.actor_operator)
        merged_details.setdefault("impersonation_session_id", ctx.impersonation_context)
    event = AuditEvent(
        organization_id=ctx.organization_id,
        actor_user_id=ctx.actor_user_id,
        event_type=event_type,
        entity_type=entity_type,
        entity_id=str(entity_id),
        details=merged_details,
    )
    db.add(event)
    return event
