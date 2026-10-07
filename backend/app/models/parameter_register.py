"""The per-tenant board parameter registers (``param_*``): effective-dated, approved numbers."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UuidV4PrimaryKeyMixin


class RegulatoryParameterMixin(UuidV4PrimaryKeyMixin, TimestampMixin):
    """Shared columns for effective-dated, approval-tracked regulatory parameters."""

    organization_id: Mapped[str] = mapped_column(
        String(16), ForeignKey("organizations.id"), nullable=False
    )
    # NO default (enterprise audit 2026-08-20 §6). This mixin is shared by the
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
