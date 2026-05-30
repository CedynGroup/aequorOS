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
    entity_id: UUID,
    details: dict[str, Any] | None = None,
) -> AuditEvent:
    event = AuditEvent(
        organization_id=ctx.organization_id,
        actor_user_id=ctx.actor_user_id,
        event_type=event_type,
        entity_type=entity_type,
        entity_id=entity_id,
        details=details or {},
    )
    db.add(event)
    return event
