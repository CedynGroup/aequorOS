from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy import text as sql_text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UuidV4PrimaryKeyMixin


class CapitalProjection(UuidV4PrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "capital_projections"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed')",
            name="ck_capital_projections_status",
        ),
        Index("ix_capital_projections_case_scenario", "organization_id", "case_id", "scenario_id"),
        ForeignKeyConstraint(
            ["calculation_run_id", "organization_id", "case_id"],
            ["calculation_runs.id", "calculation_runs.organization_id", "calculation_runs.case_id"],
        ),
        ForeignKeyConstraint(
            ["scenario_id", "organization_id", "case_id"],
            ["risk_scenarios.id", "risk_scenarios.organization_id", "risk_scenarios.case_id"],
        ),
        UniqueConstraint(
            "id", "organization_id", "case_id", name="uq_capital_projections_id_org_case"
        ),
    )

    organization_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    case_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    scenario_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    calculation_run_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    engine_version: Mapped[str] = mapped_column(String(80), nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    reporting_currency: Mapped[str] = mapped_column(String(3), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_by: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)


class CapitalIndicator(UuidV4PrimaryKeyMixin, Base):
    __tablename__ = "capital_indicators"
    __table_args__ = (
        CheckConstraint("period_number > 0", name="ck_capital_indicators_period_number"),
        CheckConstraint(
            "pressure_level IN ('low', 'medium', 'high', 'critical')",
            name="ck_capital_indicators_pressure_level",
        ),
        Index("ix_capital_indicators_projection", "projection_id", "period_number"),
        ForeignKeyConstraint(
            ["projection_id", "organization_id", "case_id"],
            [
                "capital_projections.id",
                "capital_projections.organization_id",
                "capital_projections.case_id",
            ],
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "projection_id", "period_number", name="uq_capital_indicator_projection_period"
        ),
    )

    organization_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    case_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    projection_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    forecast_period_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    period_number: Mapped[int] = mapped_column(Integer, nullable=False)
    equity: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    equity_to_assets_ratio: Mapped[Decimal] = mapped_column(Numeric(12, 8), nullable=False)
    liabilities_to_assets_ratio: Mapped[Decimal] = mapped_column(Numeric(12, 8), nullable=False)
    equity_change: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    pressure_level: Mapped[str] = mapped_column(String(24), nullable=False)
    evidence: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, server_default=sql_text("'{}'"), nullable=False
    )


class CapitalProjectionFinding(UuidV4PrimaryKeyMixin, Base):
    __tablename__ = "capital_projection_findings"
    __table_args__ = (
        ForeignKeyConstraint(
            ["projection_id", "organization_id", "case_id"],
            [
                "capital_projections.id",
                "capital_projections.organization_id",
                "capital_projections.case_id",
            ],
            ondelete="CASCADE",
        ),
        UniqueConstraint("projection_id", "finding_id", name="uq_capital_projection_finding"),
        Index("ix_capital_projection_findings_projection", "projection_id"),
    )

    organization_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    case_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    projection_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    finding_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("risk_findings.id", ondelete="CASCADE"), nullable=False
    )
