"""EWI framework + Contingency Funding Plan (LRMD 2026 ¶28(e)–(f), ¶70–77).

Three tenant-scoped (RLS-forced) tables, migration 202608070036:

- ``liquidity_ewi_indicators`` — the bank's early-warning indicator register.
  The eight BoG-named starter indicators (¶28(f)) are code-defined defaults
  (``app/services/liquidity_ewi.STARTER_INDICATORS``); a DB row overrides a
  starter's thresholds/enabled state or adds a bank-specific indicator.
  ``approved_by``/``approval_timestamp`` are the Board evidence for the
  trigger levels, mirroring the threshold-register convention.
- ``contingency_funding_plans`` — versioned CFP documents carrying the
  ¶72(a)–(g) minimum contents as a validated JSON block. Board approval is
  annual (¶71): ``approval_expires_at`` marks when re-approval falls due.
- ``cfp_activation_events`` — the append-only activation / de-escalation
  log (¶74), each event carrying the EWI evaluation snapshot at event time
  and the regulator-notification evidence.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UuidV4PrimaryKeyMixin

CFP_STATUSES = ("draft", "approved", "superseded")
CFP_EVENT_TYPES = ("activated", "de_escalated")
EWI_DIRECTIONS = ("above", "below")


class LiquidityEwiIndicator(UuidV4PrimaryKeyMixin, TimestampMixin, Base):
    """One row of the bank's EWI register (override of a starter, or custom)."""

    __tablename__ = "liquidity_ewi_indicators"
    __table_args__ = (
        CheckConstraint(
            f"direction IS NULL OR direction IN {EWI_DIRECTIONS!r}",
            name="ck_liquidity_ewi_indicators_direction",
        ),
        UniqueConstraint(
            "organization_id", "bank_id", "code", name="uq_liquidity_ewi_indicators_scope"
        ),
        Index("ix_liquidity_ewi_indicators_bank", "organization_id", "bank_id"),
    )

    organization_id: Mapped[str] = mapped_column(
        String(16), ForeignKey("organizations.id"), nullable=False
    )
    bank_id: Mapped[str] = mapped_column(String(16), ForeignKey("banks.id"), nullable=False)
    code: Mapped[str] = mapped_column(String(60), nullable=False)
    # Display fields are nullable: a starter override inherits the code-defined
    # name/description; a custom indicator must supply its own (API-enforced).
    name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # LRMD ¶28(e): each indicator names the recovery-plan indicator it aligns
    # with — carried as data, never invented.
    recovery_plan_reference: Mapped[str | None] = mapped_column(String(200), nullable=True)
    watch_threshold: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    action_threshold: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    # Direction/unit are code-defined for starters (NULL here); custom
    # indicators carry their own.
    direction: Mapped[str | None] = mapped_column(String(8), nullable=True)
    unit: Mapped[str | None] = mapped_column(String(8), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    custom: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    approved_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    approval_timestamp: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class ContingencyFundingPlan(UuidV4PrimaryKeyMixin, TimestampMixin, Base):
    """One CFP version; the current plan is the highest approved version."""

    __tablename__ = "contingency_funding_plans"
    __table_args__ = (
        CheckConstraint(f"status IN {CFP_STATUSES!r}", name="ck_contingency_funding_plans_status"),
        UniqueConstraint(
            "organization_id", "bank_id", "version", name="uq_contingency_funding_plans_version"
        ),
        Index("ix_contingency_funding_plans_bank", "organization_id", "bank_id"),
    )

    organization_id: Mapped[str] = mapped_column(
        String(16), ForeignKey("organizations.id"), nullable=False
    )
    bank_id: Mapped[str] = mapped_column(String(16), ForeignKey("banks.id"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="draft")
    # The ¶72(a)–(g) blocks, shape-validated by ``schemas.liquidity_cfp.CfpContent``.
    content: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    prepared_by: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    approved_by_user_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    # Board evidence (¶71): who/what minute, when, and when annual re-approval
    # falls due.
    approval_reference: Mapped[str | None] = mapped_column(String(200), nullable=True)
    approval_timestamp: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    approval_expires_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    # Convenience mirror of the event log: True between an ``activated`` event
    # and its ``de_escalated`` counterpart.
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class CfpActivationEvent(UuidV4PrimaryKeyMixin, TimestampMixin, Base):
    """Append-only ¶74 log: activation / de-escalation with EWI evidence."""

    __tablename__ = "cfp_activation_events"
    __table_args__ = (
        CheckConstraint(
            f"event_type IN {CFP_EVENT_TYPES!r}", name="ck_cfp_activation_events_type"
        ),
        Index("ix_cfp_activation_events_bank", "organization_id", "bank_id"),
    )

    organization_id: Mapped[str] = mapped_column(
        String(16), ForeignKey("organizations.id"), nullable=False
    )
    bank_id: Mapped[str] = mapped_column(String(16), ForeignKey("banks.id"), nullable=False)
    cfp_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("contingency_funding_plans.id"), nullable=False
    )
    event_type: Mapped[str] = mapped_column(String(16), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    # The evaluated indicator states at event time — the examiner's "what did
    # you see when you pulled the trigger".
    ewi_snapshot: Mapped[list[Any]] = mapped_column(JSON, nullable=False)
    # ¶71 discipline surfaced, not hidden: activation on an expired Board
    # approval is allowed (a crisis does not wait for a minute) but flagged.
    approval_overdue: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Evidence that the ¶74 regulator-notification obligation entered the
    # notification pipeline (first minted Notification row).
    regulator_notification_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), nullable=True
    )
    created_by: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
