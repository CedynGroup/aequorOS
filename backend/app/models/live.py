from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import (
    Boolean,
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
from sqlalchemy.orm import Mapped, mapped_column, synonym

from app.db.base import Base, TimestampMixin, UuidV4PrimaryKeyMixin

# The live Treasury/ALM surfaces. Rating is a cheap live scorecard over the
# canonical book, current market data, and the other live module outputs.
LIVE_MODULES = ("liquidity", "capital", "irr", "fx", "ftp", "rating", "forecast")
_MODULE_CHECK = "module IN ('liquidity', 'capital', 'irr', 'fx', 'ftp', 'rating', 'forecast')"


class LiveMetric(UuidV4PrimaryKeyMixin, TimestampMixin, Base):
    """The always-fresh per-module baseline view for a bank.

    Upserted on every pipeline refresh (unique on org/bank/module), so a read
    is a single cheap lookup and never runs the engines. ``source_fact_period``
    is migration-era provenance for the temporary fact materialisation only;
    it is not an identity or a reporting requirement. Distinct from immutable
    ``regulatory_runs`` used for filing.
    """

    __tablename__ = "live_metrics"
    __table_args__ = (
        CheckConstraint(_MODULE_CHECK, name="ck_live_metrics_module"),
        CheckConstraint(
            "status IN ('green', 'amber', 'red', 'na')",
            name="ck_live_metrics_status",
        ),
        ForeignKeyConstraint(
            ["bank_id", "organization_id"],
            ["banks.id", "banks.organization_id"],
        ),
        ForeignKeyConstraint(
            ["source_fact_period_id", "organization_id", "bank_id"],
            [
                "bank_reporting_periods.id",
                "bank_reporting_periods.organization_id",
                "bank_reporting_periods.bank_id",
            ],
        ),
        UniqueConstraint(
            "organization_id",
            "bank_id",
            "module",
            name="uq_live_metrics_org_bank_module",
        ),
        Index(
            "ix_live_metrics_org_bank", "organization_id", "bank_id"
        ),
    )

    organization_id: Mapped[str] = mapped_column(String(16), nullable=False)
    bank_id: Mapped[str] = mapped_column(String(16), nullable=False)
    # Optional provenance only. Live computation is keyed by current canonical
    # data and never requires a selected reporting period.
    source_fact_period_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    # Compatibility alias for legacy inspectors and fixtures. New live code
    # never reads or writes it as an identity; it only maps to optional
    # provenance above.
    reporting_period_id = synonym("source_fact_period_id")
    source_as_of_date: Mapped[date] = mapped_column(Date, default=date.today, nullable=False)
    module: Mapped[str] = mapped_column(String(16), nullable=False)
    metrics: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, server_default=sql_text("'{}'"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(8), nullable=False)
    computed_from_input_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    engine_version: Mapped[str] = mapped_column(String(80), default="legacy", nullable=False)
    calculation_generation: Mapped[int] = mapped_column(default=0, nullable=False)
    pipeline_state: Mapped[str] = mapped_column(String(16), default="ready", nullable=False)
    pipeline_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CurrentFinancialFact(UuidV4PrimaryKeyMixin, TimestampMixin, Base):
    """The current canonical-derived fact set for the live Treasury plane.

    One current row exists per `(bank, fact_group, category)`. It is replaced
    atomically during ``pipeline_refresh`` and carries the source business date
    and ingestion generation that produced it. It deliberately has no foreign
    key to a reporting period: historical/official facts remain in
    ``BankFinancialFact`` and are only selected by explicit governance paths.
    """

    __tablename__ = "current_financial_facts"
    __table_args__ = (
        CheckConstraint(
            "fact_group IN ('balance_sheet', 'loan_exposure', 'securities', 'off_balance', "
            "'lcr_inflow', 'market_risk', 'operational_income', 'capital_component', "
            "'deposit_behavior', 'irr_position', 'irr_swap', 'fx_position', "
            "'fx_return_history', 'fx_hedge', 'ftp_curve_point', 'ftp_product', "
            "'ftp_branch', 'ftp_nmd', 'ecl_exposure', 'crm_collateral', 'cashflow')",
            name="ck_current_financial_facts_fact_group",
        ),
        ForeignKeyConstraint(
            ["bank_id", "organization_id"], ["banks.id", "banks.organization_id"]
        ),
        UniqueConstraint(
            "organization_id",
            "bank_id",
            "fact_group",
            "category",
            name="uq_current_financial_facts_bank_group_category",
        ),
        Index("ix_current_financial_facts_org_bank_group", "organization_id", "bank_id", "fact_group"),
    )

    organization_id: Mapped[str] = mapped_column(String(16), nullable=False)
    bank_id: Mapped[str] = mapped_column(String(16), nullable=False)
    source_as_of_date: Mapped[date] = mapped_column(Date, nullable=False)
    source_generation: Mapped[int] = mapped_column(Integer, nullable=False)
    fact_group: Mapped[str] = mapped_column(String(40), nullable=False)
    category: Mapped[str] = mapped_column(String(80), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    risk_weight_code: Mapped[str | None] = mapped_column(String(16), nullable=True)
    hqla_level: Mapped[str | None] = mapped_column(String(8), nullable=True)
    ccf_pct: Mapped[Decimal | None] = mapped_column(Numeric(9, 6), nullable=True)
    rate_pct: Mapped[Decimal | None] = mapped_column(Numeric(9, 6), nullable=True)
    income_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    capital_tier: Mapped[str | None] = mapped_column(String(8), nullable=True)
    is_deduction: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=sql_text("false"), nullable=False
    )
    attributes: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, server_default=sql_text("'{}'"), nullable=False
    )


