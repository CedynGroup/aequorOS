from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy import text as sql_text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UuidV4PrimaryKeyMixin


class CalculationRun(UuidV4PrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "calculation_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed')",
            name="ck_calculation_runs_status",
        ),
        CheckConstraint("forecast_periods BETWEEN 1 AND 12", name="ck_calculation_runs_periods"),
        Index("ix_calculation_runs_case_scenario", "case_id", "scenario_id"),
        Index("ix_calculation_runs_input_hash", "organization_id", "input_hash"),
        ForeignKeyConstraint(
            ["case_id", "organization_id"],
            ["risk_cases.id", "risk_cases.organization_id"],
        ),
        ForeignKeyConstraint(
            ["scenario_id", "organization_id", "case_id"],
            ["risk_scenarios.id", "risk_scenarios.organization_id", "risk_scenarios.case_id"],
        ),
        ForeignKeyConstraint(
            ["rerun_of_run_id", "organization_id", "case_id"],
            ["calculation_runs.id", "calculation_runs.organization_id", "calculation_runs.case_id"],
        ),
        UniqueConstraint(
            "id", "organization_id", "case_id", name="uq_calculation_runs_id_org_case"
        ),
    )

    organization_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    case_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    scenario_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    rerun_of_run_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    engine_version: Mapped[str] = mapped_column(String(80), nullable=False)
    input_schema_version: Mapped[str] = mapped_column(String(40), nullable=False)
    output_schema_version: Mapped[str] = mapped_column(String(40), nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    inputs: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    forecast_periods: Mapped[int] = mapped_column(Integer, nullable=False)
    as_of_date: Mapped[date] = mapped_column(Date, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(120), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_details: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_by: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)


class CalculationForecastPeriod(UuidV4PrimaryKeyMixin, Base):
    __tablename__ = "calculation_forecast_periods"
    __table_args__ = (
        CheckConstraint("period_number > 0", name="ck_calculation_forecast_periods_number"),
        Index("ix_calculation_forecast_periods_run_id", "run_id"),
        UniqueConstraint("run_id", "period_number", name="uq_calculation_forecast_run_period"),
        ForeignKeyConstraint(
            ["run_id", "organization_id", "case_id"],
            ["calculation_runs.id", "calculation_runs.organization_id", "calculation_runs.case_id"],
            ondelete="CASCADE",
        ),
    )

    organization_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    case_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    run_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    period_number: Mapped[int] = mapped_column(Integer, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    total_assets: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    total_liabilities: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    total_equity: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    cash: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    projected_inflows: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    projected_outflows: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    credit_draw: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    debt_repayment: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    components: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, server_default=sql_text("'{}'"), nullable=False
    )


class LiquidityAnalysisResult(UuidV4PrimaryKeyMixin, Base):
    __tablename__ = "liquidity_analysis_results"
    __table_args__ = (
        Index("ix_liquidity_analysis_results_case_id", "case_id"),
        ForeignKeyConstraint(
            ["run_id", "organization_id", "case_id"],
            ["calculation_runs.id", "calculation_runs.organization_id", "calculation_runs.case_id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("run_id", name="uq_liquidity_analysis_results_run_id"),
    )

    organization_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    case_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    run_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    analysis_version: Mapped[str] = mapped_column(String(80), nullable=False)
    result: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
