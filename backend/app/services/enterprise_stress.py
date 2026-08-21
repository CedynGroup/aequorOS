"""Enterprise-wide stress test service (docs/stress.md §3.3–3.4, Phase 2 items 1–4).

The persistence / IO seam for the pure enterprise stress engines. It resolves an
APPROVED macro scenario, assembles the domain inputs from the canonical book and
the effective-dated parameter registers, drives the pure orchestrator +
projection + Appendix II builders, and persists the whole enterprise outcome as
one immutable ``RegulatoryRun`` (module ``enterprise_stress``) with a value-based
``input_hash`` — the same reproducibility spine every official run carries.

It reads ONLY stable models: ``BankFinancialFact`` and the ``Param*`` registers
via ``app.services.params.get_active_params``. It never imports the tenant
regulatory-run services (which are being reworked concurrently); the domain-fact
converters here are self-contained, so this service is decoupled from that churn.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.domain.capital.ecl import EclAssumption, EclExposure
from app.domain.capital.engine import (
    FACT_GROUP_ECL_EXPOSURE,
    GENERAL_PROVISIONS_CATEGORY,
    TIER_T2,
    CapitalFact,
    CapitalParams,
    compute_capital_ratios,
    compute_rwa,
    tier1_capital,
)
from app.domain.forecasting.engine import ForecastAssumptions, ForecastFact, ForecastParams
from app.domain.fx.engine import FxPosition
from app.domain.irr.engine import IrrPosition
from app.domain.liquidity.engine import LiquidityFact, LiquidityParams
from app.domain.stress.appendix_ii import Pillar2Requirement, build_appendix_ii, thousands
from app.domain.stress.concentration import (
    ConcentrationExposure,
    ConcentrationInputs,
    FundingPosition,
)
from app.domain.stress.contingent_leverage import (
    ContingentLeverageInputs,
    DerivativePosition,
    SftPosition,
)
from app.domain.stress.credit_bottom_up import (
    BottomUpCreditInputs,
    CreditExposure,
    result_for_year,
)
from app.domain.stress.management_actions import (
    ManagementActionsResult,
    apply_management_actions,
)
from app.domain.stress.operational import OperationalConfig
from app.domain.stress.orchestrator import (
    EnterpriseStressInputs,
    EnterpriseStressOutcome,
    FxStressInputs,
    IrrStressInputs,
    run_enterprise_stress,
)
from app.domain.stress.projection import (
    EnterpriseProjection,
    EnterpriseProjectionInputs,
    ProjectionInputError,
    project_enterprise,
)
from app.domain.stress.translation import MacroPathPoint
from app.models import (
    Bank,
    BankFinancialFact,
    BankReportingPeriod,
    CanonicalCounterparty,
    CanonicalPosition,
    CanonicalPositionSnapshot,
    CanonicalProduct,
    MacroScenario,
    MacroScenarioPath,
    ManagementActionPlan,
    ParamCapitalThreshold,
    ParamCrmHaircut,
    ParamEclAssumption,
    ParamLcrRunoffRate,
    ParamNsfrWeight,
    ParamRiskWeight,
    ParamStressShock,
    RegulatoryRun,
)
from app.schemas.enterprise_stress import (
    EnterpriseStressRead,
    EnterpriseStressRunCreate,
    EnterpriseStressRunSummary,
    EnterpriseStressSummary,
    PlanAssumptionsIn,
)
from app.services import (
    institution_types,
    macro_scenarios,
    management_action_plans,
    regulatory_parameters,
)
from app.services.audit import record_event
from app.services.params import get_active_params
from app.services.regulatory_capital import _SDI_STRUCTURAL_CAPITAL

ENGINE_VERSION = "enterprise-stress-v1.0.0"
INPUT_SCHEMA_VERSION = "enterprise-stress-input-v1"
OUTPUT_SCHEMA_VERSION = "enterprise-stress-outcome-v1"
MODULE_ENTERPRISE_STRESS = "enterprise_stress"

_ZERO = Decimal("0")
_HUNDRED = Decimal("100")

_CAPITAL_GROUPS = (
    "balance_sheet",
    "loan_exposure",
    "off_balance",
    "market_risk",
    "operational_income",
    "capital_component",
    "ecl_exposure",
    "crm_collateral",
)
_LIQUIDITY_GROUPS = (
    "balance_sheet",
    "securities",
    "loan_exposure",
    "off_balance",
    "lcr_inflow",
)
_FORECAST_GROUPS = (
    "balance_sheet",
    "loan_exposure",
    "securities",
    "off_balance",
    "lcr_inflow",
    "market_risk",
    "operational_income",
    "capital_component",
)
_IRR_GROUPS = ("irr_position",)
_FX_GROUPS = ("fx_position",)

_REQUIRED_CAPITAL_THRESHOLDS = (
    "bia_alpha_pct",
    "fx_charge_pct",
    "rwa_multiplier",
    "tier2_gp_cap_pct_credit_rwa",
    "cet1_min",
    "tier1_min",
    "car_min",
    "leverage_min",
    "car_early_warning",
    "car_critical",
)
_REQUIRED_LIQUIDITY_THRESHOLDS = ("lcr_min", "lcr_amber_floor", "nsfr_min", "lcr_inflow_cap_pct")

#: Inert Basel liquidity params for an SDI run, where the LCR/NSFR leg is skipped
#: (docs/sdi.md §4.6). The enterprise projection reads ``ForecastParams.liquidity``
#: only when ``basel_liquidity`` is True, so this sentinel is never evaluated — it
#: exists solely to satisfy the non-optional field without asserting a Basel value.
_EMPTY_LIQUIDITY_PARAMS = LiquidityParams(
    outflow_rates={},
    inflow_rates={},
    asf_weights={},
    rsf_weights={},
    inflow_cap_pct=_ZERO,
    lcr_min_pct=_ZERO,
    lcr_amber_floor_pct=_ZERO,
    nsfr_min_pct=_ZERO,
    nsfr_amber_floor_pct=_ZERO,
)

# Base-case plan defaults (used where the request omits a field). Documented,
# conservative, jurisdiction-neutral business-as-usual assumptions.
_DEFAULT_NIM_PCT = Decimal("4.8")
_DEFAULT_COST_TO_INCOME_PCT = Decimal("50")
_DEFAULT_CREDIT_LOSS_RATE_PCT = Decimal("1.0")
_DEFAULT_DIVIDEND_PAYOUT_PCT = Decimal("30")
_DEFAULT_FEE_INCOME_PCT_ASSETS = Decimal("1.2")
_DEFAULT_TAX_RATE_PCT = Decimal("25")
_DEFAULT_LOAN_GROWTH_PCT = Decimal("15")


class EnterpriseStressError(HTTPException):
    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None) -> None:
        payload: dict[str, Any] = {"error_code": code, "message": message}
        if details:
            payload["details"] = details
        super().__init__(status_code=status.HTTP_409_CONFLICT, detail=payload)


def _dec(value: Any) -> Decimal:
    return Decimal(str(value))


def _get_bank_or_404(db: Session, ctx: TenantContext, bank_id: str) -> Bank:
    bank = db.scalar(
        select(Bank).where(Bank.id == bank_id, Bank.organization_id == ctx.organization_id)
    )
    if bank is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bank not found.")
    return bank


def _get_period_or_404(
    db: Session, ctx: TenantContext, bank: Bank, period_id: UUID
) -> BankReportingPeriod:
    period = db.scalar(
        select(BankReportingPeriod).where(
            BankReportingPeriod.id == period_id,
            BankReportingPeriod.organization_id == ctx.organization_id,
            BankReportingPeriod.bank_id == bank.id,
        )
    )
    if period is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Reporting period not found."
        )
    return period


# --- Fact loading + domain conversion (stable models only) -------------------


def _load_facts(
    db: Session,
    ctx: TenantContext,
    bank: Bank,
    period: BankReportingPeriod,
    groups: tuple[str, ...],
) -> list[BankFinancialFact]:
    return list(
        db.scalars(
            select(BankFinancialFact)
            .where(
                BankFinancialFact.organization_id == ctx.organization_id,
                BankFinancialFact.bank_id == bank.id,
                BankFinancialFact.reporting_period_id == period.id,
                BankFinancialFact.fact_group.in_(groups),
            )
            .order_by(BankFinancialFact.fact_group, BankFinancialFact.category)
        )
    )


def _capital_fact(fact: BankFinancialFact) -> CapitalFact:
    return CapitalFact(
        fact_group=fact.fact_group,
        category=fact.category,
        amount=_dec(fact.amount),
        risk_weight_code=fact.risk_weight_code,
        ccf_pct=_dec(fact.ccf_pct) if fact.ccf_pct is not None else None,
        income_year=fact.income_year,
        capital_tier=fact.capital_tier,
        is_deduction=fact.is_deduction,
        side=fact.attributes.get("side"),
    )


def _liquidity_fact(fact: BankFinancialFact) -> LiquidityFact:
    return LiquidityFact(
        fact_group=fact.fact_group,
        category=fact.category,
        amount=_dec(fact.amount),
        hqla_level=fact.hqla_level,
        side=fact.attributes.get("side"),
        cash_derived=fact.attributes.get("source") == "cash",
    )


def _forecast_fact(fact: BankFinancialFact) -> ForecastFact:
    return ForecastFact(
        fact_group=fact.fact_group,
        category=fact.category,
        amount=_dec(fact.amount),
        risk_weight_code=fact.risk_weight_code,
        hqla_level=fact.hqla_level,
        ccf_pct=_dec(fact.ccf_pct) if fact.ccf_pct is not None else None,
        income_year=fact.income_year,
        capital_tier=fact.capital_tier,
        is_deduction=fact.is_deduction,
        side=fact.attributes.get("side"),
        cash_derived=fact.attributes.get("source") == "cash",
    )


# --- Parameter resolution (stable models only) -------------------------------


def _capital_params(db: Session, ctx: TenantContext, bank: Bank, as_of: date) -> CapitalParams:
    weight_rows = get_active_params(
        db, ctx.organization_id, bank.jurisdiction_code, ParamRiskWeight, as_of
    )
    threshold_rows = get_active_params(
        db, ctx.organization_id, bank.jurisdiction_code, ParamCapitalThreshold, as_of
    )
    crm_rows = get_active_params(
        db, ctx.organization_id, bank.jurisdiction_code, ParamCrmHaircut, as_of
    )
    thresholds = {row.threshold_code: _dec(row.value_pct) for row in threshold_rows}
    risk_weights = {row.risk_weight_code: _dec(row.weight_pct) for row in weight_rows}
    crm_haircuts = {row.collateral_class: _dec(row.haircut_pct) for row in crm_rows}
    # SDI simplified s.29 solvency (docs/sdi.md §4.6, Phase H): only the CAR floor
    # is a required regulatory value; market/operational/tier/leverage take the
    # s.29 structural settings and `basel_applicable=False` so the projection's
    # minima check omits the Basel sub-tier / leverage floors. Banks: unchanged.
    if institution_types.institution_class(db, bank) == "sdi":
        return _sdi_capital_params(db, bank, as_of, thresholds, risk_weights, crm_haircuts)
    missing = [code for code in _REQUIRED_CAPITAL_THRESHOLDS if code not in thresholds]
    if missing:
        raise EnterpriseStressError(
            "missing_parameter",
            "Required capital threshold parameters are not configured.",
            {"threshold_codes": missing},
        )
    return CapitalParams(
        risk_weights=risk_weights,
        bia_alpha_pct=thresholds["bia_alpha_pct"],
        fx_charge_pct=thresholds["fx_charge_pct"],
        rwa_multiplier_pct=thresholds["rwa_multiplier"],
        tier2_gp_cap_pct_credit_rwa=thresholds["tier2_gp_cap_pct_credit_rwa"],
        cet1_min_pct=thresholds["cet1_min"],
        tier1_min_pct=thresholds["tier1_min"],
        car_min_pct=thresholds["car_min"],
        leverage_min_pct=thresholds["leverage_min"],
        car_early_warning_pct=thresholds["car_early_warning"],
        car_critical_pct=thresholds["car_critical"],
        crm_haircuts=crm_haircuts,
    )


def _sdi_capital_params(  # noqa: PLR0913 - the resolved capital inputs
    db: Session,
    bank: Bank,
    as_of: date,
    thresholds: dict[str, Decimal],
    risk_weights: dict[str, Decimal],
    crm_haircuts: dict[str, Decimal],
) -> CapitalParams:
    """SDI simplified s.29 capital params for the stress projection — the CAR floor
    from the tenant board register, else the control-plane class default; the Basel
    sub-tier constructs take the shared structural sentinels (mirror of
    ``regulatory_capital._sdi_engine_params``)."""
    car_min = thresholds.get("car_min")
    if car_min is None:
        param = regulatory_parameters.try_resolve(db, bank, "car_min", as_of=as_of)
        car_min = param.value if param is not None else None
    if car_min is None:
        raise EnterpriseStressError(
            "missing_parameter",
            "The SDI CAR floor (car_min) is configured neither on the board register "
            "nor in the regulatory-parameter control plane.",
            {"threshold_codes": ["car_min"]},
        )
    s = _SDI_STRUCTURAL_CAPITAL
    return CapitalParams(
        risk_weights=risk_weights,
        bia_alpha_pct=s["bia_alpha_pct"],
        fx_charge_pct=s["fx_charge_pct"],
        rwa_multiplier_pct=s["rwa_multiplier"],
        tier2_gp_cap_pct_credit_rwa=s["tier2_gp_cap_pct_credit_rwa"],
        cet1_min_pct=s["cet1_min"],
        tier1_min_pct=s["tier1_min"],
        car_min_pct=car_min,
        leverage_min_pct=s["leverage_min"],
        car_early_warning_pct=car_min,
        car_critical_pct=car_min,
        crm_haircuts=crm_haircuts,
        basel_applicable=False,
    )


def _liquidity_params(db: Session, ctx: TenantContext, bank: Bank, as_of: date) -> LiquidityParams:
    runoff_rows = get_active_params(
        db, ctx.organization_id, bank.jurisdiction_code, ParamLcrRunoffRate, as_of
    )
    nsfr_rows = get_active_params(
        db, ctx.organization_id, bank.jurisdiction_code, ParamNsfrWeight, as_of
    )
    threshold_rows = get_active_params(
        db, ctx.organization_id, bank.jurisdiction_code, ParamCapitalThreshold, as_of
    )
    outflow_rates: dict[str, Decimal] = {}
    inflow_rates: dict[str, Decimal] = {}
    for row in runoff_rows:
        target = outflow_rates if row.flow_direction == "outflow" else inflow_rates
        target[row.category] = _dec(row.rate_pct)
    asf_weights: dict[str, Decimal] = {}
    rsf_weights: dict[str, Decimal] = {}
    for row in nsfr_rows:
        target = asf_weights if row.side == "asf" else rsf_weights
        target[row.category] = _dec(row.weight_pct)
    thresholds = {row.threshold_code: _dec(row.value_pct) for row in threshold_rows}
    missing = [code for code in _REQUIRED_LIQUIDITY_THRESHOLDS if code not in thresholds]
    if missing:
        raise EnterpriseStressError(
            "missing_parameter",
            "Required liquidity threshold parameters are not configured.",
            {"threshold_codes": missing},
        )
    amber_floor = thresholds["lcr_amber_floor"]
    return LiquidityParams(
        outflow_rates=outflow_rates,
        inflow_rates=inflow_rates,
        asf_weights=asf_weights,
        rsf_weights=rsf_weights,
        inflow_cap_pct=thresholds["lcr_inflow_cap_pct"],
        lcr_min_pct=thresholds["lcr_min"],
        lcr_amber_floor_pct=amber_floor,
        nsfr_min_pct=thresholds["nsfr_min"],
        nsfr_amber_floor_pct=amber_floor,
    )


def _ecl_inputs(
    db: Session,
    ctx: TenantContext,
    bank: Bank,
    as_of: date,
    capital_rows: list[BankFinancialFact],
) -> tuple[list[EclExposure], list[EclAssumption]]:
    assumptions_rows = get_active_params(
        db, ctx.organization_id, bank.jurisdiction_code, ParamEclAssumption, as_of
    )
    exposures: list[EclExposure] = []
    for fact in capital_rows:
        if fact.fact_group != FACT_GROUP_ECL_EXPOSURE:
            continue
        segment, _, stage_token = fact.category.rpartition(":")
        if not segment or not stage_token.startswith("stage"):
            continue
        try:
            stage = int(stage_token.removeprefix("stage"))
        except ValueError:
            continue
        exposures.append(EclExposure(segment=segment, stage=stage, ead=_dec(fact.amount)))
    assumptions = [
        EclAssumption(
            segment=row.segment, stage=row.stage, pd_pct=_dec(row.pd_pct), lgd_pct=_dec(row.lgd_pct)
        )
        for row in assumptions_rows
    ]
    if not exposures or not assumptions:
        return [], []
    return exposures, assumptions


def _irr_inputs(  # noqa: PLR0913 - a read helper naming its scope
    db: Session,
    ctx: TenantContext,
    bank: Bank,
    period: BankReportingPeriod,
    as_of: date,
    tier1: Decimal,
) -> IrrStressInputs | None:
    positions = _irr_positions(_load_facts(db, ctx, bank, period, _IRR_GROUPS))
    curve = _base_curve(db, ctx, bank, as_of)
    if not positions or not curve or tier1 <= _ZERO:
        return None
    # Every position must price on the curve; skip IRR if the curve is partial.
    if any(position.midpoint_years not in curve for position in positions):
        return None
    return IrrStressInputs(positions=tuple(positions), curve=curve, tier1=tier1)


def _irr_positions(rows: list[BankFinancialFact]) -> list[IrrPosition]:
    positions: list[IrrPosition] = []
    for fact in rows:
        attributes = fact.attributes or {}
        side = attributes.get("side")
        bucket = attributes.get("bucket")
        midpoint = attributes.get("midpoint_years")
        if side not in ("asset", "liability") or bucket is None or midpoint is None:
            continue
        positions.append(
            IrrPosition(
                side=side,
                bucket=str(bucket),
                amount=_dec(fact.amount),
                rate_pct=_dec(fact.rate_pct) if fact.rate_pct is not None else _ZERO,
                fixed_or_float=str(attributes.get("fixed_or_float", "fixed")),
                midpoint_years=_dec(midpoint),
                source=str(attributes.get("source", fact.category)),
            )
        )
    return positions


def _base_curve(
    db: Session, ctx: TenantContext, bank: Bank, as_of: date
) -> dict[Decimal, Decimal]:
    rows = get_active_params(
        db, ctx.organization_id, bank.jurisdiction_code, ParamStressShock, as_of
    )
    curve: dict[Decimal, Decimal] = {}
    for row in rows:
        if row.module != "irr" or row.scenario_code != "base_curve":
            continue
        key = row.shock_key
        if not key.endswith("y"):
            continue
        try:
            curve[_dec(key.removesuffix("y"))] = _dec(row.shock_value)
        except (ValueError, ArithmeticError):
            continue
    return curve


def _fx_inputs(  # noqa: PLR0913 - a read helper naming its scope
    db: Session,
    ctx: TenantContext,
    bank: Bank,
    period: BankReportingPeriod,
    as_of: date,
    tier1: Decimal,
) -> FxStressInputs | None:
    positions = _fx_positions(_load_facts(db, ctx, bank, period, _FX_GROUPS))
    thresholds = {
        row.threshold_code: _dec(row.value_pct)
        for row in get_active_params(
            db, ctx.organization_id, bank.jurisdiction_code, ParamCapitalThreshold, as_of
        )
    }
    single = thresholds.get("fx_nop_single_limit_pct")
    aggregate = thresholds.get("fx_nop_aggregate_limit_pct")
    if not positions or tier1 <= _ZERO or single is None or aggregate is None:
        return None
    return FxStressInputs(
        positions=tuple(positions),
        tier1=tier1,
        single_limit_pct=single,
        aggregate_limit_pct=aggregate,
    )


def _fx_positions(rows: list[BankFinancialFact]) -> list[FxPosition]:
    positions: list[FxPosition] = []
    for fact in rows:
        attributes = fact.attributes or {}
        currency = attributes.get("currency") or fact.currency
        net_ghs = attributes.get("net_ghs")
        if net_ghs is None:
            continue
        positions.append(
            FxPosition(
                currency=str(currency),
                net_ghs=_dec(net_ghs),
                spot_ghs=_dec(attributes.get("spot_ghs", "0")),
                net_ccy=_dec(attributes.get("net_ccy", "0")),
                assets_ccy=_dec(attributes.get("assets_ccy", "0")),
                liabilities_ccy=_dec(attributes.get("liabilities_ccy", "0")),
                net_derivatives_ccy=_dec(attributes.get("net_derivatives_ccy", "0")),
            )
        )
    return positions


# --- Phase 4: exposure-level canonical readers (read-only, stable models) ----
# The per-risk methods (bottom-up credit, concentration, contingent leverage)
# read the canonical position book directly, mirroring le_generation's
# current-generation slice (accepted/warning, non-superseded) WITHOUT importing
# it (le_generation pulls in the concurrently-reworked regulatory_* services).
# Everything here degrades gracefully to empty when the bank has no canonical
# positions — the existing BankFinancialFact-only path is byte-identical.

_CANONICAL_INCLUDED_STATUSES = ("accepted", "warning")
# The migrating credit book: loans + interbank placements (the counterparty
# credit exposures the AppI credit method downgrades). Securities are the
# sovereign/investment book (market/IRRBB stress), excluded from the credit-loss
# migration but INCLUDED in concentration (issuer/sovereign concentration is a
# real Ghanaian risk — DDEP).
_CREDIT_POSITION_TYPES = ("LOAN", "INTERBANK_PLACEMENT")
_CONCENTRATION_POSITION_TYPES = ("LOAN", "INTERBANK_PLACEMENT", "SECURITY_HOLDING")
_FUNDING_POSITION_TYPES = ("DEPOSIT", "INTERBANK_BORROWING")
_DERIVATIVE_POSITION_TYPES = ("DERIVATIVE", "FX_HEDGE", "INTEREST_RATE_SWAP")

@dataclass(frozen=True)
class _ExposureRow:
    """One current-generation position snapshot flattened for the Phase-4 methods.

    Built once by ``_load_exposure_rows`` (the single place the SQLAlchemy ``Row``
    is unpacked), so every downstream builder consumes a typed dataclass — the
    ``le_generation`` house pattern.
    """

    source_reference: str
    position_type: str
    currency: str
    balance_ghs: Decimal
    is_foreign_currency: bool
    notional_ghs: Decimal
    ifrs9_stage: int | None
    attributes: dict[str, Any]
    counterparty_type: str | None
    counterparty_resident: bool | None
    counterparty_country: str | None
    group_key: str
    regulatory_category: str | None
    product_risk_weight_code: str | None
    product_code: str | None

# Documented PD/LGD/RW defaults used when the source carries none on the
# snapshot (¶45); a snapshot attribute always wins. Sovereign-ish classes get a
# zero PD so they contribute no modelled expected loss.
_DEFAULT_PD_BY_STAGE: dict[int, Decimal] = {
    1: Decimal("2"),
    2: Decimal("12"),
    3: Decimal("100"),
}
_DEFAULT_LGD_PCT = Decimal("45")
_DEFAULT_RISK_WEIGHT_PCT = Decimal("100")
_DEFAULT_DERIVATIVE_PFE_PCT = Decimal("5")
_ZERO_PD_CLASSES = frozenset(
    {"gog", "bog", "other_sovereigns_central_banks", "multilateral_development_banks"}
)


def _canonical_group_key(source_reference: str, counterparty: Any) -> str:
    """Connected-group / single-name identity (le_generation's pattern)."""
    if counterparty is not None:
        if counterparty.group_reference:
            return f"group:{counterparty.group_reference}"
        cp_attributes = counterparty.attributes or {}
        for key in ("group_reference", "group", "parent"):
            value = cp_attributes.get(key)
            if value:
                return f"group:{value}"
        return f"cp:{counterparty.name}"
    return f"pos:{source_reference}"


def _load_exposure_rows(
    db: Session,
    ctx: TenantContext,
    bank: Bank,
    as_of: date,
    position_types: tuple[str, ...],
) -> list[_ExposureRow]:
    """The current-generation (accepted/warning, non-superseded) slice, flattened.

    The single place a SQLAlchemy ``Row`` is unpacked; balance/notional are
    resolved to GHS here (foreign books without an ingested conversion contribute
    zero, mirroring ``fact_derivation``/``le_generation``).
    """
    base_currency = (bank.currency or "GHS").strip().upper()
    records = db.execute(
        select(
            CanonicalPositionSnapshot,
            CanonicalPosition,
            CanonicalCounterparty,
            CanonicalProduct,
        )
        .join(CanonicalPosition, CanonicalPositionSnapshot.position_id == CanonicalPosition.id)
        .outerjoin(
            CanonicalCounterparty,
            CanonicalPositionSnapshot.counterparty_id == CanonicalCounterparty.id,
        )
        .outerjoin(
            CanonicalProduct,
            CanonicalPositionSnapshot.product_id == CanonicalProduct.id,
        )
        .where(
            CanonicalPositionSnapshot.organization_id == ctx.organization_id,
            CanonicalPositionSnapshot.bank_id == bank.id,
            CanonicalPositionSnapshot.as_of_date == as_of,
            CanonicalPositionSnapshot.superseded_by.is_(None),
            CanonicalPositionSnapshot.validation_status.in_(_CANONICAL_INCLUDED_STATUSES),
            CanonicalPosition.position_type.in_(position_types),
        )
        .order_by(CanonicalPositionSnapshot.source_reference)
    ).all()

    rows: list[_ExposureRow] = []
    for snapshot, position, counterparty, product in records:
        attributes = dict(snapshot.attributes or {})
        currency = str(position.currency).strip().upper()
        balance_value = attributes.get("balance_ghs")
        if balance_value is not None and balance_value != "":
            balance_ghs = _dec(balance_value)
        elif currency == base_currency:
            balance_ghs = _dec(snapshot.balance or 0)
        else:
            balance_ghs = _ZERO
        notional_value = attributes.get("notional_ghs")
        if notional_value is not None and notional_value != "":
            notional_ghs = _dec(notional_value)
        elif currency == base_currency and snapshot.notional is not None:
            notional_ghs = _dec(snapshot.notional)
        else:
            notional_ghs = _ZERO
        issuer = attributes.get("issuer")
        group_key = _canonical_group_key(str(snapshot.source_reference), counterparty)
        if counterparty is None and issuer:
            group_key = f"issuer:{issuer}"
        rows.append(
            _ExposureRow(
                source_reference=str(snapshot.source_reference),
                position_type=str(position.position_type),
                currency=currency,
                balance_ghs=balance_ghs,
                is_foreign_currency=currency != base_currency,
                notional_ghs=notional_ghs,
                ifrs9_stage=snapshot.ifrs9_stage,
                attributes=attributes,
                counterparty_type=(
                    counterparty.counterparty_type if counterparty is not None else None
                ),
                counterparty_resident=(
                    counterparty.resident if counterparty is not None else None
                ),
                counterparty_country=(
                    counterparty.country_code if counterparty is not None else None
                ),
                group_key=group_key,
                regulatory_category=(
                    product.regulatory_category if product is not None else None
                ),
                product_risk_weight_code=(
                    product.risk_weight_code if product is not None else None
                ),
                product_code=(product.product_code if product is not None else None),
            )
        )
    return rows


def _crd_class_for(row: _ExposureRow) -> str:  # noqa: PLR0911 - a flat CRD classifier
    """Map a flattened exposure onto a CRD exposure class (documented; ¶45).

    Impaired exposures (IFRS-9 stage 3) are ``past_due``; ``HIGH_RISK`` products
    are ``high_risk``; the rest key off the counterparty type, with
    counterparty-less securities keying off the product's regulatory category.
    """
    category = (row.regulatory_category or "").upper()
    if "HIGH_RISK" in category or "HIGH RISK" in category:
        return "high_risk"
    if row.ifrs9_stage == 3:  # noqa: PLR2004 - IFRS-9 stage 3 = credit-impaired
        return "past_due"
    cp_type = row.counterparty_type
    if cp_type == "CENTRAL_BANK":
        return "bog"
    if cp_type == "SOVEREIGN":
        return "gog" if row.counterparty_resident is not False else "other_sovereigns_central_banks"
    if cp_type == "GOVERNMENT_ENTITY":
        return "public_sector_entities"
    if cp_type == "MULTILATERAL_DEV_BANK":
        return "multilateral_development_banks"
    if cp_type in ("BANK_OECD", "BANK_NON_OECD"):
        return "banks"
    if cp_type == "NBFI":
        return "other_financial_institutions"
    if cp_type == "CORPORATE":
        return "corporates"
    if cp_type in ("SME", "RETAIL_INDIVIDUAL"):
        return "retail_sme"
    if category.startswith("SOVEREIGN"):
        return "gog"
    return "other"


def _exposure_risk_weight(row: _ExposureRow, capital_params: CapitalParams) -> Decimal:
    code = row.attributes.get("risk_weight_code") or row.product_risk_weight_code
    if code is not None:
        return capital_params.risk_weights.get(str(code), _DEFAULT_RISK_WEIGHT_PCT)
    return _DEFAULT_RISK_WEIGHT_PCT


def _exposure_pd_lgd(row: _ExposureRow, crd_class: str) -> tuple[Decimal, Decimal]:
    if crd_class in _ZERO_PD_CLASSES:
        pd_default = _ZERO
    else:
        pd_default = _DEFAULT_PD_BY_STAGE.get(row.ifrs9_stage or 1, _DEFAULT_PD_BY_STAGE[1])
    pd_pct = _dec(row.attributes["pd_pct"]) if row.attributes.get("pd_pct") else pd_default
    lgd_pct = _dec(row.attributes["lgd_pct"]) if row.attributes.get("lgd_pct") else _DEFAULT_LGD_PCT
    return pd_pct, lgd_pct


def _build_credit_exposures(
    rows: list[_ExposureRow], capital_params: CapitalParams
) -> list[CreditExposure]:
    exposures: list[CreditExposure] = []
    for row in rows:
        if row.balance_ghs <= _ZERO:
            continue
        crd_class = _crd_class_for(row)
        pd_pct, lgd_pct = _exposure_pd_lgd(row, crd_class)
        exposures.append(
            CreditExposure(
                exposure_id=row.source_reference,
                crd_class=crd_class,
                ead=row.balance_ghs,
                pd_pct=pd_pct,
                lgd_pct=lgd_pct,
                risk_weight_pct=_exposure_risk_weight(row, capital_params),
                is_foreign_currency=row.is_foreign_currency,
            )
        )
    return exposures


def _build_concentration_inputs(
    asset_rows: list[_ExposureRow],
    funding_rows: list[_ExposureRow],
    base_credit_rwa: Decimal,
) -> ConcentrationInputs | None:
    exposures: list[ConcentrationExposure] = []
    for row in asset_rows:
        if row.balance_ghs <= _ZERO:
            continue
        sector = (
            row.attributes.get("sector")
            or row.attributes.get("industry")
            or row.counterparty_type
        )
        collateral = row.attributes.get("collateral_type") or row.attributes.get(
            "collateral_asset_class"
        )
        exposures.append(
            ConcentrationExposure(
                exposure_id=row.source_reference,
                ead=row.balance_ghs,
                group_key=row.group_key,
                sector=str(sector) if sector else None,
                geography=row.counterparty_country,
                product=row.product_code,
                collateral_type=str(collateral) if collateral else None,
                on_balance=row.position_type != "COMMITMENT_UNDRAWN",
            )
        )
    funding: list[FundingPosition] = [
        FundingPosition(source_key=row.group_key, amount=row.balance_ghs)
        for row in funding_rows
        if row.balance_ghs > _ZERO
    ]
    if not exposures and not funding:
        return None
    return ConcentrationInputs(
        exposures=tuple(exposures),
        funding=tuple(funding),
        base_credit_rwa=base_credit_rwa,
    )


def _build_contingent_leverage_inputs(
    rows: list[_ExposureRow],
    base_leverage_exposure: Decimal,
    tier1: Decimal,
) -> ContingentLeverageInputs | None:
    derivatives: list[DerivativePosition] = []
    sfts: list[SftPosition] = []
    netting_benefit = _ZERO
    collateral_swaps = _ZERO
    for row in rows:
        attributes = row.attributes
        if attributes.get("sft_gross_ghs"):
            sfts.append(
                SftPosition(
                    position_id=row.source_reference,
                    gross_value=_dec(attributes["sft_gross_ghs"]),
                )
            )
            continue
        pfe = (
            _dec(attributes["pfe_addon_ghs"])
            if attributes.get("pfe_addon_ghs")
            else row.notional_ghs * _DEFAULT_DERIVATIVE_PFE_PCT / _HUNDRED
        )
        replacement_cost = (
            _dec(attributes["mtm_ghs"])
            if attributes.get("mtm_ghs") and _dec(attributes["mtm_ghs"]) > _ZERO
            else _ZERO
        )
        derivatives.append(
            DerivativePosition(
                position_id=row.source_reference,
                notional=row.notional_ghs,
                pfe_addon=pfe,
                replacement_cost=replacement_cost,
            )
        )
        if attributes.get("netting_benefit_ghs"):
            netting_benefit += _dec(attributes["netting_benefit_ghs"])
        if attributes.get("collateral_swap_notional_ghs"):
            collateral_swaps += _dec(attributes["collateral_swap_notional_ghs"])
    if not derivatives and not sfts and netting_benefit == _ZERO and collateral_swaps == _ZERO:
        return None
    return ContingentLeverageInputs(
        base_leverage_exposure=base_leverage_exposure,
        tier1=tier1,
        derivatives=tuple(derivatives),
        sfts=tuple(sfts),
        netting_benefit=netting_benefit,
        collateral_swaps_notional=collateral_swaps,
    )


def _credit_overlays(
    exposures: list[CreditExposure],
    paths: list[MacroPathPoint],
    horizon_years: int,
) -> tuple[dict[int, Decimal], dict[int, dict[str, Decimal]]]:
    """Per-stress-year credit-RWA uplift factor + real exposure-class decomposition.

    Perfect-foresight: each year is conditioned by its own macro (¶48). The
    uplift feeds the projection's stress-leg credit RWA (rating migration + FX
    revaluation); the decomposition feeds Table 1's "Impact of Adverse".
    """
    uplift: dict[int, Decimal] = {}
    decomposition: dict[int, dict[str, Decimal]] = {}
    for year in range(1, horizon_years + 1):
        result = result_for_year(exposures, paths, year)
        uplift[year] = result.credit_rwa_uplift_factor
        decomposition[year] = result.incremental_loss_by_class()
    return uplift, decomposition


def _pillar2_overlay(
    outcome: EnterpriseStressOutcome, tier1: Decimal, horizon_years: int
) -> dict[int, Pillar2Requirement] | None:
    """Derive the Table 5 Pillar-2 add-ons from the enterprise outcome (GHS'000).

    credit_concentration ← the concentration Pillar-2 charge; irrbb ← the adverse
    ΔEVE; country_and_fx ← the incremental NOP-vs-Tier-1; other ← the worst
    operational loss. Sovereign / reputational stay unmodelled (None). Held
    across the horizon (an as-of ICAAP assessment).
    """
    credit_conc: Decimal | None = None
    irrbb: Decimal | None = None
    country_fx: Decimal | None = None
    other: Decimal | None = None
    if (
        outcome.concentration is not None
        and outcome.concentration.pillar2_concentration_charge > _ZERO
    ):
        credit_conc = thousands(outcome.concentration.pillar2_concentration_charge)
    if outcome.irr is not None:
        eve_loss = max(-outcome.irr.delta_eve, _ZERO)
        if eve_loss > _ZERO:
            irrbb = thousands(eve_loss)
    if outcome.fx is not None:
        nop_uplift = max(
            outcome.fx.stressed_nop_pct_tier1 - outcome.fx.base_nop_pct_tier1, _ZERO
        )
        addon = tier1 * nop_uplift / _HUNDRED
        if addon > _ZERO:
            country_fx = thousands(addon)
    if outcome.operational is not None and outcome.operational.pillar2_operational_charge > _ZERO:
        other = thousands(outcome.operational.pillar2_operational_charge)
    if credit_conc is None and irrbb is None and country_fx is None and other is None:
        return None
    requirement = Pillar2Requirement(
        credit_concentration=credit_conc,
        irrbb=irrbb,
        country_and_fx=country_fx,
        other=other,
    )
    return {year: requirement for year in range(1, horizon_years + 1)}


def _credit_exposure_snapshot(exposures: list[CreditExposure]) -> list[dict[str, Any]]:
    """A value-based snapshot of the bottom-up credit book for the input hash."""
    snapshot = [
        {
            "exposure_id": exposure.exposure_id,
            "crd_class": exposure.crd_class,
            "ead": str(exposure.ead),
            "pd_pct": str(exposure.pd_pct),
            "lgd_pct": str(exposure.lgd_pct),
            "risk_weight_pct": str(exposure.risk_weight_pct),
            "is_foreign_currency": exposure.is_foreign_currency,
        }
        for exposure in exposures
    ]
    return sorted(snapshot, key=lambda item: json.dumps(item, sort_keys=True))


# --- Scenario paths + plan ---------------------------------------------------


def _scenario_paths(db: Session, scenario: MacroScenario) -> list[MacroPathPoint]:
    rows = db.scalars(
        select(MacroScenarioPath)
        .where(MacroScenarioPath.scenario_id == scenario.id)
        .order_by(MacroScenarioPath.variable, MacroScenarioPath.year_index)
    )
    return [
        MacroPathPoint(
            variable=row.variable,
            year_index=row.year_index,
            base_value=_dec(row.base_value),
            stress_value=_dec(row.stress_value),
        )
        for row in rows
    ]


def _base_value_at_year1(paths: list[MacroPathPoint], variable: str) -> Decimal | None:
    candidates = [p.base_value for p in paths if p.variable == variable and p.year_index == 1]
    if candidates:
        return candidates[0]
    fallback = [p.base_value for p in paths if p.variable == variable]
    return fallback[0] if fallback else None


def _resolve_plan(
    payload_plan: PlanAssumptionsIn | None, paths: list[MacroPathPoint]
) -> ForecastAssumptions:
    """The base-case business plan: request overrides on top of macro-derived defaults.

    Default nominal loan/deposit growth = base GDP growth + base inflation
    (year-1 base macro); the rest fall back to documented business-as-usual
    constants. The base leg holds these constant across the horizon.
    """
    gdp = _base_value_at_year1(paths, "gdp_growth")
    inflation = _base_value_at_year1(paths, "inflation")
    if gdp is not None and inflation is not None:
        nominal_growth = (gdp + inflation) * _HUNDRED
    else:
        nominal_growth = _DEFAULT_LOAN_GROWTH_PCT
    plan = payload_plan or PlanAssumptionsIn()
    return ForecastAssumptions(
        loan_growth_pct=_first_not_none(plan.loan_growth_pct, nominal_growth),
        deposit_growth_pct=_first_not_none(plan.deposit_growth_pct, nominal_growth),
        nim_pct=_first_not_none(plan.nim_pct, _DEFAULT_NIM_PCT),
        cost_to_income_pct=_first_not_none(plan.cost_to_income_pct, _DEFAULT_COST_TO_INCOME_PCT),
        credit_loss_rate_pct=_first_not_none(
            plan.credit_loss_rate_pct, _DEFAULT_CREDIT_LOSS_RATE_PCT
        ),
        fx_depreciation_pct=_first_not_none(plan.fx_depreciation_pct, _ZERO),
        dividend_payout_pct=_first_not_none(plan.dividend_payout_pct, _DEFAULT_DIVIDEND_PAYOUT_PCT),
        fee_income_pct_assets=_first_not_none(
            plan.fee_income_pct_assets, _DEFAULT_FEE_INCOME_PCT_ASSETS
        ),
        tax_rate_pct=_first_not_none(plan.tax_rate_pct, _DEFAULT_TAX_RATE_PCT),
        securities_shift_pp=_first_not_none(plan.securities_shift_pp, _ZERO),
    )


def _first_not_none(value: Decimal | None, default: Decimal) -> Decimal:
    return default if value is None else value


def _general_provisions(capital_facts: list[CapitalFact]) -> Decimal:
    return sum(
        (
            fact.amount
            for fact in capital_facts
            if fact.capital_tier == TIER_T2
            and fact.category == GENERAL_PROVISIONS_CATEGORY
            and not fact.is_deduction
        ),
        _ZERO,
    )


# --- Input hash (value-based) ------------------------------------------------


def _fact_snapshot(rows: list[BankFinancialFact]) -> list[dict[str, Any]]:
    """A value-based fact snapshot (no ``fact.id``), canonically ordered."""
    snapshot = [
        {
            "fact_group": fact.fact_group,
            "category": fact.category,
            "amount": str(_dec(fact.amount)),
            "risk_weight_code": fact.risk_weight_code,
            "hqla_level": fact.hqla_level,
            "ccf_pct": None if fact.ccf_pct is None else str(_dec(fact.ccf_pct)),
            "rate_pct": None if fact.rate_pct is None else str(_dec(fact.rate_pct)),
            "income_year": fact.income_year,
            "capital_tier": fact.capital_tier,
            "is_deduction": fact.is_deduction,
            "attributes": fact.attributes,
        }
        for fact in rows
    ]
    return sorted(snapshot, key=lambda item: json.dumps(item, sort_keys=True))


def _plan_snapshot(plan: ForecastAssumptions) -> dict[str, str]:
    return {
        "loan_growth_pct": str(plan.loan_growth_pct),
        "deposit_growth_pct": str(plan.deposit_growth_pct),
        "nim_pct": str(plan.nim_pct),
        "cost_to_income_pct": str(plan.cost_to_income_pct),
        "credit_loss_rate_pct": str(plan.credit_loss_rate_pct),
        "fx_depreciation_pct": str(plan.fx_depreciation_pct),
        "dividend_payout_pct": str(plan.dividend_payout_pct),
        "fee_income_pct_assets": str(plan.fee_income_pct_assets),
        "tax_rate_pct": str(plan.tax_rate_pct),
        "securities_shift_pp": str(plan.securities_shift_pp),
    }


def _hash(payload: dict[str, Any]) -> str:
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(text.encode()).hexdigest()


# --- Projection summary serialization ----------------------------------------


def _serialize_projection(projection: EnterpriseProjection) -> dict[str, Any]:
    def year(row: Any) -> dict[str, Any]:
        return {
            "year": row.year,
            "leg": row.leg,
            "car_pct": str(row.ratios.car_pct),
            "cet1_ratio_pct": str(row.ratios.cet1_ratio_pct),
            "tier1_ratio_pct": str(row.ratios.tier1_ratio_pct),
            "leverage_ratio_pct": str(row.ratios.leverage_ratio_pct),
            "lcr_pct": None if row.lcr_pct is None else str(row.lcr_pct),
            "nsfr_pct": None if row.nsfr_pct is None else str(row.nsfr_pct),
            "net_income": str(row.pnl.net_income),
            "credit_losses": str(row.pnl.credit_losses),
            "pd_multiplier": str(row.pd_multiplier),
            "lgd_multiplier": str(row.lgd_multiplier),
            "minima_all_ok": row.minima.all_ok,
            "binding_minima": list(row.minima.binding),
        }

    return {
        "scenario_code": projection.scenario_code,
        "horizon_years": projection.horizon_years,
        "stress_stays_above_all_minima": projection.stress_stays_above_all_minima,
        "first_breach_year": projection.first_breach_year,
        "binding_minima": list(projection.binding_minima),
        "current": year(projection.current),
        "base": [year(row) for row in projection.base],
        "stress": [year(row) for row in projection.stress],
    }


# --- Public entrypoints ------------------------------------------------------


def run_enterprise_stress_test(  # noqa: PLR0915 - one linear orchestration of the run
    db: Session, ctx: TenantContext, bank_id: str, payload: EnterpriseStressRunCreate
) -> EnterpriseStressRead:
    """Run one enterprise stress test and persist it as an immutable run."""
    bank = _get_bank_or_404(db, ctx, bank_id)
    period = _get_period_or_404(db, ctx, bank, payload.reporting_period_id)
    scenario = macro_scenarios.resolve_for_official_run(db, ctx, payload.scenario_id)
    if scenario.bank_id is not None and scenario.bank_id != bank.id:
        raise EnterpriseStressError(
            "scenario_scope_mismatch",
            "The scenario is scoped to a different bank.",
            {"scenario_bank_id": scenario.bank_id, "bank_id": bank.id},
        )
    plan_model = _resolve_action_plan(db, ctx, bank, payload)
    as_of = period.period_end
    paths = _scenario_paths(db, scenario)
    if not paths:
        raise EnterpriseStressError(
            "scenario_without_paths", "The scenario carries no macro-variable paths."
        )
    # Horizon coverage (QA audit 2026-08-20 P0-3): the run schema requires a ≥3-year
    # projection (Stress Testing Guideline ¶75), but the approved scenario must
    # actually stress every projected year. A scenario whose paths stop short of the
    # requested horizon would leave the tail years driven by neutral shocks and mint
    # an "official" 3-year stress that is unstressed at the horizon — reject it.
    max_path_year = max(point.year_index for point in paths)
    if max_path_year < payload.horizon_years:
        raise EnterpriseStressError(
            "scenario_horizon_too_short",
            (
                f"The approved scenario '{scenario.code}' has macro paths through year "
                f"{max_path_year}, but this run projects {payload.horizon_years} years. "
                "Every projected year must be stressed — extend the scenario's paths to "
                "the full horizon before running it (Stress Testing Guideline ¶75)."
            ),
            {"scenario_max_year": max_path_year, "horizon_years": payload.horizon_years},
        )

    capital_rows = _load_facts(db, ctx, bank, period, _CAPITAL_GROUPS)
    liquidity_rows = _load_facts(db, ctx, bank, period, _LIQUIDITY_GROUPS)
    forecast_rows = _load_facts(db, ctx, bank, period, _FORECAST_GROUPS)
    if not capital_rows or not forecast_rows:
        raise EnterpriseStressError(
            "financial_facts_missing", "The reporting period has no financial facts to analyze."
        )

    capital_facts = [_capital_fact(fact) for fact in capital_rows]
    forecast_facts = [_forecast_fact(fact) for fact in forecast_rows]
    capital_params = _capital_params(db, ctx, bank, as_of)
    # The Basel liquidity leg (LCR/NSFR + its params) is a bank-only regime. An SDI
    # run omits it entirely (docs/sdi.md §4.6; QA audit 2026-08-20 P0-1) — do NOT
    # resolve the Basel liquidity thresholds for an SDI, which would fail-loud on the
    # LCR/NSFR params a savings-&-loans tenant has no reason to configure.
    basel_liquidity = capital_params.basel_applicable
    liquidity_facts = [_liquidity_fact(fact) for fact in liquidity_rows] if basel_liquidity else []
    liquidity_params = _liquidity_params(db, ctx, bank, as_of) if basel_liquidity else None
    ecl_exposures, ecl_assumptions = _ecl_inputs(db, ctx, bank, as_of, capital_rows)
    tier1 = tier1_capital(capital_facts)

    # As-of RWA / leverage from the pure capital engine — the concentration
    # Pillar-2 base and the contingent-leverage base exposure.
    base_rwa = compute_rwa(capital_facts, capital_params)
    base_ratios = compute_capital_ratios(capital_facts, base_rwa, capital_params)

    # --- Phase 4: exposure-level per-risk inputs (canonical book; graceful) --
    credit_rows = _load_exposure_rows(db, ctx, bank, as_of, _CREDIT_POSITION_TYPES)
    concentration_rows = _load_exposure_rows(db, ctx, bank, as_of, _CONCENTRATION_POSITION_TYPES)
    funding_rows = _load_exposure_rows(db, ctx, bank, as_of, _FUNDING_POSITION_TYPES)
    derivative_rows = _load_exposure_rows(db, ctx, bank, as_of, _DERIVATIVE_POSITION_TYPES)

    credit_exposures = _build_credit_exposures(credit_rows, capital_params)
    credit_rwa_uplift: dict[int, Decimal] | None = None
    exposure_class_losses: dict[int, dict[str, Decimal]] | None = None
    bottom_up_inputs: BottomUpCreditInputs | None = None
    if credit_exposures:
        credit_rwa_uplift, exposure_class_losses = _credit_overlays(
            credit_exposures, paths, payload.horizon_years
        )
        bottom_up_inputs = BottomUpCreditInputs(exposures=tuple(credit_exposures))
    concentration_inputs = _build_concentration_inputs(
        concentration_rows, funding_rows, base_rwa.credit_rwa
    )
    contingent_leverage_inputs = _build_contingent_leverage_inputs(
        derivative_rows, base_ratios.leverage_exposure, tier1
    )

    plan = _resolve_plan(payload.plan, paths)
    # ForecastParams.liquidity is non-optional, but the enterprise projection reads
    # it ONLY when basel_liquidity is True (docs/sdi.md §4.6 gate in projection.py),
    # so an SDI passes the inert empty sentinel — never evaluated, never a Basel claim.
    forecast_params = ForecastParams(
        liquidity=liquidity_params or _EMPTY_LIQUIDITY_PARAMS, capital=capital_params
    )
    paid_up_min = _resolve_paid_up_min(db, ctx, bank, as_of, payload)

    # 3-year projection (base + stress) → Appendix II tables. The stress leg's
    # credit RWA rises with the bottom-up rating-migration + FX-revaluation
    # uplift (Phase 4), so the stressed capital ratios erode from RWA, not just
    # P&L (closing the Phase-2 limitation).
    try:
        projection = project_enterprise(
            EnterpriseProjectionInputs(
                scenario_code=scenario.code,
                scenario_paths=paths,
                facts=forecast_facts,
                params=forecast_params,
                plan=plan,
                horizon_years=payload.horizon_years,
                paid_up_min=paid_up_min,
                credit_rwa_uplift=credit_rwa_uplift,
                # SDI: exclude Basel LCR/NSFR from the projection (docs/sdi.md §4.6);
                # the SDI liquidity stress is the standalone LMTD Table-1 + ladder.
                basel_liquidity=capital_params.basel_applicable,
            )
        )
    except ProjectionInputError as exc:
        raise EnterpriseStressError(exc.code, exc.message) from exc

    baseline_income, baseline_credit_loss = _baseline_pnl(projection)
    irr_inputs = (
        _irr_inputs(db, ctx, bank, period, as_of, tier1) if payload.include_irr else None
    )
    fx_inputs = _fx_inputs(db, ctx, bank, period, as_of, tier1) if payload.include_fx else None

    outcome = run_enterprise_stress(
        EnterpriseStressInputs(
            scenario_code=scenario.code,
            scenario_paths=paths,
            capital_facts=capital_facts,
            capital_params=capital_params,
            liquidity_facts=liquidity_facts,
            liquidity_params=liquidity_params,
            baseline_annual_preprovision_income=baseline_income,
            baseline_annual_credit_loss=baseline_credit_loss,
            baseline_credit_allowance=_general_provisions(capital_facts),
            tax_rate_pct=plan.tax_rate_pct,
            ecl_exposures=ecl_exposures,
            ecl_assumptions=ecl_assumptions,
            irr=irr_inputs,
            fx=fx_inputs,
            bottom_up_credit=bottom_up_inputs,
            concentration=concentration_inputs,
            operational=OperationalConfig(annual_gross_income=max(baseline_income, _ZERO)),
            contingent_leverage=contingent_leverage_inputs,
            basel_liquidity=basel_liquidity,
        )
    )
    # Phase 3: overlay an APPROVED management-actions plan onto the stress leg to
    # produce the "results WITH management actions" (¶67(f), ¶78–81). None when the
    # run models no plan — Table 1's action blocks then stay empty (pre-action).
    management_result: ManagementActionsResult | None = None
    if plan_model is not None:
        domain_plan = management_action_plans.build_domain_plan(db, plan_model)
        management_result = apply_management_actions(
            projection,
            domain_plan,
            severity=scenario.severity,
            capital_params=capital_params,
            paid_up_min=paid_up_min,
            car_target_pct=payload.car_target_pct,
        )

    appendix = build_appendix_ii(
        projection,
        paths,
        car_target_pct=payload.car_target_pct,
        paid_up_min=paid_up_min,
        source=scenario.source,
        exposure_class_losses=exposure_class_losses,
        pillar2_by_stress_year=_pillar2_overlay(outcome, tier1, payload.horizon_years),
        management_actions=management_result,
        # SDI: omit the Basel Table-2 3-tier capital build (docs/sdi.md §4.6).
        basel_applicable=capital_params.basel_applicable,
    )

    inputs = {
        "schema_version": INPUT_SCHEMA_VERSION,
        "module": MODULE_ENTERPRISE_STRESS,
        "bank_id": str(bank.id),
        "reporting_period_id": str(period.id),
        "as_of_date": as_of.isoformat(),
        "scenario": {
            "id": str(scenario.id),
            "code": scenario.code,
            "version": scenario.version,
            "paths": [
                {
                    "variable": point.variable,
                    "year_index": point.year_index,
                    "base_value": str(point.base_value),
                    "stress_value": str(point.stress_value),
                }
                for point in sorted(paths, key=lambda p: (p.variable, p.year_index))
            ],
        },
        "plan": _plan_snapshot(plan),
        "horizon_years": payload.horizon_years,
        "paid_up_min": str(paid_up_min),
        "car_target_pct": str(payload.car_target_pct),
        "include_irr": payload.include_irr,
        "include_fx": payload.include_fx,
        "capital_facts": _fact_snapshot(capital_rows),
        "liquidity_facts": _fact_snapshot(liquidity_rows),
        "irr_evaluated": irr_inputs is not None,
        "fx_evaluated": fx_inputs is not None,
        # Phase 4 exposure-level evidence (empty when no canonical book) — the
        # bottom-up credit exposures materially drive the projected RWA, so they
        # belong in the value-based reproducibility hash.
        "credit_exposures": _credit_exposure_snapshot(credit_exposures),
        "credit_migration_evaluated": bottom_up_inputs is not None,
        "concentration_evaluated": concentration_inputs is not None,
        "contingent_leverage_evaluated": contingent_leverage_inputs is not None,
        # Phase 3: the management-actions plan materially drives the WITH-actions
        # projection, so its value-based snapshot belongs in the reproducibility
        # hash (absent ⇒ the pre-action run's hash is unchanged).
        "management_action_plan": (
            None if plan_model is None else management_action_plans.plan_snapshot(db, plan_model)
        ),
    }
    input_hash = _hash(inputs)

    outcome_json = outcome.serialize()
    projection_json = _serialize_projection(projection)
    appendix_json = appendix.serialize()
    management_json = None if management_result is None else management_result.serialize()
    metrics = {
        "outcome": outcome_json,
        "projection": projection_json,
        "appendix_ii": appendix_json,
        "management_actions": management_json,
    }
    run = RegulatoryRun(
        organization_id=ctx.organization_id,
        bank_id=bank.id,
        reporting_period_id=period.id,
        module=MODULE_ENTERPRISE_STRESS,
        scenario_code=scenario.code,
        status="succeeded",
        engine_version=ENGINE_VERSION,
        input_schema_version=INPUT_SCHEMA_VERSION,
        output_schema_version=OUTPUT_SCHEMA_VERSION,
        input_hash=input_hash,
        inputs=inputs,
        metrics=metrics,
        created_by=ctx.actor_user_id,
        started_at=datetime.now(UTC),
        completed_at=datetime.now(UTC),
    )
    db.add(run)
    db.flush()
    record_event(
        db,
        ctx,
        event_type="enterprise_stress.completed",
        entity_type="regulatory_run",
        entity_id=run.id,
        details={
            "bank_id": str(bank.id),
            "reporting_period_id": str(period.id),
            "scenario_id": str(scenario.id),
            "scenario_code": scenario.code,
            "input_hash": input_hash,
            "car_erosion_pp": outcome_json["capital"]["car_erosion_pp"],  # type: ignore[index]
            "stress_stays_above_all_minima": projection.stress_stays_above_all_minima,
            "management_action_plan_id": (
                None if plan_model is None else str(plan_model.id)
            ),
            "with_actions_stays_above_all_minima": (
                None if management_result is None else management_result.stays_above_all_minima
            ),
            "reason": payload.reason,
        },
    )
    db.commit()
    return _read(run, scenario)


def get_latest_enterprise_stress(
    db: Session, ctx: TenantContext, bank_id: str, reporting_period_id: UUID, scenario_id: UUID
) -> EnterpriseStressRead:
    bank = _get_bank_or_404(db, ctx, bank_id)
    scenario = db.scalar(
        select(MacroScenario).where(
            MacroScenario.id == scenario_id,
            MacroScenario.organization_id == ctx.organization_id,
        )
    )
    if scenario is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Macro scenario not found."
        )
    run = db.scalar(
        select(RegulatoryRun)
        .where(
            RegulatoryRun.organization_id == ctx.organization_id,
            RegulatoryRun.bank_id == bank.id,
            RegulatoryRun.reporting_period_id == reporting_period_id,
            RegulatoryRun.module == MODULE_ENTERPRISE_STRESS,
            RegulatoryRun.scenario_code == scenario.code,
            RegulatoryRun.status == "succeeded",
        )
        .order_by(RegulatoryRun.created_at.desc())
        .limit(1)
    )
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No enterprise stress run exists for this period and scenario.",
        )
    return _read(run, scenario)


