from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import String, select, text
from sqlalchemy import cast as sql_cast
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.core.audit_attribution import SUBJECT_TABLES, subject_bank_sql
from app.core.audit_projection import normalize_event_details
from app.models import AuditEvent


def record_event(  # noqa: PLR0913
    db: Session,
    ctx: TenantContext,
    *,
    event_type: str,
    entity_type: str,
    entity_id: UUID | str,
    details: dict[str, Any] | None = None,
    bank_id: str | None = None,
) -> AuditEvent:
    merged_details: dict[str, Any] = normalize_event_details(event_type, dict(details or {}))
    if bank_id is not None:
        merged_details.setdefault("bank_id", bank_id)
    if entity_type == "authorization_access_request" and isinstance(
        merged_details.get("institution_id"), str
    ):
        merged_details.setdefault("bank_id", merged_details["institution_id"])
    if entity_type in SUBJECT_TABLES:
        db.flush()
        bank_id = db.scalar(
            select(
                sql_cast(
                    text(
                        subject_bank_sql(
                            ":org",
                            ":kind",
                            ":id",
                            postgresql=db.get_bind().dialect.name == "postgresql",
                            subject=entity_type,
                        )
                    ),
                    String,
                )
            ),
            {"org": ctx.organization_id, "kind": entity_type, "id": str(entity_id)},
        )
        if bank_id is not None:
            merged_details.setdefault("bank_id", bank_id)
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