class LiveMetricSnapshot(UuidV4PrimaryKeyMixin, TimestampMixin, Base):
    """Plane-2 history: one row per (bank, calendar day, module).

    Upserted on every pipeline refresh alongside ``live_metrics``, so the
    day's LAST refresh is the end-of-day close and today's row is the live
    edge "so far". Prior-close desk deltas and daily sparklines read this
    ladder; the monthly reporting spine and regulatory runs are untouched.
    """

    __tablename__ = "live_metric_snapshots"
    __table_args__ = (
        CheckConstraint(_MODULE_CHECK, name="ck_live_metric_snapshots_module"),
        CheckConstraint(
            "status IN ('green', 'amber', 'red', 'na')",
            name="ck_live_metric_snapshots_status",
        ),
        ForeignKeyConstraint(
            ["bank_id", "organization_id"],
            ["banks.id", "banks.organization_id"],
        ),
        UniqueConstraint(
            "organization_id",
            "bank_id",
            "snapshot_date",
            "module",
            name="uq_live_metric_snapshots_day",
        ),
        Index(
            "ix_live_metric_snapshots_series",
            "organization_id",
            "bank_id",
            "module",
            "snapshot_date",
        ),
    )

    organization_id: Mapped[str] = mapped_column(String(16), nullable=False)
    bank_id: Mapped[str] = mapped_column(String(16), nullable=False)
    # Retained as historical provenance, not a live identity.
    reporting_period_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    snapshot_date: Mapped[date] = mapped_column(Date, nullable=False)
    module: Mapped[str] = mapped_column(String(16), nullable=False)
    metrics: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, server_default=sql_text("'{}'"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(8), nullable=False)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class LiveFinding(UuidV4PrimaryKeyMixin, TimestampMixin, Base):
    """A live limit breach for a (bank, module) — the alert source.

    Deterministic-rule findings reconciled on every refresh: a continuing breach
    keeps its row (and ``created_at``) while a cleared breach is superseded. Bank
    scoped, unlike the case-scoped ``risk_findings`` of the assessment workflow.
    """

    __tablename__ = "live_findings"
    __table_args__ = (
        CheckConstraint(_MODULE_CHECK, name="ck_live_findings_module"),
        CheckConstraint(
            "severity IN ('low', 'medium', 'high', 'critical')",
            name="ck_live_findings_severity",
        ),
        CheckConstraint(
            "status IN ('open', 'needs_review', 'superseded')",
            name="ck_live_findings_status",
        ),
        ForeignKeyConstraint(
            ["bank_id", "organization_id"],
            ["banks.id", "banks.organization_id"],
        ),
        ForeignKeyConstraint(
            ["source_fact_period_id", "organization_id", "bank_id"],
            [
                "bank_reporting_periods.id",
                "bank_reporting_periods.organization_id",
                "bank_reporting_periods.bank_id",
            ],
        ),
        Index(
            "uq_live_findings_open",
            "organization_id",
            "bank_id",
            "module",
            "rule_id",
            unique=True,
            postgresql_where=sql_text("status = 'open'"),
            sqlite_where=sql_text("status = 'open'"),
        ),
        Index("ix_live_findings_org_bank_status", "organization_id", "bank_id", "status"),
    )

    organization_id: Mapped[str] = mapped_column(String(16), nullable=False)
    bank_id: Mapped[str] = mapped_column(String(16), nullable=False)
    source_fact_period_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    reporting_period_id = synonym("source_fact_period_id")
    source_as_of_date: Mapped[date] = mapped_column(Date, default=date.today, nullable=False)
    module: Mapped[str] = mapped_column(String(16), nullable=False)
    rule_id: Mapped[str] = mapped_column(String(120), nullable=False)
    severity: Mapped[str] = mapped_column(String(8), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), default="open", server_default=sql_text("'open'"), nullable=False
    )
    message: Mapped[str] = mapped_column(Text, nullable=False)
    metric: Mapped[str | None] = mapped_column(String(80), nullable=True)
