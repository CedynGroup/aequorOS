"""In-app notification feed (docs/submission_pipeline_plan.md §W3).

One row per recipient: ``recipient_user_id`` NULL means org-wide (visible to
every user in the tenant). ``type`` is a namespaced code (``reporting.*``);
scheduled deadline notifications embed their deterministic dedupe key in it
(e.g. ``reporting.deadline.due_soon_7:BSD3:2026-03-31``) so a re-scan the same
day emits nothing new. Rows are append-only apart from ``read_at``.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.authorization import InstitutionScope, Module, Sensitivity
from app.db.base import Base, UuidV7PrimaryKeyMixin, utc_now

NOTIFICATION_SEVERITIES = ("info", "warning", "critical")


def _values(options: tuple[str, ...]) -> str:
    return ", ".join(f"'{option}'" for option in options)


class Notification(UuidV7PrimaryKeyMixin, Base):
    """One in-app notification for a user (or the whole organization)."""

    __tablename__ = "notifications"
    __table_args__ = (
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
    # Set once the SMTP mirror has delivered this row (outbox semantics);
    # stays NULL forever when the mirror is disabled.
    emailed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
