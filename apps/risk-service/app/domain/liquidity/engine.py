"""Pure regulatory liquidity engine (LCR, NSFR, stress application).

Every function here is deterministic, Decimal-only, and free of database or
tenant concerns: callers supply the bank facts and the active parameter set and
receive fully materialized results with per-category line items. Monetary
amounts quantize to ``MONEY`` (4 dp) and ratio percentages quantize to
``RATIO_PCT`` (6 dp) with ``ROUND_HALF_UP``; status classification always
happens AFTER quantization so stored and displayed values agree.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

MONEY = Decimal("0.0001")
RATIO_PCT = Decimal("0.000001")
_HUNDRED = Decimal("100")
_ZERO = Decimal("0")

type LiquidityStatus = Literal["green", "amber", "red"]
type LineSection = Literal["hqla", "outflow", "inflow", "asf", "rsf"]

FACT_GROUP_BALANCE_SHEET = "balance_sheet"
FACT_GROUP_LOAN_EXPOSURE = "loan_exposure"
FACT_GROUP_SECURITIES = "securities"
FACT_GROUP_OFF_BALANCE = "off_balance"
FACT_GROUP_LCR_INFLOW = "lcr_inflow"

# The balance sheet carries loans in one aggregate row; the NSFR consumes the
# tie-out validated granular ``loan_exposure`` facts instead, so the aggregate
# is excluded from the RSF base to avoid double counting.
LOANS_GROSS_CATEGORY = "loans_gross"
OFF_BALANCE_RSF_CATEGORY = "off_balance_commitments"
RSF_SECURITIES_CATEGORIES = ("securities_bog_bills", "securities_gog_bonds")

SHOCK_INFLOW_MULTIPLIER = "inflow_multiplier"
SHOCK_HQLA_SECURITIES_HAIRCUT = "hqla_securities_haircut_pct"
SHOCK_RSF_SECURITIES_OVERRIDE = "rsf:securities_weight_override"
SHOCK_RUNOFF_PREFIX = "runoff:"
SHOCK_ASF_PREFIX = "asf:"


class MissingParameterError(Exception):
    """A category with a non-zero balance has no active rate/weight parameter."""

    def __init__(self, category: str) -> None:
        super().__init__(f"No active liquidity parameter covers category '{category}'.")
        self.category = category


class UnsupportedShockError(Exception):
    """A stress scenario carries a shock key the engine does not understand."""

    def __init__(self, scenario_code: str, shock_key: str) -> None:
        super().__init__(
            f"Stress scenario '{scenario_code}' carries unsupported shock key '{shock_key}'."
        )
        self.scenario_code = scenario_code
        self.shock_key = shock_key


class LiquidityComputationError(Exception):
    """The supplied facts produce a degenerate ratio (zero denominator)."""


@dataclass(frozen=True)
class LiquidityFact:
    """One bank financial fact, reduced to the fields the liquidity engine uses."""

    fact_group: str
    category: str
    amount: Decimal
    hqla_level: str | None = None
    side: str | None = None
    cash_derived: bool = False


@dataclass(frozen=True)
class LiquidityParams:
    """Active parameter set resolved as of the reporting-period end."""

    outflow_rates: Mapping[str, Decimal]
    inflow_rates: Mapping[str, Decimal]
    asf_weights: Mapping[str, Decimal]
    rsf_weights: Mapping[str, Decimal]
    inflow_cap_pct: Decimal
    lcr_min_pct: Decimal
    lcr_amber_floor_pct: Decimal
    nsfr_min_pct: Decimal
    nsfr_amber_floor_pct: Decimal


@dataclass(frozen=True)
class LiquidityLineItem:
    section: LineSection
    line_code: str
    description: str
    exposure_amount: Decimal | None
    rate_pct: Decimal | None
    weighted_amount: Decimal


@dataclass(frozen=True)
class LcrResult:
    hqla_total: Decimal
    outflows_total: Decimal
    gross_inflows_total: Decimal
    inflow_cap_amount: Decimal
    capped_inflows_total: Decimal
    inflow_cap_applied: bool
    net_outflows_total: Decimal
    lcr_pct: Decimal
    status: LiquidityStatus
    all_hqla_level1: bool
    line_items: tuple[LiquidityLineItem, ...]


@dataclass(frozen=True)
class NsfrResult:
    asf_total: Decimal
    rsf_total: Decimal
    nsfr_pct: Decimal
    status: LiquidityStatus
    line_items: tuple[LiquidityLineItem, ...]


def money(value: Decimal) -> Decimal:
    return value.quantize(MONEY, rounding=ROUND_HALF_UP)


def ratio_pct(value: Decimal) -> Decimal:
    return value.quantize(RATIO_PCT, rounding=ROUND_HALF_UP)


def classify_ratio(
    value_pct: Decimal, minimum_pct: Decimal, amber_floor_pct: Decimal
) -> LiquidityStatus:
    """Classify an already-quantized ratio percentage against its thresholds."""
    if value_pct >= minimum_pct:
        return "green"
    if value_pct >= amber_floor_pct:
        return "amber"
    return "red"


def compute_lcr(facts: Sequence[LiquidityFact], params: LiquidityParams) -> LcrResult:
    hqla_facts = _sorted(
        fact
        for fact in facts
        if fact.fact_group == FACT_GROUP_SECURITIES and fact.hqla_level is not None
    )
    hqla_items = tuple(
        LiquidityLineItem(
            section="hqla",
            line_code=fact.category,
            description=_describe(fact.category),
            exposure_amount=money(fact.amount),
            rate_pct=None,
            weighted_amount=money(fact.amount),
        )
        for fact in hqla_facts
    )
    hqla_total = money(sum((item.weighted_amount for item in hqla_items), _ZERO))

    outflow_facts = _sorted(
        fact
        for fact in facts
        if (fact.fact_group == FACT_GROUP_BALANCE_SHEET and fact.side == "liability")
        or fact.fact_group == FACT_GROUP_OFF_BALANCE
    )
    outflow_items = _weighted_items("outflow", outflow_facts, params.outflow_rates)
    outflows_total = money(sum((item.weighted_amount for item in outflow_items), _ZERO))

    inflow_facts = _sorted(fact for fact in facts if fact.fact_group == FACT_GROUP_LCR_INFLOW)
    inflow_items = _weighted_items("inflow", inflow_facts, params.inflow_rates)
    gross_inflows_total = money(sum((item.weighted_amount for item in inflow_items), _ZERO))

    inflow_cap_amount = money(outflows_total * params.inflow_cap_pct / _HUNDRED)
    inflow_cap_applied = gross_inflows_total > inflow_cap_amount
    capped_inflows_total = inflow_cap_amount if inflow_cap_applied else gross_inflows_total
    net_outflows_total = money(outflows_total - capped_inflows_total)
    if net_outflows_total <= _ZERO:
        raise LiquidityComputationError(
            "Net cash outflows must be positive to compute the LCR ratio."
        )

    lcr_pct = ratio_pct(hqla_total / net_outflows_total * _HUNDRED)
    status = classify_ratio(lcr_pct, params.lcr_min_pct, params.lcr_amber_floor_pct)
    return LcrResult(
        hqla_total=hqla_total,
        outflows_total=outflows_total,
        gross_inflows_total=gross_inflows_total,
        inflow_cap_amount=inflow_cap_amount,
        capped_inflows_total=capped_inflows_total,
        inflow_cap_applied=inflow_cap_applied,
        net_outflows_total=net_outflows_total,
        lcr_pct=lcr_pct,
        status=status,
        all_hqla_level1=all(fact.hqla_level == "L1" for fact in hqla_facts),
        line_items=(*hqla_items, *outflow_items, *inflow_items),
    )


def compute_nsfr(facts: Sequence[LiquidityFact], params: LiquidityParams) -> NsfrResult:
    asf_facts = _sorted(
        fact
        for fact in facts
        if fact.fact_group == FACT_GROUP_BALANCE_SHEET and fact.side in ("liability", "equity")
    )
    asf_items = _weighted_items("asf", asf_facts, params.asf_weights)
    asf_total = money(sum((item.weighted_amount for item in asf_items), _ZERO))

    rsf_facts = _sorted(
        fact
        for fact in facts
        if (
            fact.fact_group == FACT_GROUP_BALANCE_SHEET
            and fact.side == "asset"
            and fact.category != LOANS_GROSS_CATEGORY
        )
        or fact.fact_group == FACT_GROUP_LOAN_EXPOSURE
    )
    rsf_items = list(_weighted_items("rsf", rsf_facts, params.rsf_weights))

    off_balance_total = money(
        sum(
            (fact.amount for fact in facts if fact.fact_group == FACT_GROUP_OFF_BALANCE),
            _ZERO,
        )
    )
    off_balance_weight = params.rsf_weights.get(OFF_BALANCE_RSF_CATEGORY)
    if off_balance_weight is None and off_balance_total != _ZERO:
        raise MissingParameterError(OFF_BALANCE_RSF_CATEGORY)
    if off_balance_weight is not None:
        rsf_items.append(
            LiquidityLineItem(
                section="rsf",
                line_code=OFF_BALANCE_RSF_CATEGORY,
                description=_describe(OFF_BALANCE_RSF_CATEGORY),
                exposure_amount=off_balance_total,
                rate_pct=off_balance_weight,
                weighted_amount=money(off_balance_total * off_balance_weight / _HUNDRED),
            )
        )
    rsf_total = money(sum((item.weighted_amount for item in rsf_items), _ZERO))
    if rsf_total <= _ZERO:
        raise LiquidityComputationError(
            "Required stable funding must be positive to compute the NSFR ratio."
        )

    nsfr_pct = ratio_pct(asf_total / rsf_total * _HUNDRED)
    status = classify_ratio(nsfr_pct, params.nsfr_min_pct, params.nsfr_amber_floor_pct)
    return NsfrResult(
        asf_total=asf_total,
        rsf_total=rsf_total,
        nsfr_pct=nsfr_pct,
        status=status,
        line_items=(*asf_items, *rsf_items),
    )


def apply_liquidity_stress(
    scenario_code: str,
    facts: Sequence[LiquidityFact],
    params: LiquidityParams,
    shocks: Mapping[str, Decimal],
) -> tuple[tuple[LiquidityFact, ...], LiquidityParams]:
    """Return ``(stressed_facts, stressed_params)`` for one stress scenario.

    Supported shock keys:

    - ``runoff:<category>`` replaces the LCR outflow runoff rate.
    - ``inflow_multiplier`` scales gross LCR inflows (applied to inflow rates).
    - ``hqla_securities_haircut_pct`` haircuts securities-group HQLA facts,
      excluding cash-derived rows (vault cash and BoG excess reserves keep
      their face value; only marketable securities take the haircut).
    - ``asf:<category>`` replaces the NSFR ASF weight.
    - ``rsf:securities_weight_override`` replaces the RSF weight for the
      balance-sheet securities rows. The NSFR is structural, so the override
      applies to UNSTRESSED balance-sheet values (no market-value haircut on
      the RSF side).
    """
    outflow_rates = dict(params.outflow_rates)
    inflow_rates = dict(params.inflow_rates)
    asf_weights = dict(params.asf_weights)
    rsf_weights = dict(params.rsf_weights)
    haircut_pct = _ZERO

    for shock_key, shock_value in shocks.items():
        if shock_key.startswith(SHOCK_RUNOFF_PREFIX):
            outflow_rates[shock_key.removeprefix(SHOCK_RUNOFF_PREFIX)] = shock_value
        elif shock_key == SHOCK_INFLOW_MULTIPLIER:
            inflow_rates = {category: rate * shock_value for category, rate in inflow_rates.items()}
        elif shock_key == SHOCK_HQLA_SECURITIES_HAIRCUT:
            haircut_pct = shock_value
        elif shock_key.startswith(SHOCK_ASF_PREFIX):
            asf_weights[shock_key.removeprefix(SHOCK_ASF_PREFIX)] = shock_value
        elif shock_key == SHOCK_RSF_SECURITIES_OVERRIDE:
            for category in RSF_SECURITIES_CATEGORIES:
                rsf_weights[category] = shock_value
        else:
            raise UnsupportedShockError(scenario_code, shock_key)

    haircut_factor = (_HUNDRED - haircut_pct) / _HUNDRED
    stressed_facts = tuple(
        replace(fact, amount=money(fact.amount * haircut_factor))
        if (
            haircut_pct != _ZERO
            and fact.fact_group == FACT_GROUP_SECURITIES
            and fact.hqla_level is not None
            and not fact.cash_derived
        )
        else fact
        for fact in facts
    )
    stressed_params = replace(
        params,
        outflow_rates=outflow_rates,
        inflow_rates=inflow_rates,
        asf_weights=asf_weights,
        rsf_weights=rsf_weights,
    )
    return stressed_facts, stressed_params


def _weighted_items(
    section: LineSection,
    facts: Sequence[LiquidityFact],
    rates: Mapping[str, Decimal],
) -> tuple[LiquidityLineItem, ...]:
    items: list[LiquidityLineItem] = []
    for fact in facts:
        rate = rates.get(fact.category)
        if rate is None:
            if fact.amount != _ZERO:
                raise MissingParameterError(fact.category)
            continue
        items.append(
            LiquidityLineItem(
                section=section,
                line_code=fact.category,
                description=_describe(fact.category),
                exposure_amount=money(fact.amount),
                rate_pct=rate,
                weighted_amount=money(fact.amount * rate / _HUNDRED),
            )
        )
    return tuple(items)


def _sorted(facts: Iterable[LiquidityFact]) -> tuple[LiquidityFact, ...]:
    return tuple(sorted(facts, key=lambda fact: (fact.fact_group, fact.category)))


def _describe(category: str) -> str:
    return category.replace("_", " ").title().replace("Bog", "BoG").replace("Gog", "GoG")