def _run_summary(run: RegulatoryRun) -> EnterpriseStressRunSummary:
    """A history row from immutable-run fields + persisted metrics only.

    No re-computation and no scenario fetch — the registry lists every run and
    re-opens one by id through ``get_enterprise_stress_run``.
    """
    metrics = run.metrics
    capital = metrics["outcome"]["capital"]
    projection = metrics["projection"]
    appendix = metrics["appendix_ii"]
    management = metrics.get("management_actions")
    return EnterpriseStressRunSummary(
        run_id=run.id,
        reporting_period_id=run.reporting_period_id,
        scenario_code=run.scenario_code or "",
        status=run.status,
        input_hash=run.input_hash,
        engine_version=run.engine_version,
        stressed_car_end_pct=_dec(capital["stressed_car_end_pct"]),
        baseline_car_end_pct=_dec(capital["baseline_car_end_pct"]),
        car_erosion_pp=_dec(capital["car_erosion_pp"]),
        stress_stays_above_all_minima=bool(projection["stress_stays_above_all_minima"]),
        first_breach_year=projection["first_breach_year"],
        capital_gap=_dec(appendix["table1_summary"]["capital_gap"]),
        management_action_plan_code=(None if management is None else management["plan_id"]),
        with_actions_stays_above_all_minima=(
            None if management is None else bool(management["stays_above_all_minima"])
        ),
        created_at=run.created_at,
    )


