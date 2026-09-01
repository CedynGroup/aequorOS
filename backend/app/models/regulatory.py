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
    # THE typed institution discriminator (docs/sdi.md §1): the authoritative
    # licence class every future SDI scoping keys off. FK into the global
    # ``institution_types`` registry, from which the coarse ``institution_class``
    # ('bank'|'sdi'), return family, capital regime and limits resolve — the way
    # ``jurisdiction_code`` resolves country identity. REQUIRED with NO default,
    # by the same fail-loud discipline as ``currency``/``jurisdiction_code``: an
    # unset value means the creation site skipped a required decision, not that
    # the bank is a universal bank. Distinct from the free-text
    # ``InstitutionProfile.institution_type`` master-data field — THIS is the
    # load-bearing, typed, branch-on-me field; the profile string is descriptive.
    institution_type: Mapped[str] = mapped_column(
        String(40), ForeignKey("institution_types.type_code"), nullable=False
    )
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
            "'ftp_branch', 'ftp_nmd', 'ecl_exposure', 'crm_collateral', "
            "'provision_held', 'cashflow')",
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
    # NO default (enterprise audit 2026-08-20 §6). ``default="GHS"`` here meant a
    # fact row inserted without an explicit currency silently became a cedi amount
    # — the same trap ``banks.currency`` was made mandatory to prevent, one table
    # further down. Every writer sets it: ``fact_derivation._fact`` passes
    # ``spec.currency or bank.currency``.
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


class RegulatoryParameterMixin(UuidV4PrimaryKeyMixin, TimestampMixin):
    """Shared columns for effective-dated, approval-tracked regulatory parameters."""

    organization_id: Mapped[str] = mapped_column(
        String(16), ForeignKey("organizations.id"), nullable=False
    )
    # NO default (enterprise audit 2026-08-20 §6). This mixin is inherited by NINE
    # parameter tables, so a single ``default="GH"`` silently filed every board
    # register generation under Ghana — including a Nigerian tenant's. The
    # jurisdiction is part of the parameter's identity (it is in the resolution
    # key), so it must be an explicit decision at every write site; all of them
    # already pass it.
    jurisdiction_code: Mapped[str] = mapped_column(String(8), nullable=False)
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


class ParamConcentrationLimit(RegulatoryParameterMixin, Base):
    """Board credit-concentration limits (BoG Concentration Guidelines, Sept 2025).

    The Guidelines require a Board limit structure per concentration dimension
    (§D: limits defined against capital or total assets, with breach
    escalation) but prescribe NO numeric values — so this register starts
    EMPTY and every row is a Board decision with the mixin's approval
    evidence. An absent limit renders "Not set" on the monitor, never an
    invented number. ``bucket_key`` scopes a limit to one named bucket (a
    single employer, a named sector); NULL applies the limit to the
    dimension's largest bucket.
    """

    __tablename__ = "param_concentration_limit"
    __table_args__ = (
        CheckConstraint(
            "dimension IN ('single_name', 'sector', 'geography', 'product', "
            "'collateral', 'funding', 'employer')",
            name="ck_param_concentration_limit_dimension",
        ),
        CheckConstraint(
            "limit_kind IN ('share_of_book_pct', 'share_of_capital_pct', 'hhi')",
            name="ck_param_concentration_limit_kind",
        ),
        UniqueConstraint(
            "organization_id",
            "jurisdiction_code",
            "dimension",
            "limit_kind",
            "bucket_key",
            "effective_from",
            name="uq_param_concentration_limit_scope",
        ),
    )

    dimension: Mapped[str] = mapped_column(String(24), nullable=False)
    limit_kind: Mapped[str] = mapped_column(String(24), nullable=False)
    bucket_key: Mapped[str | None] = mapped_column(String(120), nullable=True)
    #: Percent for the share kinds; the raw index value (0-10,000) for ``hhi``.
    value: Mapped[Decimal] = mapped_column(Numeric(12, 6), nullable=False)


class ParamCreditThreshold(RegulatoryParameterMixin, Base):
    """Board credit early-warning trigger levels (watch/action bands the credit
    EWIs compare against). Starts EMPTY for the same reason as the
    concentration limits: no instrument prescribes the values."""

    __tablename__ = "param_credit_threshold"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "jurisdiction_code",
            "threshold_code",
            "effective_from",
            name="uq_param_credit_threshold_scope",
        ),
    )

    threshold_code: Mapped[str] = mapped_column(String(60), nullable=False)
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


class ParamEclAssumption(RegulatoryParameterMixin, Base):
    """IFRS 9 PD/LGD assumptions per segment + stage (Phase 2 item 8).

    ``segment`` matches the loan family's fact category, with ``ALL`` as the
    fallback; stage 1 rows carry the 12-month PD, stage 2 the lifetime PD,
    and stage 3 rows contribute only their LGD (PD is 100% by definition for
    credit-impaired exposures). The mixin's approval evidence is the model
    committee / Board trail an auditor asks for.
    """

    __tablename__ = "param_ecl_assumption"
    __table_args__ = (
        CheckConstraint("stage IN (1, 2, 3)", name="ck_param_ecl_assumption_stage"),
        UniqueConstraint(
            "organization_id",
            "jurisdiction_code",
            "segment",
            "stage",
            "effective_from",
            name="uq_param_ecl_assumption_scope",
        ),
    )

    segment: Mapped[str] = mapped_column(String(60), nullable=False)
    stage: Mapped[int] = mapped_column(Integer, nullable=False)
    pd_pct: Mapped[Decimal] = mapped_column(Numeric(9, 6), nullable=False)
    lgd_pct: Mapped[Decimal] = mapped_column(Numeric(9, 6), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class ParamCrmHaircut(RegulatoryParameterMixin, Base):
    """Supervisory haircuts per CRM collateral class (Phase 2 item 9).

    Basel II comprehensive-approach supervisory haircuts (¶151 table) for
    collateral recognized against credit exposures. Distinct from
    ``ParamLiquidityHaircut`` (the LRMD liquidity-value schedule): a class
    with no active row gets ZERO recognition in credit RWA — a haircut is
    never invented for an unknown collateral type.
    """

    __tablename__ = "param_crm_haircut"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "jurisdiction_code",
            "collateral_class",
            "effective_from",
            name="uq_param_crm_haircut_scope",
        ),
    )

    collateral_class: Mapped[str] = mapped_column(String(80), nullable=False)
    haircut_pct: Mapped[Decimal] = mapped_column(Numeric(9, 6), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
