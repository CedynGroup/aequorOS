from __future__ import annotations

from datetime import datetime
from decimal import Decimal
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
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy import text as sql_text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UuidV4PrimaryKeyMixin


class RegulatoryRun(UuidV4PrimaryKeyMixin, TimestampMixin, Base):
    """One immutable regulatory engine attempt for a bank reporting period.

    ``module`` selects the engine: ``liquidity``, ``capital``, ``forecast``
    (5-year balance-sheet projection), ``optimizer`` (constrained strategic
    search), ``whatif`` (single-shock forecast comparison), ``irr`` (interest
    rate risk in the banking book), ``fx`` (foreign-exchange risk), or ``ftp``
    (funds transfer pricing).
    """

    __tablename__ = "regulatory_runs"
    __table_args__ = (
        CheckConstraint(
            "module IN ('liquidity', 'capital', 'forecast', 'optimizer', 'whatif', "
            "'irr', 'fx', 'ftp')",
            name="ck_regulatory_runs_module",
        ),
        CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed')",
            name="ck_regulatory_runs_status",
        ),
        ForeignKeyConstraint(
            ["bank_id", "organization_id"],
            ["banks.id", "banks.organization_id"],
        ),
        ForeignKeyConstraint(
            ["reporting_period_id", "organization_id", "bank_id"],
            [
                "bank_reporting_periods.id",
                "bank_reporting_periods.organization_id",
                "bank_reporting_periods.bank_id",
            ],
        ),
        UniqueConstraint("id", "organization_id", "bank_id", name="uq_regulatory_runs_id_org_bank"),
        Index(
            "ix_regulatory_runs_org_bank_module_scenario",
            "organization_id",
            "bank_id",
            "module",
            "scenario_code",
        ),
        Index("ix_regulatory_runs_org_input_hash", "organization_id", "input_hash"),
    )

    organization_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    bank_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    reporting_period_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    module: Mapped[str] = mapped_column(String(16), nullable=False)
    scenario_code: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    engine_version: Mapped[str] = mapped_column(String(80), nullable=False)
    input_schema_version: Mapped[str] = mapped_column(String(40), nullable=False)
    output_schema_version: Mapped[str] = mapped_column(String(40), nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    inputs: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    metrics: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, server_default=sql_text("'{}'"), nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(120), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_details: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_by: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)


class RegulatoryMetricResult(UuidV4PrimaryKeyMixin, Base):
    """One headline metric produced by a successful regulatory run."""

    __tablename__ = "regulatory_metric_results"
    __table_args__ = (
        CheckConstraint(
            "unit IN ('pct', 'ghs', 'years')", name="ck_regulatory_metric_results_unit"
        ),
        CheckConstraint(
            "status IN ('green', 'amber', 'red', 'na')",
            name="ck_regulatory_metric_results_status",
        ),
        ForeignKeyConstraint(
            ["run_id", "organization_id", "bank_id"],
            [
                "regulatory_runs.id",
                "regulatory_runs.organization_id",
                "regulatory_runs.bank_id",
            ],
            ondelete="CASCADE",
        ),
        UniqueConstraint("run_id", "metric_code", name="uq_regulatory_metric_results_run_metric"),
        Index("ix_regulatory_metric_results_run_id", "run_id"),
    )

    organization_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    bank_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    run_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    metric_code: Mapped[str] = mapped_column(String(60), nullable=False)
    metric_value: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    unit: Mapped[str] = mapped_column(String(8), nullable=False)
    threshold_min: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    status: Mapped[str] = mapped_column(String(8), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)


class RegulatoryLineItem(UuidV4PrimaryKeyMixin, Base):
    """One weighted return line (exposure x rate) produced by a successful regulatory run."""

    __tablename__ = "regulatory_line_items"
    __table_args__ = (
        CheckConstraint(
            "section IN ('hqla', 'outflow', 'inflow', 'asf', 'rsf', 'credit_rwa', "
            "'market_rwa', 'operational_rwa', 'capital_component', 'ratio', "
            "'irr_gap', 'irr_eve', 'irr_ear', 'fx_position', 'fx_var', 'fx_hedge', "
            "'ftp_curve', 'ftp_product', 'ftp_branch')",
            name="ck_regulatory_line_items_section",
        ),
        ForeignKeyConstraint(
            ["run_id", "organization_id", "bank_id"],
            [
                "regulatory_runs.id",
                "regulatory_runs.organization_id",
                "regulatory_runs.bank_id",
            ],
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "run_id", "section", "line_code", name="uq_regulatory_line_items_run_section_line"
        ),
        Index("ix_regulatory_line_items_run_id", "run_id"),
    )

    organization_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    bank_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    run_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    section: Mapped[str] = mapped_column(String(24), nullable=False)
    line_code: Mapped[str] = mapped_column(String(60), nullable=False)
    description: Mapped[str] = mapped_column(String(255), nullable=False)
    exposure_amount: Mapped[Decimal | None] = mapped_column(Numeric(20, 4), nullable=True)
    rate_pct: Mapped[Decimal | None] = mapped_column(Numeric(9, 6), nullable=True)
    weighted_amount: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)


class RegulatoryValidation(UuidV4PrimaryKeyMixin, Base):
    """One named validation rule outcome recorded against a regulatory run."""

    __tablename__ = "regulatory_validations"
    __table_args__ = (
        CheckConstraint(
            "severity IN ('error', 'warning', 'info')",
            name="ck_regulatory_validations_severity",
        ),
        ForeignKeyConstraint(
            ["run_id", "organization_id", "bank_id"],
            [
                "regulatory_runs.id",
                "regulatory_runs.organization_id",
                "regulatory_runs.bank_id",
            ],
            ondelete="CASCADE",
        ),
        UniqueConstraint("run_id", "rule_code", name="uq_regulatory_validations_run_rule"),
        Index("ix_regulatory_validations_run_id", "run_id"),
    )

    organization_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    bank_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    run_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    rule_code: Mapped[str] = mapped_column(String(60), nullable=False)
    passed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    severity: Mapped[str] = mapped_column(String(8), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