def list_enterprise_stress_runs(
    db: Session,
    ctx: TenantContext,
    bank_id: str,
    reporting_period_id: UUID | None = None,
) -> list[EnterpriseStressRunSummary]:
    """The bank's enterprise-stress run history, newest first (docs/stress.md §4.5).

    Optionally scoped to one reporting period. RLS-scoped; succeeded runs only
    (a failed attempt carries no Appendix II payload to summarise).
    """
    bank = _get_bank_or_404(db, ctx, bank_id)
    stmt = (
        select(RegulatoryRun)
        .where(
            RegulatoryRun.organization_id == ctx.organization_id,
            RegulatoryRun.bank_id == bank.id,
            RegulatoryRun.module == MODULE_ENTERPRISE_STRESS,
            RegulatoryRun.status == "succeeded",
        )
        .order_by(RegulatoryRun.created_at.desc())
    )
    if reporting_period_id is not None:
        stmt = stmt.where(RegulatoryRun.reporting_period_id == reporting_period_id)
    return [_run_summary(run) for run in db.scalars(stmt)]


def get_enterprise_stress_run(
    db: Session, ctx: TenantContext, bank_id: str, run_id: UUID
) -> EnterpriseStressRead:
    """Re-open one immutable enterprise-stress run by id (docs/stress.md §4.5).

    The scenario is resolved from the run's own value-based input snapshot
    (``inputs.scenario.id``) so the re-opened view is faithful to the run even if
    the scenario has since been re-versioned or archived.
    """
    bank = _get_bank_or_404(db, ctx, bank_id)
    run = db.scalar(
        select(RegulatoryRun).where(
            RegulatoryRun.id == run_id,
            RegulatoryRun.organization_id == ctx.organization_id,
            RegulatoryRun.bank_id == bank.id,
            RegulatoryRun.module == MODULE_ENTERPRISE_STRESS,
        )
    )
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Enterprise stress run not found."
        )
    scenario_id = UUID(str(run.inputs["scenario"]["id"]))
    scenario = db.scalar(
        select(MacroScenario).where(
            MacroScenario.id == scenario_id,
            MacroScenario.organization_id == ctx.organization_id,
        )
    )
    if scenario is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="The macro scenario for this run no longer exists.",
        )
    return _read(run, scenario)


