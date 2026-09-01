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
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy import (
    text as sql_text,
)
from sqlalchemy.orm import Mapped, mapped_column, synonym

from app.db.base import Base, TimestampMixin, UuidV4PrimaryKeyMixin

# The live Treasury/ALM surfaces. Rating is a cheap live scorecard over the
# canonical book, current market data, and the other live module outputs.
LIVE_MODULES = ("liquidity", "capital", "credit", "irr", "fx", "ftp", "rating", "forecast")
_MODULE_CHECK = "module IN (" + ", ".join(f"'{module}'" for module in LIVE_MODULES) + ")"


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
        CheckConstraint(
            "retry_classification IS NULL OR retry_classification IN "
            "('structural_unavailable', 'transient_failure')",
            name="ck_live_metrics_retry_classification",
        ),
        CheckConstraint(
            "retry_attempt_count >= 0",
            name="ck_live_metrics_retry_attempt_count",
        ),
        CheckConstraint(
            "next_retry_at IS NULL OR retry_classification = 'transient_failure'",
            name="ck_live_metrics_next_retry_classification",
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
        Index("ix_live_metrics_org_bank", "organization_id", "bank_id"),
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
    # Structural unavailability is a stable result (for example, rating inputs
    # with no entitled market data). Only true module exceptions are transient
    # and carry the bounded queue retry schedule persisted here for operators.
    retry_classification: Mapped[str | None] = mapped_column(String(32), nullable=True)
    retry_attempt_count: Mapped[int] = mapped_column(
        Integer, default=0, server_default=sql_text("0"), nullable=False
    )
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CurrentFinancialFact(UuidV4PrimaryKeyMixin, TimestampMixin, Base):
    """The current canonical-derived fact set for the live Treasury plane.

    One current row exists per `(bank, fact_group, category)`. It is replaced
    atomically during ``pipeline_refresh`` and carries the source business date
    and ingestion generation that produced it. It deliberately has no foreign
    key to a reporting period: historical/official facts remain in
    ``BankFinancialFact`` and are only selected by explicit governance paths.

    Tenant isolation is enforced in the database, not only in application SQL:
    on Postgres the table is ENABLE + FORCE ROW LEVEL SECURITY under policy
    ``current_financial_facts_tenant_isolation``, which admits only rows whose
    ``organization_id`` equals the transaction-local ``app.organization_id``
    GUC (migration ``202608220027``; the GUC is set by the ``after_begin`` hook
    in ``app/db/session.py`` from ``session.info['organization_id']``). FORCE is
    load-bearing because the application role owns the table and would otherwise
    be exempt from its own policy. A session with no GUC sees zero rows — every
    reader and writer here (the live Treasury modules, ``fact_derivation``,
    ``live_state``, ``live_view``, ``implied_rating``) must run on a
    tenant-bound session, exactly as the sibling ``live_metrics`` /
    ``bank_financial_facts`` tables already require.
    """

    __tablename__ = "current_financial_facts"
    __table_args__ = (
        CheckConstraint(
            "fact_group IN ('balance_sheet', 'loan_exposure', 'securities', 'off_balance', "
            "'lcr_inflow', 'market_risk', 'operational_income', 'capital_component', "
            "'deposit_behavior', 'irr_position', 'irr_swap', 'fx_position', "
            "'fx_return_history', 'fx_hedge', 'ftp_curve_point', 'ftp_product', "
            "'ftp_branch', 'ftp_nmd', 'ecl_exposure', 'crm_collateral', "
            "'provision_held', 'cashflow')",
            name="ck_current_financial_facts_fact_group",
        ),
        ForeignKeyConstraint(["bank_id", "organization_id"], ["banks.id", "banks.organization_id"]),
        UniqueConstraint(
            "organization_id",
            "bank_id",
            "fact_group",
            "category",
            name="uq_current_financial_facts_bank_group_category",
        ),
        Index(
            "ix_current_financial_facts_org_bank_group",
            "organization_id",
            "bank_id",
            "fact_group",
        ),
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


class WorkerHeartbeat(Base):
    """Cross-tenant worker liveness evidence, written by the worker itself.

    This table intentionally has no tenant key or RLS policy: it contains no
    tenant financial data and records service health across all tenants. It is
    written only through the worker's verified BYPASSRLS connection.
    """

    __tablename__ = "worker_heartbeats"

    worker_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_job_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)


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
