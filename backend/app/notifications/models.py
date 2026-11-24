"""Notification platform tables (backend/docs/notifications.md).

``notifications`` is the in-app feed and the message of record;
``notification_deliveries`` is the per-recipient, per-channel outbox and
delivery log; preferences, policies and settings decide what reaches email.

Original feed notes (docs/submission_pipeline_plan.md §W3):

One row per recipient: ``recipient_user_id`` NULL means org-wide (visible to
every user in the tenant). ``type`` is a namespaced code (``reporting.*``);
scheduled deadline notifications embed their deterministic dedupe key in it
(e.g. ``reporting.deadline.due_soon_7:BSD3:2026-03-31``) so a re-scan the same
day emits nothing new. Rows are append-only apart from ``read_at``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy import text as sql_text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.authorization import InstitutionScope, Module, Sensitivity
from app.db.base import Base, TableArgs, TimestampMixin, UuidV7PrimaryKeyMixin, utc_now

NOTIFICATION_SEVERITIES = ("info", "warning", "critical")


def _values(options: tuple[str, ...]) -> str:
    return ", ".join(f"'{option}'" for option in options)


class Notification(UuidV7PrimaryKeyMixin, Base):
    """One in-app notification for a user (or the whole organization)."""

    __tablename__ = "notifications"
    __table_args__: TableArgs = (
        CheckConstraint(
            f"severity IN ({_values(NOTIFICATION_SEVERITIES)})",
            name="ck_notifications_severity",
        ),
        # Typed source locator (#186): the producing service persists the
        # module context at write time. Legacy rows carry all-NULL locators
        # and are hidden at every tenant read path — never inferred or
        # backfilled from display text (mission D9).
        CheckConstraint(
            f"institution_scope IN ({_values(tuple(InstitutionScope))})",
            name="ck_notifications_institution_scope",
        ),
        CheckConstraint(
            f"module IN ({_values(tuple(Module))})",
            name="ck_notifications_module",
        ),
        CheckConstraint(
            f"sensitivity IN ({_values(tuple(Sensitivity))})",
            name="ck_notifications_sensitivity",
        ),
        # Composite org FK: a targeted notification can only reference a user
        # of the same tenant (NULL recipient = org-wide, FK not enforced).
        ForeignKeyConstraint(
            ["recipient_user_id", "organization_id"],
            ["users.id", "users.organization_id"],
        ),
        UniqueConstraint("id", "organization_id", name="uq_notifications_id_org"),
        Index(
            "ix_notifications_org_recipient_read",
            "organization_id",
            "recipient_user_id",
            "read_at",
        ),
        Index("ix_notifications_org_created", "organization_id", "created_at"),
        Index(
            "ix_notifications_org_recipient_read_locator",
            "organization_id",
            "recipient_user_id",
            "read_at",
            "module",
            "sensitivity",
            "bank_id",
        ),
    )

    organization_id: Mapped[str] = mapped_column(String(16), nullable=False)
    recipient_user_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    # Long enough for the deadline-scan dedupe keys (namespace + return_code +
    # reporting_date + scan date); same width as audit_events.event_type.
    type: Mapped[str] = mapped_column(String(120), nullable=False)
    severity: Mapped[str] = mapped_column(String(12), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    entity_type: Mapped[str | None] = mapped_column(String(40), nullable=True)
    # Polymorphic reference: UUID for most entities, platform ID for banks/orgs.
    entity_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    # Typed source locator (#186), persisted at write time by the emitter.
    # All four are NULL on legacy rows, which read paths treat as hidden.
    institution_scope: Mapped[str | None] = mapped_column(String(16), nullable=True)
    bank_id: Mapped[str | None] = mapped_column(String(16), nullable=True)
    module: Mapped[str | None] = mapped_column(String(32), nullable=True)
    sensitivity: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # The catalogue event (app/notifications/catalog.py). NULL on rows written
    # before the catalogue; readers resolve those through ``catalog.for_type``.
    event_key: Mapped[str | None] = mapped_column(String(80), nullable=True)
    # The structured message the email and the feed render: facts, a quoted
    # comment, the call to action. Display data only — never a figure of record.
    data: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, server_default=sql_text("'{}'"), nullable=False
    )
    # Bank-app path the "Open in AequorOS" button and the feed link to.
    action_path: Mapped[str | None] = mapped_column(String(400), nullable=True)
    # Legacy SMTP-mirror stamp. Deliveries are now tracked per recipient in
    # ``notification_deliveries``; kept so pre-platform rows stay readable.
    emailed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


NOTIFICATION_CHANNELS = ("email",)
DELIVERY_MODES = ("immediate", "digest")
#: ``pending`` waits for ``next_attempt_at`` (a retry keeps it pending with the
#: last error recorded); ``sent`` reached the relay; ``dead`` exhausted its
#: attempts or was refused permanently; ``suppressed`` was deliberately not
#: sent (recipient deactivated, lost access, email switched off, allowlist).
DELIVERY_STATUSES = ("pending", "sent", "dead", "suppressed")
EMAIL_MODES = ("immediate", "digest", "off")


class NotificationDelivery(UuidV7PrimaryKeyMixin, TimestampMixin, Base):
    """One notification on its way to one person through one channel.

    The outbox and the delivery log are the same rows: an administrator reads
    exactly what the dispatcher acted on. Written in the business transaction
    that published the notification, so a rolled-back action never mails.
    """

    __tablename__ = "notification_deliveries"
    __table_args__: TableArgs = (
        CheckConstraint(
            f"channel IN ({_values(NOTIFICATION_CHANNELS)})",
            name="ck_notification_deliveries_channel",
        ),
        CheckConstraint(
            f"mode IN ({_values(DELIVERY_MODES)})", name="ck_notification_deliveries_mode"
        ),
        CheckConstraint(
            f"status IN ({_values(DELIVERY_STATUSES)})",
            name="ck_notification_deliveries_status",
        ),
        CheckConstraint("attempts >= 0", name="ck_notification_deliveries_attempts"),
        ForeignKeyConstraint(
            ["notification_id", "organization_id"],
            ["notifications.id", "notifications.organization_id"],
        ),
        ForeignKeyConstraint(
            ["recipient_user_id", "organization_id"],
            ["users.id", "users.organization_id"],
        ),
        UniqueConstraint(
            "notification_id",
            "recipient_user_id",
            "channel",
            name="uq_notification_deliveries_once",
        ),
        Index(
            "ix_notification_deliveries_due",
            "organization_id",
            "next_attempt_at",
            postgresql_where=sql_text("status = 'pending'"),
            sqlite_where=sql_text("status = 'pending'"),
        ),
        Index("ix_notification_deliveries_org_created", "organization_id", "created_at"),
    )

    organization_id: Mapped[str] = mapped_column(String(16), nullable=False)
    notification_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    recipient_user_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    channel: Mapped[str] = mapped_column(String(16), nullable=False)
    # The address as resolved when published; re-checked against the live user
    # row at send time, so a changed or deactivated account is honoured.
    address: Mapped[str] = mapped_column(String(320), nullable=False)
    mode: Mapped[str] = mapped_column(String(12), nullable=False)
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="pending")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Shared by every delivery one digest email carried.
    digest_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)


class NotificationPreference(UuidV7PrimaryKeyMixin, TimestampMixin, Base):
    """One person's choice for one event: email it now, in the daily digest, or not."""

    __tablename__ = "notification_preferences"
    __table_args__: TableArgs = (
        CheckConstraint(
            f"email_mode IN ({_values(EMAIL_MODES)})",
            name="ck_notification_preferences_email_mode",
        ),
        ForeignKeyConstraint(
            ["user_id", "organization_id"],
            ["users.id", "users.organization_id"],
        ),
        UniqueConstraint(
            "organization_id", "user_id", "event_key", name="uq_notification_preferences_event"
        ),
    )

    organization_id: Mapped[str] = mapped_column(String(16), nullable=False)
    user_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    event_key: Mapped[str] = mapped_column(String(80), nullable=False)
    email_mode: Mapped[str] = mapped_column(String(12), nullable=False)


