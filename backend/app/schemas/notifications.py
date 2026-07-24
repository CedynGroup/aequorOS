from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

type NotificationSeverity = Literal["info", "warning", "critical"]


class ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NotificationRead(ClosedModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    id: UUID
    type: str
    severity: NotificationSeverity
    title: str
    body: str
    entity_type: str | None
    entity_id: UUID | None
    recipient_user_id: UUID | None
    read_at: datetime | None
    created_at: datetime


class NotificationListRead(ClosedModel):
    notifications: list[NotificationRead]
    total: int
    unread_count: int
    limit: int
    offset: int
    has_more: bool


class NotificationsMarkAllRead(ClosedModel):
    marked: int
