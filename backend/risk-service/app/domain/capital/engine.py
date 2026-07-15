"""Pure regulatory capital engine (Basel RWA, capital ratios, quarterly stress).

Every function here is deterministic, Decimal-only, and free of database or
tenant concerns: callers supply the bank facts and the active parameter set and
receive fully materialized results with per-category line items. Monetary
amounts quantize to ``MONEY`` (4 dp) and ratio percentages quantize to
``RATIO_PCT`` (6 dp) with ``ROUND_HALF_UP``; status classification always
happens AFTER quantization so stored and displayed values agree.

Methodology notes:

- Credit RWA (standardized approach) covers the full balance sheet: granular
  ``loan_exposure`` facts at their coded risk weights, the ``other_assets``
  balance-sheet row at RW100, and ``off_balance`` facts converted to an EAD via
  their CCF before weighting. Cash, BoG reserves, and sovereign securities are
  RW0; one summary line each (``bog_bills``, ``gog_bonds``,
  ``cash_and_reserves`` — the latter aggregating the vault-cash and BoG reserve
  balance-sheet rows) is emitted with a zero weighted amount for transparency.
- Market RWA charges ``fx_charge_pct`` of the larger open FX position and
  converts the charge to RWA with the 12.5x multiplier (``rwa_multiplier_pct``
  expressed as a percent, 1250).
- Operational RWA follows the Basic Indicator Approach: alpha times the average
  of the POSITIVE gross-income years, multiplying before dividing so exact
  Decimal results survive (1120 x 15% / 3 = 56 exactly).
- The Tier 2 general-provisions component is capped at
  ``tier2_gp_cap_pct_credit_rwa`` percent of credit RWA; the cap is re-applied
  against every stressed quarter's credit RWA.
- The 12-month stress path holds AT1 and operational RWA constant, grows credit
  RWA geometrically, applies the FX RWA multiplier from Q1 onward, and evolves
  CET1 by retained quarterly income net of credit losses (dividends are zero
  under stress). The leverage exposure has no shock of its own, so it grows at
  the same rate as credit RWA.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

MONEY = Decimal("0.0001")
RATIO_PCT = Decimal("0.000001")
MILLION = Decimal("1000000")
_HUNDRED = Decimal("100")
_ZERO = Decimal("0")
_ONE = Decimal("1")

# A ratio is green only when it clears the regulatory minimum by this buffer.
GREEN_BUFFER_PP = Decimal("0.5")
STRESS_QUARTERS = 4

type CapitalStatus = Literal["green", "amber", "red"]
type CapitalLineSection = Literal[
    "credit_rwa", "market_rwa", "operational_rwa", "capital_component", "ratio"
]
type CapitalTriggerCode = Literal["early_warning", "breach", "critical"]

FACT_GROUP_BALANCE_SHEET = "balance_sheet"
FACT_GROUP_LOAN_EXPOSURE = "loan_exposure"
FACT_GROUP_OFF_BALANCE = "off_balance"
FACT_GROUP_MARKET_RISK = "market_risk"
FACT_GROUP_OPERATIONAL_INCOME = "operational_income"
FACT_GROUP_CAPITAL_COMPONENT = "capital_component"

OTHER_ASSETS_CATEGORY = "other_assets"
OTHER_ASSETS_RISK_WEIGHT_CODE = "RW100"
ZERO_RISK_WEIGHT_CODE = "RW0"
NET_LONG_FX_CATEGORY = "net_long_fx"
NET_SHORT_FX_CATEGORY = "net_short_fx"
GENERAL_PROVISIONS_CATEGORY = "general_provisions"
TIER_CET1 = "CET1"
TIER_AT1 = "AT1"
TIER_T2 = "T2"
_TIER_ORDER = {TIER_CET1: 0, TIER_AT1: 1, TIER_T2: 2}

# Zero-weight balance-sheet transparency rows: (line_code, description,
# contributing balance-sheet categories).
_ZERO_WEIGHT_SUMMARY_LINES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("bog_bills", "BoG Bills", ("securities_bog_bills",)),
    ("gog_bonds", "GoG Bonds", ("securities_gog_bonds",)),
    (
        "cash_and_reserves",
        "Cash And Reserves",
        ("cash_vault", "bog_required_reserves", "bog_excess_reserves"),
    ),
)

SHOCK_QUARTERLY_RWA_GROWTH_PCT = "quarterly_rwa_growth_pct"
SHOCK_QUARTERLY_INCOME_M = "quarterly_income_m"
SHOCK_QUARTERLY_CREDIT_LOSS_M = "quarterly_credit_loss_m"
SHOCK_FX_RWA_MULTIPLIER = "fx_rwa_multiplier"
REQUIRED_STRESS_SHOCK_KEYS = (
    SHOCK_QUARTERLY_RWA_GROWTH_PCT,
    SHOCK_QUARTERLY_INCOME_M,
    SHOCK_QUARTERLY_CREDIT_LOSS_M,
    SHOCK_FX_RWA_MULTIPLIER,
)

TRIGGER_EARLY_WARNING: CapitalTriggerCode = "early_warning"
TRIGGER_BREACH: CapitalTriggerCode = "breach"
TRIGGER_CRITICAL: CapitalTriggerCode = "critical"
TRIGGER_ACTIONS: dict[CapitalTriggerCode, str] = {
    TRIGGER_EARLY_WARNING: (
        "Suspend variable compensation and halt non-essential capital expenditure."
    ),
    TRIGGER_BREACH: (
        "Halt dividend distributions, reduce RWA via portfolio sale, and activate the "
        "Tier 2 issuance plan."
    ),
    TRIGGER_CRITICAL: "Notify Bank of Ghana and initiate the underwritten emergency rights issue.",
}


class MissingParameterError(Exception):
    """A fact requires a rate/weight/shock that the active parameter set lacks."""

    def __init__(self, name: str) -> None:
        super().__init__(f"No active capital parameter covers '{name}'.")
        self.name = name


class UnsupportedShockError(Exception):
    """A stress scenario carries a shock key the engine does not understand."""

    def __init__(self, scenario_code: str, shock_key: str) -> None:
        super().__init__(
            f"Stress scenario '{scenario_code}' carries unsupported shock key '{shock_key}'."
        )
        self.scenario_code = scenario_code
        self.shock_key = shock_key


class CapitalComputationError(Exception):
    """The supplied facts produce a degenerate ratio (zero denominator)."""


@dataclass(frozen=True)
class CapitalFact:
    """One bank financial fact, reduced to the fields the capital engine uses."""

    fact_group: str
    category: str
    amount: Decimal
    risk_weight_code: str | None = None
    ccf_pct: Decimal | None = None
    income_year: int | None = None
    capital_tier: str | None = None
    is_deduction: bool = False
    side: str | None = None


@dataclass(frozen=True)
class CapitalParams:
    """Active parameter set resolved as of the reporting-period end."""

    risk_weights: Mapping[str, Decimal]
    bia_alpha_pct: Decimal
    fx_charge_pct: Decimal
    rwa_multiplier_pct: Decimal
    tier2_gp_cap_pct_credit_rwa: Decimal
    cet1_min_pct: Decimal
    tier1_min_pct: Decimal
    car_min_pct: Decimal
    leverage_min_pct: Decimal
    car_early_warning_pct: Decimal
    car_critical_pct: Decimal


@dataclass(frozen=True)
class CapitalLineItem:
    section: CapitalLineSection
    line_code: str
    description: str
    exposure_amount: Decimal | None
    rate_pct: Decimal | None
    weighted_amount: Decimal


@dataclass(frozen=True)
class RwaResult:
    credit_rwa: Decimal
    market_rwa: Decimal
    operational_rwa: Decimal
    total_rwa: Decimal
    fx_net_long: Decimal
    fx_net_short: Decimal
    fx_charge: Decimal
    gross_income_positive_total: Decimal
    positive_income_years: int
    bia_charge: Decimal
    line_items: tuple[CapitalLineItem, ...]


@dataclass(frozen=True)
class CapitalRatiosResult:
    cet1_capital: Decimal
    at1_capital: Decimal
    tier1_capital: Decimal
    tier2_capital: Decimal
    total_capital: Decimal
    general_provisions_amount: Decimal
    general_provisions_cap: Decimal
    gp_cap_applied: bool
    leverage_exposure: Decimal
    cet1_ratio_pct: Decimal
    tier1_ratio_pct: Decimal
    car_pct: Decimal
    leverage_ratio_pct: Decimal
    cet1_status: CapitalStatus
    tier1_status: CapitalStatus
    car_status: CapitalStatus
    leverage_status: CapitalStatus
    line_items: tuple[CapitalLineItem, ...]


@dataclass(frozen=True)
class CapitalStressQuarter:
    quarter: int
    cet1_capital: Decimal
    tier1_capital: Decimal
    total_capital: Decimal
    credit_rwa: Decimal
    market_rwa: Decimal
    operational_rwa: Decimal
    total_rwa: Decimal
    cet1_ratio: Decimal
    tier1_ratio: Decimal
    car: Decimal
    leverage_ratio: Decimal


@dataclass(frozen=True)
class CapitalStressTrigger:
    code: CapitalTriggerCode
    threshold_pct: Decimal
    fired: bool
    first_quarter: int | None
    action: str


@dataclass(frozen=True)
class CapitalStressResult:
    scenario_code: str
    rwa: RwaResult
    ratios: CapitalRatiosResult
    path: tuple[CapitalStressQuarter, ...]
    triggers: tuple[CapitalStressTrigger, ...]


def money(value: Decimal) -> Decimal:
    return value.quantize(MONEY, rounding=ROUND_HALF_UP)


def ratio_pct(value: Decimal) -> Decimal:
    return value.quantize(RATIO_PCT, rounding=ROUND_HALF_UP)


def classify_capital_ratio(value_pct: Decimal, minimum_pct: Decimal) -> CapitalStatus:
    """Classify an already-quantized ratio percentage against its minimum.

    Green requires clearing the minimum by ``GREEN_BUFFER_PP`` (0.5pp), which
    makes the CAR green floor exactly the ``car_early_warning`` threshold.
    """
    if value_pct >= minimum_pct + GREEN_BUFFER_PP:
        return "green"
    if value_pct >= minimum_pct:
        return "amber"
    return "red"


def tier1_capital(facts: Sequence[CapitalFact]) -> Decimal:
    """Tier 1 capital (CET1 + AT1) from the capital-component facts.

    Deductions subtract; general provisions and other Tier 2 items are ignored.
    Reused by non-capital engines (e.g. IRRBB) that need Tier 1 as the
    denominator for a supervisory limit without re-running the full RWA build.
    """
    components = [fact for fact in facts if fact.fact_group == FACT_GROUP_CAPITAL_COMPONENT]
    return money(_tier_total(components, TIER_CET1) + _tier_total(components, TIER_AT1))


def compute_rwa(facts: Sequence[CapitalFact], params: CapitalParams) -> RwaResult:
    credit_items = _credit_line_items(facts, params)
    credit_rwa = money(sum((item.weighted_amount for item in credit_items), _ZERO))

    net_long = money(_facts_total(facts, FACT_GROUP_MARKET_RISK, NET_LONG_FX_CATEGORY))
    net_short = money(_facts_total(facts, FACT_GROUP_MARKET_RISK, NET_SHORT_FX_CATEGORY))
    open_position = max(net_long, net_short)
    fx_charge = money(open_position * params.fx_charge_pct / _HUNDRED)
    market_rwa = money(fx_charge * params.rwa_multiplier_pct / _HUNDRED)
    market_items = (
        CapitalLineItem(
            "market_rwa", "net_long_fx", "Net Long FX Position", net_long, None, net_long
        ),
        CapitalLineItem(
            "market_rwa", "net_short_fx", "Net Short FX Position", net_short, None, net_short
        ),
        CapitalLineItem(
            "market_rwa",
            "fx_charge",
            "FX Capital Charge (Larger Open Position x Charge Rate)",
            open_position,
            params.fx_charge_pct,
            fx_charge,
        ),
        CapitalLineItem(
            "market_rwa",
            "fx_rwa",
            "Market Risk RWA (FX Charge x RWA Multiplier)",
            fx_charge,
            None,
            market_rwa,
        ),
    )

    income_facts = sorted(
        (
            fact
            for fact in facts
            if fact.fact_group == FACT_GROUP_OPERATIONAL_INCOME and fact.income_year is not None
        ),
        key=lambda fact: (fact.income_year or 0, fact.category),
    )
    positive = [fact for fact in income_facts if fact.amount > _ZERO]
    if not positive:
        raise CapitalComputationError(
            "At least one positive gross-income year is required for the BIA charge."
        )
    gross_income_total = money(sum((fact.amount for fact in positive), _ZERO))
    # Multiply before dividing so exact Decimal results survive (1120 x 15 / 300 = 56).
    bia_charge = money(
        gross_income_total * params.bia_alpha_pct / (_HUNDRED * Decimal(len(positive)))
    )
    operational_rwa = money(bia_charge * params.rwa_multiplier_pct / _HUNDRED)
    operational_items = (
        *(
            CapitalLineItem(
                "operational_rwa",
                fact.category,
                f"Gross Income {fact.income_year}"
                + ("" if fact.amount > _ZERO else " (Excluded: Non-Positive)"),
                money(fact.amount),
                None,
                money(fact.amount) if fact.amount > _ZERO else _ZERO,
            )
            for fact in income_facts
        ),
        CapitalLineItem(
            "operational_rwa",
            "bia_charge",
            f"BIA Capital Charge (Alpha on {len(positive)}-Year Average Gross Income)",
            gross_income_total,
            params.bia_alpha_pct,
            bia_charge,
        ),
        CapitalLineItem(
            "operational_rwa",
            "operational_rwa",
            "Operational Risk RWA (BIA Charge x RWA Multiplier)",
            bia_charge,
            None,
            operational_rwa,
        ),
    )

    return RwaResult(
        credit_rwa=credit_rwa,
        market_rwa=market_rwa,
        operational_rwa=operational_rwa,
        total_rwa=money(credit_rwa + market_rwa + operational_rwa),
        fx_net_long=net_long,
        fx_net_short=net_short,
        fx_charge=fx_charge,
        gross_income_positive_total=gross_income_total,
        positive_income_years=len(positive),
        bia_charge=bia_charge,
        line_items=(*credit_items, *market_items, *operational_items),
    )


def compute_capital_ratios(
    facts: Sequence[CapitalFact], rwa: RwaResult, params: CapitalParams
) -> CapitalRatiosResult:
    components = sorted(
        (fact for fact in facts if fact.fact_group == FACT_GROUP_CAPITAL_COMPONENT),
        key=lambda fact: (
            _TIER_ORDER.get(fact.capital_tier or "", 99),
            fact.is_deduction,
            fact.category,
        ),
    )
    gp_amount = money(
        sum(
            (
                fact.amount
                for fact in components
                if fact.capital_tier == TIER_T2
                and fact.category == GENERAL_PROVISIONS_CATEGORY
                and not fact.is_deduction
            ),
            _ZERO,
        )
    )
    gp_cap = money(rwa.credit_rwa * params.tier2_gp_cap_pct_credit_rwa / _HUNDRED)
    gp_included = min(gp_amount, gp_cap)
    gp_cap_applied = gp_amount > gp_cap

    cet1 = money(_tier_total(components, TIER_CET1))
    at1 = money(_tier_total(components, TIER_AT1))
    tier2_other = money(
        _tier_total(
            [
                fact
                for fact in components
                if not (
                    fact.capital_tier == TIER_T2
                    and fact.category == GENERAL_PROVISIONS_CATEGORY
                    and not fact.is_deduction
                )
            ],
            TIER_T2,
        )
    )
    tier2 = money(tier2_other + gp_included)
    tier1 = money(cet1 + at1)
    total_capital = money(tier1 + tier2)

    leverage_exposure = money(_leverage_exposure(facts))
    if rwa.total_rwa <= _ZERO:
        raise CapitalComputationError(
            "Total risk-weighted assets must be positive to compute capital ratios."
        )
    if leverage_exposure <= _ZERO:
        raise CapitalComputationError(
            "The total leverage exposure must be positive to compute the leverage ratio."
        )

    cet1_ratio = ratio_pct(cet1 / rwa.total_rwa * _HUNDRED)
    tier1_ratio = ratio_pct(tier1 / rwa.total_rwa * _HUNDRED)
    car = ratio_pct(total_capital / rwa.total_rwa * _HUNDRED)
    leverage_ratio = ratio_pct(tier1 / leverage_exposure * _HUNDRED)

    component_items = tuple(_component_line_item(fact, gp_included) for fact in components)
    # Ratio lines are stored as denominator (exposure) x ratio (rate) = numerator (weighted).
    ratio_items = (
        _ratio_line_item("cet1_ratio", "CET1 Capital Ratio", rwa.total_rwa, cet1_ratio, cet1),
        _ratio_line_item("tier1_ratio", "Tier 1 Capital Ratio", rwa.total_rwa, tier1_ratio, tier1),
        _ratio_line_item("car", "Capital Adequacy Ratio", rwa.total_rwa, car, total_capital),
        _ratio_line_item(
            "leverage_ratio", "Leverage Ratio", leverage_exposure, leverage_ratio, tier1
        ),
    )
    return CapitalRatiosResult(
        cet1_capital=cet1,
        at1_capital=at1,
        tier1_capital=tier1,
        tier2_capital=tier2,
        total_capital=total_capital,
        general_provisions_amount=gp_amount,
        general_provisions_cap=gp_cap,
        gp_cap_applied=gp_cap_applied,
        leverage_exposure=leverage_exposure,
        cet1_ratio_pct=cet1_ratio,
        tier1_ratio_pct=tier1_ratio,
        car_pct=car,
        leverage_ratio_pct=leverage_ratio,
        cet1_status=classify_capital_ratio(cet1_ratio, params.cet1_min_pct),
        tier1_status=classify_capital_ratio(tier1_ratio, params.tier1_min_pct),
        car_status=classify_capital_ratio(car, params.car_min_pct),
        leverage_status=classify_capital_ratio(leverage_ratio, params.leverage_min_pct),
        line_items=(*component_items, *ratio_items),
    )


def run_capital_stress(
    scenario_code: str,
    facts: Sequence[CapitalFact],
    params: CapitalParams,
    shocks: Mapping[str, Decimal],
) -> CapitalStressResult:
    """Project the four-quarter capital path for one stress scenario.

    Q0 is the unstressed as-of position. For q >= 1: CET1 evolves by quarterly
    income net of credit losses (dividends zero under stress), AT1 and
    operational RWA stay constant, credit RWA compounds at the quarterly growth
    rate, market RWA is scaled by the FX multiplier from Q1 onward, the Tier 2
    general-provisions cap is re-applied against each quarter's credit RWA, and
    the leverage exposure grows at the same rate as credit RWA (it has no shock
    of its own).
    """
    for shock_key in shocks:
        if shock_key not in REQUIRED_STRESS_SHOCK_KEYS:
            raise UnsupportedShockError(scenario_code, shock_key)
    for shock_key in REQUIRED_STRESS_SHOCK_KEYS:
        if shock_key not in shocks:
            raise MissingParameterError(f"stress_shock:{scenario_code}:{shock_key}")

    rwa = compute_rwa(facts, params)
    ratios = compute_capital_ratios(facts, rwa, params)
    growth_factor = _ONE + shocks[SHOCK_QUARTERLY_RWA_GROWTH_PCT] / _HUNDRED
    quarterly_retention = (
        shocks[SHOCK_QUARTERLY_INCOME_M] - shocks[SHOCK_QUARTERLY_CREDIT_LOSS_M]
    ) * MILLION
    fx_multiplier = shocks[SHOCK_FX_RWA_MULTIPLIER]
    tier2_other = money(
        ratios.tier2_capital - min(ratios.general_provisions_amount, ratios.general_provisions_cap)
    )

    path: list[CapitalStressQuarter] = []
    for quarter in range(STRESS_QUARTERS + 1):
        cet1 = money(ratios.cet1_capital + Decimal(quarter) * quarterly_retention)
        credit_rwa = money(rwa.credit_rwa * growth_factor**quarter)
        market_rwa = money(rwa.market_rwa * fx_multiplier) if quarter >= 1 else rwa.market_rwa
        gp_cap = money(credit_rwa * params.tier2_gp_cap_pct_credit_rwa / _HUNDRED)
        tier2 = money(tier2_other + min(ratios.general_provisions_amount, gp_cap))
        tier1 = money(cet1 + ratios.at1_capital)
        total_capital = money(tier1 + tier2)
        total_rwa = money(credit_rwa + market_rwa + rwa.operational_rwa)
        leverage_exposure = money(ratios.leverage_exposure * growth_factor**quarter)
        if total_rwa <= _ZERO or leverage_exposure <= _ZERO:
            raise CapitalComputationError(
                "Stressed risk-weighted assets and leverage exposure must remain positive."
            )
        path.append(
            CapitalStressQuarter(
                quarter=quarter,
                cet1_capital=cet1,
                tier1_capital=tier1,
                total_capital=total_capital,
                credit_rwa=credit_rwa,
                market_rwa=market_rwa,
                operational_rwa=rwa.operational_rwa,
                total_rwa=total_rwa,
                cet1_ratio=ratio_pct(cet1 / total_rwa * _HUNDRED),
                tier1_ratio=ratio_pct(tier1 / total_rwa * _HUNDRED),
                car=ratio_pct(total_capital / total_rwa * _HUNDRED),
                leverage_ratio=ratio_pct(tier1 / leverage_exposure * _HUNDRED),
            )
        )

    trigger_thresholds: tuple[tuple[CapitalTriggerCode, Decimal], ...] = (
        (TRIGGER_EARLY_WARNING, params.car_early_warning_pct),
        (TRIGGER_BREACH, params.car_min_pct),
        (TRIGGER_CRITICAL, params.car_critical_pct),
    )
    triggers = tuple(
        _evaluate_trigger(code, threshold, path) for code, threshold in trigger_thresholds
    )
    return CapitalStressResult(
        scenario_code=scenario_code,
        rwa=rwa,
        ratios=ratios,
        path=tuple(path),
        triggers=triggers,
    )


def _evaluate_trigger(
    code: CapitalTriggerCode, threshold_pct: Decimal, path: Sequence[CapitalStressQuarter]
) -> CapitalStressTrigger:
    first_quarter = next((row.quarter for row in path if row.car < threshold_pct), None)
    return CapitalStressTrigger(
        code=code,
        threshold_pct=threshold_pct,
        fired=first_quarter is not None,
        first_quarter=first_quarter,
        action=TRIGGER_ACTIONS[code],
    )


def _credit_line_items(
    facts: Sequence[CapitalFact], params: CapitalParams
) -> tuple[CapitalLineItem, ...]:
    items: list[CapitalLineItem] = []
    loans = sorted(
        (fact for fact in facts if fact.fact_group == FACT_GROUP_LOAN_EXPOSURE),
        key=lambda fact: fact.category,
    )
    for fact in loans:
        weight = _risk_weight(params, fact.risk_weight_code, fact.category)
        items.append(
            CapitalLineItem(
                "credit_rwa",
                fact.category,
                _describe(fact.category),
                money(fact.amount),
                weight,
                money(fact.amount * weight / _HUNDRED),
            )
        )
    other_assets = [
        fact
        for fact in facts
        if fact.fact_group == FACT_GROUP_BALANCE_SHEET and fact.category == OTHER_ASSETS_CATEGORY
    ]
    for fact in other_assets:
        weight = _risk_weight(params, OTHER_ASSETS_RISK_WEIGHT_CODE, fact.category)
        items.append(
            CapitalLineItem(
                "credit_rwa",
                OTHER_ASSETS_CATEGORY,
                _describe(OTHER_ASSETS_CATEGORY),
                money(fact.amount),
                weight,
                money(fact.amount * weight / _HUNDRED),
            )
        )
    off_balance = sorted(
        (fact for fact in facts if fact.fact_group == FACT_GROUP_OFF_BALANCE),
        key=lambda fact: fact.category,
    )
    for fact in off_balance:
        if fact.ccf_pct is None:
            raise MissingParameterError(f"ccf_pct:{fact.category}")
        weight = _risk_weight(params, fact.risk_weight_code, fact.category)
        ead = money(fact.amount * fact.ccf_pct / _HUNDRED)
        items.append(
            CapitalLineItem(
                "credit_rwa",
                fact.category,
                f"{_describe(fact.category)} (EAD After {_pct_text(fact.ccf_pct)}% CCF)",
                ead,
                weight,
                money(ead * weight / _HUNDRED),
            )
        )
    balance_amounts: dict[str, Decimal] = {}
    for fact in facts:
        if fact.fact_group == FACT_GROUP_BALANCE_SHEET:
            balance_amounts[fact.category] = balance_amounts.get(fact.category, _ZERO) + fact.amount
    zero_weight = _risk_weight(params, ZERO_RISK_WEIGHT_CODE, ZERO_RISK_WEIGHT_CODE)
    for line_code, description, categories in _ZERO_WEIGHT_SUMMARY_LINES:
        exposure = money(
            sum((balance_amounts.get(category, _ZERO) for category in categories), _ZERO)
        )
        items.append(
            CapitalLineItem(
                "credit_rwa",
                line_code,
                f"{description} (Zero Risk Weight)",
                exposure,
                zero_weight,
                money(exposure * zero_weight / _HUNDRED),
            )
        )
    return tuple(items)


def _component_line_item(fact: CapitalFact, gp_included: Decimal) -> CapitalLineItem:
    tier = (fact.capital_tier or "").lower()
    is_general_provisions = (
        fact.capital_tier == TIER_T2
        and fact.category == GENERAL_PROVISIONS_CATEGORY
        and not fact.is_deduction
    )
    if fact.is_deduction:
        weighted = money(-fact.amount)
        description = f"{_describe(fact.category)} (Deduction)"
    elif is_general_provisions:
        weighted = gp_included
        description = f"{_describe(fact.category)} (Tier 2 Cap Applied)"
    else:
        weighted = money(fact.amount)
        description = _describe(fact.category)
    return CapitalLineItem(
        "capital_component",
        f"{tier}:{fact.category}",
        description,
        money(fact.amount),
        None,
        weighted,
    )


def _ratio_line_item(
    line_code: str, description: str, denominator: Decimal, value_pct: Decimal, numerator: Decimal
) -> CapitalLineItem:
    return CapitalLineItem("ratio", line_code, description, denominator, value_pct, numerator)


def _tier_total(components: Sequence[CapitalFact], tier: str) -> Decimal:
    return sum(
        (
            -fact.amount if fact.is_deduction else fact.amount
            for fact in components
            if fact.capital_tier == tier
        ),
        _ZERO,
    )


def _leverage_exposure(facts: Sequence[CapitalFact]) -> Decimal:
    on_balance = sum(
        (
            fact.amount
            for fact in facts
            if fact.fact_group == FACT_GROUP_BALANCE_SHEET and fact.side == "asset"
        ),
        _ZERO,
    )
    off_balance = _ZERO
    for fact in facts:
        if fact.fact_group != FACT_GROUP_OFF_BALANCE:
            continue
        if fact.ccf_pct is None:
            raise MissingParameterError(f"ccf_pct:{fact.category}")
        off_balance += fact.amount * fact.ccf_pct / _HUNDRED
    return on_balance + off_balance


def _facts_total(facts: Sequence[CapitalFact], fact_group: str, category: str) -> Decimal:
    return sum(
        (
            fact.amount
            for fact in facts
            if fact.fact_group == fact_group and fact.category == category
        ),
        _ZERO,
    )


def _risk_weight(params: CapitalParams, code: str | None, category: str) -> Decimal:
    if code is None:
        raise MissingParameterError(f"risk_weight_code:{category}")
    weight = params.risk_weights.get(code)
    if weight is None:
        raise MissingParameterError(code)
    return weight


def _pct_text(value: Decimal) -> str:
    if value == 0:
        return "0"
    return format(value.normalize(), "f")


def _describe(category: str) -> str:
    return category.replace("_", " ").title().replace("Bog", "BoG").replace("Gog", "GoG")