def _resolve_action_plan(
    db: Session,
    ctx: TenantContext,
    bank: Bank,
    payload: EnterpriseStressRunCreate,
) -> ManagementActionPlan | None:
    """Resolve an APPROVED management-actions plan for the run, or ``None``.

    The approval gate mirrors the macro-scenario one: a draft / pending /
    archived plan can never mint an immutable run. A bank-scoped plan must match
    the bank under stress.
    """
    if payload.management_action_plan_id is None:
        return None
    plan = management_action_plans.resolve_for_official_run(
        db, ctx, payload.management_action_plan_id
    )
    if plan.bank_id is not None and plan.bank_id != bank.id:
        raise EnterpriseStressError(
            "plan_scope_mismatch",
            "The management-actions plan is scoped to a different bank.",
            {"plan_bank_id": plan.bank_id, "bank_id": bank.id},
        )
    return plan


#: Control-plane ``paid_up_min`` is licence-specific and expressed in GHS millions
#: (mirror of ``sdi_capital_checks._GHS_PER_MILLION``); the stress engine works in
#: absolute GHS, so a resolved value is scaled up by this factor.
_GHS_PER_MILLION = Decimal("1000000")


def _resolve_paid_up_min(
    db: Session,
    ctx: TenantContext,
    bank: Bank,
    as_of: date,
    payload: EnterpriseStressRunCreate,
) -> Decimal:
    """Resolve the paid-up-capital minimum for the stress minima/gap and management-
    action sizing. Precedence: explicit run override → tenant board register → (SDI)
    the regulatory-parameter control plane → 0.

    For an ``sdi`` the control-plane fallback is REQUIRED — otherwise a savings-&-loans
    run with no board register row silently used ``0`` and dropped the GH¢15m s.29
    paid-up floor from the Appendix II minima (QA audit 2026-08-20 P0-2). Banks keep
    the historical payload→register→0 path byte-identical: a bank's paid-up minimum is
    enforced through its BSD capital return, and the enterprise-stress goldens were
    written against the 0 fallback — moving them is a separate, deliberate decision."""
    if payload.paid_up_min is not None:
        return payload.paid_up_min
    thresholds = {
        row.threshold_code: _dec(row.value_pct)
        for row in get_active_params(
            db, ctx.organization_id, bank.jurisdiction_code, ParamCapitalThreshold, as_of
        )
    }
    register_value = thresholds.get("paid_up_min")
    if register_value is not None:
        return register_value
    if institution_types.institution_class(db, bank) == "sdi":
        param = regulatory_parameters.resolve(db, bank, "paid_up_min", as_of=as_of)
        return param.decimal * _GHS_PER_MILLION
    return _ZERO


