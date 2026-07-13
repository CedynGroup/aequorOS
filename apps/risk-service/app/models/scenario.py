from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy import text as sql_text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UuidV4PrimaryKeyMixin, utc_now


class RiskScenario(UuidV4PrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "risk_scenarios"
    __table_args__ = (
        CheckConstraint(
            "scenario_type IN ('baseline', 'downside', 'custom')",
            name="ck_risk_scenarios_type",
        ),
        Index("ix_risk_scenarios_case_id", "case_id"),
        Index(
            "uq_risk_scenarios_active_default",
            "organization_id",
            "case_id",
            "scenario_type",
            unique=True,
            sqlite_where=sql_text("archived_at IS NULL AND scenario_type != 'custom'"),
            postgresql_where=sql_text("archived_at IS NULL AND scenario_type != 'custom'"),
        ),
        ForeignKeyConstraint(
            ["case_id", "organization_id"],
            ["risk_cases.id", "risk_cases.organization_id"],
        ),
        UniqueConstraint(
            "id", "organization_id", "case_id", name="uq_risk_scenarios_id_organization_id_case_id"
        ),
    )

    organization_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    case_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    scenario_type: Mapped[str] = mapped_column(String(40), nullable=False)
    copied_from_scenario_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    created_by: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ScenarioAssumption(UuidV4PrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "scenario_assumptions"
    __table_args__ = (
        CheckConstraint(
            "category IN ('growth', 'expenses', 'cash_flow_timing', 'credit_usage', "
            "'repayment_behavior', 'other')",
            name="ck_scenario_assumptions_category",
        ),
        CheckConstraint(
            "review_status IN ('draft', 'reviewed')",
            name="ck_scenario_assumptions_review_status",
        ),
        Index("ix_scenario_assumptions_scenario_id", "scenario_id"),
        Index(
            "uq_scenario_assumptions_key",
            "organization_id",
            "scenario_id",
            "key",
            unique=True,
        ),
        ForeignKeyConstraint(
            ["scenario_id", "organization_id", "case_id"],
            ["risk_scenarios.id", "risk_scenarios.organization_id", "risk_scenarios.case_id"],
            ondelete="CASCADE",
        ),
    )

    organization_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    case_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    scenario_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    category: Mapped[str] = mapped_column(String(40), nullable=False)
    key: Mapped[str] = mapped_column(String(120), nullable=False)
    label: Mapped[str] = mapped_column(String(160), nullable=False)
    value: Mapped[Any] = mapped_column(JSON, nullable=False)
    unit: Mapped[str | None] = mapped_column(String(40), nullable=True)
    provenance: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, server_default=sql_text("'{}'"), nullable=False
    )
    review_status: Mapped[str] = mapped_column(
        String(40), default="draft", server_default=sql_text("'draft'"), nullable=False
    )
    reviewed_by: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ScenarioAssumptionHistory(UuidV4PrimaryKeyMixin, Base):
    __tablename__ = "scenario_assumption_history"
    __table_args__ = (Index("ix_scenario_assumption_history_assumption_id", "assumption_id"),)

    organization_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    case_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    scenario_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    assumption_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    action: Mapped[str] = mapped_column(String(40), nullable=False)
    changed_fields: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    changed_by: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
