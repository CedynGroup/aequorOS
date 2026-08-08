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
from app.services.public_ids import new_bank_public_id


class Bank(TimestampMixin, Base):
    __tablename__ = "banks"
    __table_args__ = (
        Index("ix_banks_organization_id", "organization_id"),
        Index(
            "uq_banks_storage_slug",
            "storage_slug",
            unique=True,
            postgresql_where=sql_text("storage_slug IS NOT NULL"),
            sqlite_where=sql_text("storage_slug IS NOT NULL"),
        ),
        UniqueConstraint("id", "organization_id", name="uq_banks_id_organization_id"),
    )

    # THE institution identifier (BK-XXXXXXXX): platform-generated at creation
    # for every bank — sandbox and real tenants alike — and used everywhere
    # (primary key, API paths, UI, integrations). One identity, no aliases.
    id: Mapped[str] = mapped_column(String(16), primary_key=True, default=new_bank_public_id)
    organization_id: Mapped[str] = mapped_column(
        String(16), ForeignKey("organizations.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    short_name: Mapped[str] = mapped_column(String(80), nullable=False)
    # Both are REQUIRED at creation and carry no default. Independent defaults
    # ("GHS" and "GH") were a multi-country trap: they could silently disagree,
    # so a bank created with jurisdiction_code="NG" kept reporting in cedis.
    # The reporting currency belongs to the jurisdiction — resolve it from the
    # registry (jurisdictions.currency_code) at the creation site rather than
    # defaulting here, where the registry is not reachable.
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    jurisdiction_code: Mapped[str] = mapped_column(
        String(8), ForeignKey("jurisdictions.code"), nullable=False
    )
    license_type: Mapped[str] = mapped_column(String(40), nullable=False)
    # DNS-safe identifier used in storage bucket names
    # (aequoros-{env}-{storage_slug}-{tier}); assigned on first ingestion.
    storage_slug: Mapped[str | None] = mapped_column(String(63), nullable=True)


class BankReportingPeriod(UuidV4PrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "bank_reporting_periods"
    __table_args__ = (
        CheckConstraint("status IN ('open', 'closed')", name="ck_bank_reporting_periods_status"),
        ForeignKeyConstraint(
            ["bank_id", "organization_id"],
            ["banks.id", "banks.organization_id"],
        ),
        UniqueConstraint("bank_id", "period_end", name="uq_bank_reporting_periods_bank_period_end"),
        UniqueConstraint(
            "id", "organization_id", "bank_id", name="uq_bank_reporting_periods_id_org_bank"
        ),
        Index(
            "ix_bank_reporting_periods_org_bank_period_end",
            "organization_id",
            "bank_id",
            "period_end",
        ),
    )

    organization_id: Mapped[str] = mapped_column(String(16), nullable=False)
    bank_id: Mapped[str] = mapped_column(String(16), nullable=False)
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    label: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)


class BankFinancialFact(UuidV4PrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "bank_financial_facts"
    __table_args__ = (
        CheckConstraint(
            "fact_group IN ('balance_sheet', 'loan_exposure', 'securities', 'off_balance', "
            "'lcr_inflow', 'market_risk', 'operational_income', 'capital_component', "
            "'deposit_behavior', 'irr_position', 'irr_swap', 'fx_position', "
            "'fx_return_history', 'fx_hedge', 'ftp_curve_point', 'ftp_product', "
            "'ftp_branch', 'ftp_nmd')",
            name="ck_bank_financial_facts_fact_group",
        ),
        ForeignKeyConstraint(
            ["reporting_period_id", "organization_id", "bank_id"],
            [
                "bank_reporting_periods.id",
                "bank_reporting_periods.organization_id",
                "bank_reporting_periods.bank_id",
            ],
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "reporting_period_id",
            "fact_group",
            "category",
            name="uq_bank_financial_facts_period_group_category",
        ),
        Index(
            "ix_bank_financial_facts_org_bank_period_group",
            "organization_id",
            "bank_id",
            "reporting_period_id",
            "fact_group",
        ),
    )

    organization_id: Mapped[str] = mapped_column(String(16), nullable=False)
    bank_id: Mapped[str] = mapped_column(String(16), nullable=False)
    reporting_period_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    fact_group: Mapped[str] = mapped_column(String(40), nullable=False)
    category: Mapped[str] = mapped_column(String(80), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(20, 4), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="GHS", nullable=False)
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


class RegulatoryParameterMixin(UuidV4PrimaryKeyMixin, TimestampMixin):
    """Shared columns for effective-dated, approval-tracked regulatory parameters."""

    organization_id: Mapped[str] = mapped_column(
        String(16), ForeignKey("organizations.id"), nullable=False
    )
    jurisdiction_code: Mapped[str] = mapped_column(String(8), default="GH", nullable=False)
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    approved_by: Mapped[str] = mapped_column(String(120), nullable=False)
    approval_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ParamLcrRunoffRate(RegulatoryParameterMixin, Base):
    __tablename__ = "param_lcr_runoff_rate"
    __table_args__ = (
        CheckConstraint(
            "flow_direction IN ('outflow', 'inflow')",
            name="ck_param_lcr_runoff_rate_flow_direction",
        ),
        UniqueConstraint(
            "organization_id",
            "jurisdiction_code",
            "flow_direction",
            "category",
            "effective_from",
            name="uq_param_lcr_runoff_rate_scope",
        ),
    )

    flow_direction: Mapped[str] = mapped_column(String(8), nullable=False)
    category: Mapped[str] = mapped_column(String(80), nullable=False)
    rate_pct: Mapped[Decimal] = mapped_column(Numeric(9, 6), nullable=False)


class ParamNsfrWeight(RegulatoryParameterMixin, Base):
    __tablename__ = "param_nsfr_weight"
    __table_args__ = (
        CheckConstraint("side IN ('asf', 'rsf')", name="ck_param_nsfr_weight_side"),
        UniqueConstraint(
            "organization_id",
            "jurisdiction_code",
            "side",
            "category",
            "effective_from",
            name="uq_param_nsfr_weight_scope",
        ),
    )

    side: Mapped[str] = mapped_column(String(4), nullable=False)
    category: Mapped[str] = mapped_column(String(80), nullable=False)
    weight_pct: Mapped[Decimal] = mapped_column(Numeric(9, 6), nullable=False)


class ParamRiskWeight(RegulatoryParameterMixin, Base):
    __tablename__ = "param_risk_weight"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "jurisdiction_code",
            "risk_weight_code",
            "effective_from",
            name="uq_param_risk_weight_scope",
        ),
    )

    risk_weight_code: Mapped[str] = mapped_column(String(16), nullable=False)
    weight_pct: Mapped[Decimal] = mapped_column(Numeric(9, 6), nullable=False)


class ParamStressShock(RegulatoryParameterMixin, Base):
    __tablename__ = "param_stress_shock"
    __table_args__ = (
        CheckConstraint(
            "module IN ('liquidity', 'capital', 'forecast', 'irr', 'fx', 'ftp')",
            name="ck_param_stress_shock_module",
        ),
        UniqueConstraint(
            "organization_id",
            "jurisdiction_code",
            "module",
            "scenario_code",
            "shock_key",
            "effective_from",
            name="uq_param_stress_shock_scope",
        ),
    )

    module: Mapped[str] = mapped_column(String(16), nullable=False)
    scenario_code: Mapped[str] = mapped_column(String(40), nullable=False)
    shock_key: Mapped[str] = mapped_column(String(80), nullable=False)
    shock_value: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class ParamCapitalThreshold(RegulatoryParameterMixin, Base):
    __tablename__ = "param_capital_threshold"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "jurisdiction_code",
            "threshold_code",
            "effective_from",
            name="uq_param_capital_threshold_scope",
        ),
    )

    threshold_code: Mapped[str] = mapped_column(String(40), nullable=False)
    # Numeric(12, 6) rather than Numeric(9, 6): threshold values such as the
    # 1250 (12.5x expressed as a percent) RWA multiplier exceed Numeric(9, 6).
    value_pct: Mapped[Decimal] = mapped_column(Numeric(12, 6), nullable=False)