def _baseline_pnl(projection: EnterpriseProjection) -> tuple[Decimal, Decimal]:
    """(pre-provision operating income, through-the-cycle credit loss) from base Y1."""
    if not projection.base:
        return _ZERO, _ZERO
    base_year1 = projection.base[0]
    preprovision = base_year1.pnl.total_income - base_year1.pnl.opex
    return preprovision, base_year1.pnl.credit_losses


def _read(run: RegulatoryRun, scenario: MacroScenario) -> EnterpriseStressRead:
    metrics = run.metrics
    outcome = metrics["outcome"]
    projection = metrics["projection"]
    appendix = metrics["appendix_ii"]
    capital = outcome["capital"]
    liquidity = outcome["liquidity"]
    # SDI runs carry a not-assessed liquidity marker and no coupling block (§4.6).
    coupling = outcome.get("coupling")
    liquidity_assessed = "stressed_lcr_pct" in liquidity
    management = metrics.get("management_actions")
    summary = EnterpriseStressSummary(
        scenario_code=scenario.code,
        stressed_car_end_pct=_dec(capital["stressed_car_end_pct"]),
        baseline_car_end_pct=_dec(capital["baseline_car_end_pct"]),
        car_erosion_pp=_dec(capital["car_erosion_pp"]),
        stressed_lcr_pct=_dec(liquidity["stressed_lcr_pct"]) if liquidity_assessed else None,
        baseline_lcr_pct=_dec(liquidity["baseline_lcr_pct"]) if liquidity_assessed else None,
        both_breached=(bool(coupling["both_breached"]) if coupling is not None else None),
        stress_stays_above_all_minima=bool(projection["stress_stays_above_all_minima"]),
        first_breach_year=projection["first_breach_year"],
        binding_minima=list(projection["binding_minima"]),
        capital_gap=_dec(appendix["table1_summary"]["capital_gap"]),
        management_action_plan_code=(None if management is None else management["plan_id"]),
        with_actions_stays_above_all_minima=(
            None if management is None else bool(management["stays_above_all_minima"])
        ),
        with_actions_first_breach_year=(
            None if management is None else management["first_breach_year"]
        ),
        residual_capital_required_after_actions=(
            None if management is None else _dec(management["residual_capital_required"])
        ),
    )
    return EnterpriseStressRead(
        run_id=run.id,
        bank_id=run.bank_id,
        reporting_period_id=run.reporting_period_id,
        scenario_id=scenario.id,
        scenario_code=scenario.code,
        input_hash=run.input_hash,
        engine_version=run.engine_version,
        summary=summary,
        outcome=outcome,
        projection=projection,
        appendix_ii=appendix,
        created_at=run.created_at,
    )
