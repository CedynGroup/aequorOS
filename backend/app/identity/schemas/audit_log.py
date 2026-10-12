"""Bank-visible audit evidence, with explicit verification limits."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, JsonValue


class AuditEventRead(BaseModel):
    id: UUID
    stream: Literal["audit_events", "operator_audit_log"]
    actor: str | None
    actor_kind: Literal["bank_user", "aequoros_staff", "system"]
    action: str
    target_type: str | None
    target_id: str | None
    occurred_at: datetime
    source: str | None
    outcome: str
    before: JsonValue = None
    after: JsonValue = None
    reason: str | None = None
    support_reference: str | None = None
    details: dict[str, JsonValue]
    sequence: int | None = None
    integrity: Literal["verified", "failed", "unavailable"] = "unavailable"
    integrity_scope: Literal["event_payload_and_previous_link"] = "event_payload_and_previous_link"


class AuditLogRead(BaseModel):
    events: list[AuditEventRead]
    next_cursor: str | None = None
