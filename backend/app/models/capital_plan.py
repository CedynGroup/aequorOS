"""ICAAP capital-planning workflow (product.md §Phase 2 item 10).

Two RLS-forced tenant tables (migration 202608070038):

- ``capital_plans`` — versioned capital-plan documents: the Pillar-2 add-on
  register, management actions and the trigger framework as a validated JSON
  block, with the Board-approval trail (annual, like the ICAAP submission the
  BoG guideline mandates). The multi-year ratio projection is ASSEMBLED at
  read time from stored forecast runs — never stored, never stale.
- ``ilaap_snapshots`` — the LRMD ¶12/¶24/¶26 addendum: the ILAAP outcome is a
  quarterly-refreshable component, not an annual monolith. Each refresh is an
  append-only snapshot of the liquidity-adequacy evidence (stored runs,
  Board thresholds, CFP posture) for one reporting period.
"""

from __future__ import annotations

from datetime import date, datetime
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
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UuidV4PrimaryKeyMixin

CAPITAL_PLAN_STATUSES = ("draft", "approved", "superseded")


class CapitalPlan(UuidV4PrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "capital_plans"
    __table_args__ = (
        CheckConstraint(
            f"status IN {CAPITAL_PLAN_STATUSES!r}", name="ck_capital_plans_status"
        ),
        UniqueConstraint(
            "organization_id", "bank_id", "version", name="uq_capital_plans_version"
        ),
        Index("ix_capital_plans_bank", "organization_id", "bank_id"),
    )

    organization_id: Mapped[str] = mapped_column(
        String(16), ForeignKey("organizations.id"), nullable=False
    )
    bank_id: Mapped[str] = mapped_column(String(16), ForeignKey("banks.id"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="draft")
    # Shape-validated by ``schemas.capital_plan.CapitalPlanContent``.
    content: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    prepared_by: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    approved_by_user_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    approval_reference: Mapped[str | None] = mapped_column(String(200), nullable=True)
    approval_timestamp: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # ICAAP is an annual Board submission: re-approval falls due here.
    approval_expires_at: Mapped[date | None] = mapped_column(Date, nullable=True)


class IlaapSnapshot(UuidV4PrimaryKeyMixin, TimestampMixin, Base):
    """Append-only quarterly ILAAP component (LRMD ¶12/¶24/¶26)."""

    __tablename__ = "ilaap_snapshots"
    __table_args__ = (Index("ix_ilaap_snapshots_bank", "organization_id", "bank_id"),)

    organization_id: Mapped[str] = mapped_column(
        String(16), ForeignKey("organizations.id"), nullable=False
    )
    bank_id: Mapped[str] = mapped_column(String(16), ForeignKey("banks.id"), nullable=False)
    reporting_period_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    as_of_date: Mapped[date] = mapped_column(Date, nullable=False)
    # Assembled liquidity-adequacy evidence — stored runs, Board thresholds,
    # CFP posture, EWI escalation — at refresh time.
    content: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    adequate: Mapped[bool] = mapped_column(Boolean, nullable=False)
    created_by: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