class NotificationPolicy(UuidV7PrimaryKeyMixin, TimestampMixin, Base):
    """The organisation's rule for one event, set by an administrator.

    Without ``locked`` the mode is the organisation's default and each person
    may still choose; with ``locked`` everyone in the organisation gets exactly
    this mode. Events the catalogue locks ignore policy entirely.
    """

    __tablename__ = "notification_policies"
    __table_args__: TableArgs = (
        CheckConstraint(
            f"email_mode IN ({_values(EMAIL_MODES)})",
            name="ck_notification_policies_email_mode",
        ),
        ForeignKeyConstraint(
            ["updated_by", "organization_id"],
            ["users.id", "users.organization_id"],
        ),
        UniqueConstraint("organization_id", "event_key", name="uq_notification_policies_event"),
    )

    organization_id: Mapped[str] = mapped_column(String(16), nullable=False)
    event_key: Mapped[str] = mapped_column(String(80), nullable=False)
    email_mode: Mapped[str] = mapped_column(String(12), nullable=False)
    locked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    updated_by: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)


class NotificationSettings(TimestampMixin, Base):
    """The organisation's delivery settings: one row per organisation, defaults when absent."""

    __tablename__ = "notification_settings"
    __table_args__: TableArgs = (
        CheckConstraint(
            "digest_hour >= 0 AND digest_hour <= 23", name="ck_notification_settings_digest_hour"
        ),
        CheckConstraint(
            "reminder_after_hours > 0 AND escalate_after_hours > reminder_after_hours",
            name="ck_notification_settings_escalation_window",
        ),
        ForeignKeyConstraint(
            ["updated_by", "organization_id"],
            ["users.id", "users.organization_id"],
        ),
    )

    organization_id: Mapped[str] = mapped_column(String(16), primary_key=True)
    email_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    #: IANA zone the digest hour is read in. Tenant data, never a code default
    #: beyond UTC: the platform does not assume a country.
    time_zone: Mapped[str] = mapped_column(String(64), nullable=False, default="UTC")
    digest_hour: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=7)
    reminders_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    reminder_after_hours: Mapped[int] = mapped_column(Integer, nullable=False, default=24)
    escalate_after_hours: Mapped[int] = mapped_column(Integer, nullable=False, default=48)
    updated_by: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
