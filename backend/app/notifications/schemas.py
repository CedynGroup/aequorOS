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
    # Post-platform-ID-epoch this can be a BK-/OR- code, pre-epoch rows keep
    # UUID text — the column is plain String(40), so the schema must be too.
    entity_id: str | None
    recipient_user_id: UUID | None
    # The catalogue event and the bank-app link the notice opens (both NULL
    # on rows written before the notification platform).
    event_key: str | None = None
    action_path: str | None = None
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


# ---------------------------------------------------------------------------
# Preferences, organisation settings and the delivery log
# ---------------------------------------------------------------------------

type EmailMode = Literal["immediate", "digest", "off"]
type LockedBy = Literal["platform", "organisation"]
type DeliveryStatus = Literal["pending", "sent", "dead", "suppressed"]


class NotificationEventPreferenceRead(ClosedModel):
    """One event as the preferences screen shows it to the signed-in person."""

    key: str
    label: str
    description: str
    default_email: EmailMode
    #: What actually happens for this person now, after every rule.
    email_mode: EmailMode
    #: The person's own saved choice, or ``None`` when they follow the default.
    choice: EmailMode | None
    #: Who fixed this event's email behaviour, when somebody did.
    locked_by: LockedBy | None


class NotificationCategoryRead(ClosedModel):
    key: str
    label: str
    events: list[NotificationEventPreferenceRead]


class NotificationPreferencesRead(ClosedModel):
    #: Whether this deployment can send email at all (an SMTP relay is set).
    email_configured: bool
    #: Whether the organisation has email switched on.
    email_enabled: bool
    digest_hour: int
    time_zone: str
    categories: list[NotificationCategoryRead]


class NotificationPreferencesUpdate(ClosedModel):
    #: Event key to the chosen mode; ``None`` returns the event to the default.
    choices: dict[str, EmailMode | None]


class NotificationPolicyRead(ClosedModel):
    key: str
    label: str
    description: str
    category: str
    default_email: EmailMode
    platform_locked: bool
    #: The organisation's rule, or ``None`` when it follows the platform default.
    email_mode: EmailMode | None
    locked: bool


class NotificationPolicyUpdate(ClosedModel):
    email_mode: EmailMode
    locked: bool


class NotificationOrgSettingsRead(ClosedModel):
    email_configured: bool
    email_enabled: bool
    time_zone: str
    digest_hour: int
    reminders_enabled: bool
    reminder_after_hours: int
    escalate_after_hours: int
    policies: list[NotificationPolicyRead]


class NotificationOrgSettingsUpdate(ClosedModel):
    email_enabled: bool
    time_zone: str
    digest_hour: int
    reminders_enabled: bool
    reminder_after_hours: int
    escalate_after_hours: int
    #: Event key to its rule; ``None`` removes the rule. Events left out are unchanged.
    policies: dict[str, NotificationPolicyUpdate | None] = {}


class NotificationDeliveryRead(ClosedModel):
    id: UUID
    event_key: str
    event_label: str
    #: The notice's title when the administrator may see it, otherwise ``None``.
    title: str | None
    recipient_user_id: UUID
    recipient_name: str
    address: str
    mode: Literal["immediate", "digest"]
    status: DeliveryStatus
    attempts: int
    last_error: str | None
    next_attempt_at: datetime
    sent_at: datetime | None
    created_at: datetime


class NotificationDeliveryListRead(ClosedModel):
    deliveries: list[NotificationDeliveryRead]
    total: int
    counts: dict[str, int]
    limit: int
    offset: int
    has_more: bool