class ParamLiquidityThreshold(RegulatoryParameterMixin, Base):
    """LMTD 2026 ¶11(b)–(e): the Board-set internal threshold register.

    The Board must set internal thresholds for the six liquidity monitoring
    tools at least annually; the mixin's ``approved_by``/``approval_timestamp``
    plus the effective-dated generations ARE the Board-approval evidence an
    examiner asks for ("show me your Board-approved thresholds"). Ratio floors
    for Table 1 live here first; mismatch and concentration limits join as
    their tools land. ``institution_class`` matters because ¶9 makes these
    binding compliance ratios for SDIs while remaining monitoring tools for
    banks — same register, different consequence.
    """

    __tablename__ = "param_liquidity_threshold"
    __table_args__ = (
        CheckConstraint(
            "institution_class IN ('bank', 'sdi')",
            name="ck_param_liquidity_threshold_institution_class",
        ),
        UniqueConstraint(
            "organization_id",
            "jurisdiction_code",
            "institution_class",
            "threshold_code",
            "effective_from",
            name="uq_param_liquidity_threshold_scope",
        ),
    )

    institution_class: Mapped[str] = mapped_column(String(8), default="bank", nullable=False)
    threshold_code: Mapped[str] = mapped_column(String(60), nullable=False)
    threshold_pct: Mapped[Decimal] = mapped_column(Numeric(12, 6), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class ParamLiquidityHaircut(RegulatoryParameterMixin, Base):
    """LRMD 2026 ¶60–63: the institution's internal liquidity-value schedule.

    Estimated haircuts per asset class, re-assessed at least annually by
    Senior Management (¶62(b)) — the mixin's approval evidence and
    effective-dated generations carry that review trail. LMTD Table 9's
    "Estimated Haircut (%)" and "Monetized Value of Collateral" columns
    resolve from here: an asset class with no active row reports a zero
    haircut with the gap noted on the template, never an invented number.
    ``asset_class`` matches against the position's product
    ``regulatory_category`` by longest prefix, so a bank can calibrate
    broadly ("SOVEREIGN") or precisely ("SOVEREIGN_GOG_TBILL").
    """

    __tablename__ = "param_liquidity_haircut"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "jurisdiction_code",
            "asset_class",
            "effective_from",
            name="uq_param_liquidity_haircut_scope",
        ),
    )

    asset_class: Mapped[str] = mapped_column(String(80), nullable=False)
    haircut_pct: Mapped[Decimal] = mapped_column(Numeric(9, 6), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
