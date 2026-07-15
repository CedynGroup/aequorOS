"""Pure foreign-exchange (FX) risk engine.

Every function here is deterministic and free of database or tenant concerns:
callers supply the per-currency net open positions (in GHS-equivalent), the
daily FX return histories, the hedge book, and the resolved parameter set, and
receive fully materialized net-open-position (NOP), value-at-risk (VaR) and
hedge-effectiveness results with per-line items. Monetary amounts quantize to
``MONEY`` (4 dp) and ratio/percentage figures to ``RATIO_PCT`` (6 dp), both with
``ROUND_HALF_UP``; status classification always happens AFTER quantization so
stored and displayed values agree.

Monetary and percentage arithmetic is Decimal-only. The historical-simulation
VaR consumes daily returns that arrive as JSON floats; each element crosses the
float -> Decimal boundary exactly once, at the point of use, via
``Decimal(str(value))`` (see ``_as_decimal``). From there the profit-and-loss
vector, the sort, and the percentile selection are exact Decimal operations, so
the returned VaR figures are reproducible to the cent.

Methodology (Product Documentation Module 3):

- **Net open position (NOP).** Each currency's net exposure is expressed in
  GHS-equivalent (``net_ghs`` = assets - liabilities + net derivatives, revalued
  at spot). Long currencies carry a positive ``net_ghs``, short currencies a
  negative one. The aggregate long and short sums are formed separately and the
  overall NOP is the Basel shorthand ``max(sum_long, sum_short)``. Both the
  overall NOP and each single currency are measured against Tier 1 capital: the
  aggregate NOP must stay within ``aggregate_limit_pct`` (20% at BoG) and no
  single currency may exceed ``single_limit_pct`` (10%).
- **Value at Risk.** Historical simulation over a rolling window: the portfolio
  daily P&L vector is ``Sum_ccy net_ghs_c * return_c[t]`` across the joint
  historical return vectors, which naturally captures cross-currency
  correlation. The 1-day 99% VaR is the loss at the ``(100 - confidence)``th
  percentile (nearest-rank on the sorted ascending P&L, reported as a positive
  loss). Standalone per-currency VaR uses each currency's own P&L; the
  diversification benefit is ``Sum standalone_var - portfolio_var``. Stressed VaR
  re-runs the same simulation over the cedi-crisis sub-window only and applies a
  supervisory correlation uplift to the tail.
- **Hedge effectiveness (IFRS 9).** A hedge is effective when the prospective
  regression R-squared clears ``r2_min_pct`` (80%) AND the retrospective
  dollar-offset ratio sits within ``[offset_low_pct, offset_high_pct]``
  (80%-125%).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal
from typing import Literal

MONEY = Decimal("0.0001")
RATIO_PCT = Decimal("0.000001")
_HUNDRED = Decimal("100")
_ZERO = Decimal("0")
_ONE = Decimal("1")

type FxSide = Literal["long", "short"]
type FxLineSection = Literal["fx_position", "fx_var", "fx_hedge"]

# A limit ratio is green while it clears the limit by this buffer (as a fraction
# of the limit); amber sits between the buffer band and the limit, red above.
LIMIT_GREEN_FRACTION = Decimal("0.75")


class MissingParameterError(Exception):
    """A position needs a return history or parameter the input set lacks."""

    def __init__(self, name: str) -> None:
        super().__init__(f"No active FX parameter or return history covers '{name}'.")
        self.name = name


class FxComputationError(Exception):
    """The supplied inputs produce a degenerate result (no positions, zero Tier 1)."""


@dataclass(frozen=True)
class FxPosition:
    """One currency's net open position reduced to the fields FX risk uses.

    ``net_ghs`` is the signed GHS-equivalent (positive long, negative short) and
    is the authoritative exposure the engine measures; the currency-denominated
    fields are carried for disclosure only.
    """

    currency: str
    net_ghs: Decimal
    spot_ghs: Decimal
    net_ccy: Decimal
    assets_ccy: Decimal
    liabilities_ccy: Decimal
    net_derivatives_ccy: Decimal


@dataclass(frozen=True)
class FxHedge:
    """One hedging instrument with its IFRS 9 effectiveness measurements.

    ``prospective_r2`` and ``dollar_offset_ratio`` are stored as fractions (0.94,
    1.02); the engine scales them to percentages for the limit comparison.
    """

    hedge_id: str
    instrument: str
    pair: str
    mtm_ghs: Decimal
    prospective_r2: Decimal
    dollar_offset_ratio: Decimal


@dataclass(frozen=True)
class FxLineItem:
    section: FxLineSection
    line_code: str
    description: str
    exposure_amount: Decimal | None
    rate_pct: Decimal | None
    weighted_amount: Decimal


@dataclass(frozen=True)
class CurrencyNop:
    currency: str
    net_ghs: Decimal
    net_ccy: Decimal
    spot_ghs: Decimal
    side: FxSide
    abs_pct_tier1: Decimal
    within_single_limit: bool


@dataclass(frozen=True)
class NopResult:
    currencies: tuple[CurrencyNop, ...]
    sum_long: Decimal
    sum_short: Decimal
    overall_nop: Decimal
    nop_pct_tier1: Decimal
    single_ccy_max_pct: Decimal
    single_ccy_max_currency: str
    within_single_limit: bool
    within_aggregate_limit: bool
    single_limit_pct: Decimal
    aggregate_limit_pct: Decimal
    tier1: Decimal
    line_items: tuple[FxLineItem, ...]


@dataclass(frozen=True)
class CurrencyVar:
    currency: str
    standalone_var: Decimal
    net_ghs: Decimal


@dataclass(frozen=True)
class VarResult:
    portfolio_var: Decimal
    standalone_total: Decimal
    diversification_benefit: Decimal
    currency_vars: tuple[CurrencyVar, ...]
    window: int
    observations: int
    confidence_pct: Decimal
    line_items: tuple[FxLineItem, ...]


@dataclass(frozen=True)
class HedgeAssessment:
    hedge_id: str
    instrument: str
    pair: str
    mtm_ghs: Decimal
    prospective_r2_pct: Decimal
    dollar_offset_pct: Decimal
    r2_pass: bool
    offset_pass: bool
    effective: bool


@dataclass(frozen=True)
class HedgeResult:
    hedges: tuple[HedgeAssessment, ...]
    effective_count: int
    ineffective_count: int
    total_count: int
    aggregate_mtm_ghs: Decimal
    line_items: tuple[FxLineItem, ...]


@dataclass(frozen=True)
class FxScenarioNop:
    scenario_code: str
    shock_pct: Decimal
    nop_ghs: Decimal
    nop_pct_tier1: Decimal
    within_aggregate_limit: bool


def money(value: Decimal) -> Decimal:
    return value.quantize(MONEY, rounding=ROUND_HALF_UP)


def ratio_pct(value: Decimal) -> Decimal:
    return value.quantize(RATIO_PCT, rounding=ROUND_HALF_UP)


def classify_limit(value_pct: Decimal, limit_pct: Decimal) -> Literal["green", "amber", "red"]:
    """Classify an already-quantized ratio against a supervisory upper limit.

    Red once the ratio exceeds the limit, amber inside the buffer band just below
    it, green otherwise.
    """
    if value_pct > limit_pct:
        return "red"
    if value_pct > limit_pct * LIMIT_GREEN_FRACTION:
        return "amber"
    return "green"


def compute_nop(
    positions: Sequence[FxPosition],
    tier1: Decimal,
    single_limit_pct: Decimal,
    aggregate_limit_pct: Decimal,
) -> NopResult:
    """Materialize the per-currency and aggregate net open position vs Tier 1."""
    if not positions:
        raise FxComputationError("At least one FX position is required to compute the NOP.")
    if tier1 <= _ZERO:
        raise FxComputationError("Tier 1 capital must be positive to evaluate FX limits.")

    currencies: list[CurrencyNop] = []
    line_items: list[FxLineItem] = []
    sum_long = _ZERO
    sum_short = _ZERO
    single_max_pct = _ZERO
    single_max_ccy = ""
    for position in sorted(positions, key=lambda item: item.currency):
        net = money(position.net_ghs)
        net_ccy = money(position.net_ccy)
        side: FxSide = "long" if net >= _ZERO else "short"
        if net >= _ZERO:
            sum_long += net
        else:
            sum_short += -net
        abs_pct = ratio_pct(abs(net) / tier1 * _HUNDRED)
        within_single = abs_pct <= single_limit_pct
        if abs_pct > single_max_pct:
            single_max_pct = abs_pct
            single_max_ccy = position.currency
        currencies.append(
            CurrencyNop(
                currency=position.currency,
                net_ghs=net,
                net_ccy=net_ccy,
                spot_ghs=position.spot_ghs,
                side=side,
                abs_pct_tier1=abs_pct,
                within_single_limit=within_single,
            )
        )
        line_items.append(
            FxLineItem(
                section="fx_position",
                line_code=position.currency,
                description=f"{position.currency} Net Open Position ({side.title()})",
                exposure_amount=net_ccy,
                rate_pct=position.spot_ghs,
                weighted_amount=net,
            )
        )

    sum_long = money(sum_long)
    sum_short = money(sum_short)
    overall = max(sum_long, sum_short)
    nop_pct = ratio_pct(overall / tier1 * _HUNDRED)
    return NopResult(
        currencies=tuple(currencies),
        sum_long=sum_long,
        sum_short=sum_short,
        overall_nop=overall,
        nop_pct_tier1=nop_pct,
        single_ccy_max_pct=single_max_pct,
        single_ccy_max_currency=single_max_ccy,
        within_single_limit=all(currency.within_single_limit for currency in currencies),
        within_aggregate_limit=nop_pct <= aggregate_limit_pct,
        single_limit_pct=single_limit_pct,
        aggregate_limit_pct=aggregate_limit_pct,
        tier1=money(tier1),
        line_items=tuple(line_items),
    )


def compute_var(
    positions: Sequence[FxPosition],
    return_histories: Mapping[str, Sequence[float | Decimal]],
    confidence_pct: Decimal,
) -> VarResult:
    """1-day historical-simulation VaR at ``confidence_pct`` over the full window.

    Returns the diversified portfolio VaR, each currency's standalone VaR, and
    the diversification benefit (standalone sum minus portfolio VaR).
    """
    if not positions:
        raise FxComputationError("At least one FX position is required to compute VaR.")
    window = _common_window(positions, return_histories)

    portfolio_pnl = _portfolio_pnl(positions, return_histories, range(window))
    portfolio_var = _historical_var(portfolio_pnl, confidence_pct)

    currency_vars: list[CurrencyVar] = []
    standalone_total = _ZERO
    for position in sorted(positions, key=lambda item: item.currency):
        series = return_histories[position.currency]
        standalone_pnl = [position.net_ghs * _as_decimal(series[index]) for index in range(window)]
        standalone_var = _historical_var(standalone_pnl, confidence_pct)
        standalone_total += standalone_var
        currency_vars.append(
            CurrencyVar(
                currency=position.currency,
                standalone_var=standalone_var,
                net_ghs=money(position.net_ghs),
            )
        )
    standalone_total = money(standalone_total)
    diversification = money(standalone_total - portfolio_var)

    line_items: list[FxLineItem] = [
        FxLineItem(
            section="fx_var",
            line_code="portfolio_var",
            description=(
                f"Portfolio VaR ({_pct_text(confidence_pct)}% 1-Day, Historical Simulation)"
            ),
            exposure_amount=None,
            rate_pct=confidence_pct,
            weighted_amount=portfolio_var,
        ),
        FxLineItem(
            section="fx_var",
            line_code="diversification_benefit",
            description="Diversification Benefit (Standalone Sum - Portfolio VaR)",
            exposure_amount=standalone_total,
            rate_pct=None,
            weighted_amount=diversification,
        ),
    ]
    line_items.extend(
        FxLineItem(
            section="fx_var",
            line_code=f"standalone_{currency_var.currency.lower()}",
            description=f"{currency_var.currency} Standalone VaR",
            exposure_amount=currency_var.net_ghs,
            rate_pct=None,
            weighted_amount=currency_var.standalone_var,
        )
        for currency_var in currency_vars
    )

    return VarResult(
        portfolio_var=portfolio_var,
        standalone_total=standalone_total,
        diversification_benefit=diversification,
        currency_vars=tuple(currency_vars),
        window=window,
        observations=len(portfolio_pnl),
        confidence_pct=confidence_pct,
        line_items=tuple(line_items),
    )


def compute_stressed_var(
    positions: Sequence[FxPosition],
    return_histories: Mapping[str, Sequence[float | Decimal]],
    confidence_pct: Decimal,
    crisis_window: tuple[int, int],
    correlation_uplift: Decimal,
) -> Decimal:
    """1-day VaR over the cedi-crisis sub-window with a supervisory correlation uplift.

    The stress uses only the ``[start, end]`` slice of each return history (the
    2022-2023 cedi-crisis sub-window) and multiplies the resulting historical VaR
    by ``(1 + correlation_uplift)`` to reflect the tail co-movement the stress
    scenario prescribes.
    """
    if not positions:
        raise FxComputationError("At least one FX position is required to compute stressed VaR.")
    start, end = crisis_window
    if start < 0 or start > end:
        raise FxComputationError(f"Invalid cedi-crisis window [{start}, {end}].")
    for position in positions:
        series = return_histories.get(position.currency)
        if series is None:
            raise MissingParameterError(f"fx_return_history:{position.currency}")
        if end >= len(series):
            raise FxComputationError(
                f"Cedi-crisis window [{start}, {end}] exceeds the {len(series)}-observation "
                f"return history for {position.currency}."
            )

    crisis_pnl = _portfolio_pnl(positions, return_histories, range(start, end + 1))
    base = _historical_var(crisis_pnl, confidence_pct)
    return money(base * (_ONE + correlation_uplift))


def assess_hedges(
    hedges: Sequence[FxHedge],
    r2_min_pct: Decimal,
    offset_low_pct: Decimal,
    offset_high_pct: Decimal,
) -> HedgeResult:
    """Classify each hedge effective/ineffective under the IFRS 9 dual test."""
    assessments: list[HedgeAssessment] = []
    line_items: list[FxLineItem] = []
    effective_count = 0
    aggregate_mtm = _ZERO
    for hedge in sorted(hedges, key=lambda item: item.hedge_id):
        r2_pct = ratio_pct(hedge.prospective_r2 * _HUNDRED)
        offset_pct = ratio_pct(hedge.dollar_offset_ratio * _HUNDRED)
        r2_pass = r2_pct >= r2_min_pct
        offset_pass = offset_low_pct <= offset_pct <= offset_high_pct
        effective = r2_pass and offset_pass
        if effective:
            effective_count += 1
        mtm = money(hedge.mtm_ghs)
        aggregate_mtm += mtm
        assessments.append(
            HedgeAssessment(
                hedge_id=hedge.hedge_id,
                instrument=hedge.instrument,
                pair=hedge.pair,
                mtm_ghs=mtm,
                prospective_r2_pct=r2_pct,
                dollar_offset_pct=offset_pct,
                r2_pass=r2_pass,
                offset_pass=offset_pass,
                effective=effective,
            )
        )
        state = "Effective" if effective else "Ineffective"
        line_items.append(
            FxLineItem(
                section="fx_hedge",
                line_code=hedge.hedge_id,
                description=(
                    f"{_describe(hedge.instrument)} {hedge.pair} Hedge "
                    f"({state}: R-squared {r2_pct}%, Offset {offset_pct}%)"
                ),
                exposure_amount=mtm,
                rate_pct=r2_pct,
                weighted_amount=mtm,
            )
        )
    total = len(assessments)
    return HedgeResult(
        hedges=tuple(assessments),
        effective_count=effective_count,
        ineffective_count=total - effective_count,
        total_count=total,
        aggregate_mtm_ghs=money(aggregate_mtm),
        line_items=tuple(line_items),
    )


def run_fx_scenarios(
    positions: Sequence[FxPosition],
    tier1: Decimal,
    scenario_shocks: Mapping[str, Decimal],
    single_limit_pct: Decimal,
    aggregate_limit_pct: Decimal,
) -> tuple[FxScenarioNop, ...]:
    """Revalue the book under each cedi-depreciation shock and recompute the NOP.

    ``scenario_shocks`` maps a scenario code to a percentage cedi depreciation; a
    depreciation of ``s%`` lifts every GHS-equivalent position by ``(1 + s/100)``
    (foreign currency becomes more expensive in cedi), growing the NOP.
    """
    results: list[FxScenarioNop] = []
    for code, shock_pct in scenario_shocks.items():
        factor = _ONE + shock_pct / _HUNDRED
        shocked = [
            replace(position, net_ghs=money(position.net_ghs * factor)) for position in positions
        ]
        nop = compute_nop(shocked, tier1, single_limit_pct, aggregate_limit_pct)
        results.append(
            FxScenarioNop(
                scenario_code=code,
                shock_pct=shock_pct,
                nop_ghs=nop.overall_nop,
                nop_pct_tier1=nop.nop_pct_tier1,
                within_aggregate_limit=nop.within_aggregate_limit,
            )
        )
    return tuple(results)


def stressed_var_line_item(stressed_var: Decimal, correlation_uplift: Decimal) -> FxLineItem:
    return FxLineItem(
        section="fx_var",
        line_code="stressed_var",
        description="Stressed VaR (Cedi-Crisis Sub-Window, Correlation Uplift Applied)",
        exposure_amount=None,
        rate_pct=None,
        weighted_amount=stressed_var,
    )


def _common_window(
    positions: Sequence[FxPosition],
    return_histories: Mapping[str, Sequence[float | Decimal]],
) -> int:
    lengths: list[int] = []
    for position in positions:
        series = return_histories.get(position.currency)
        if series is None or len(series) == 0:
            raise MissingParameterError(f"fx_return_history:{position.currency}")
        lengths.append(len(series))
    return min(lengths)


def _portfolio_pnl(
    positions: Sequence[FxPosition],
    return_histories: Mapping[str, Sequence[float | Decimal]],
    indices: range,
) -> list[Decimal]:
    vector: list[Decimal] = []
    for index in indices:
        total = _ZERO
        for position in positions:
            total += position.net_ghs * _as_decimal(return_histories[position.currency][index])
        vector.append(total)
    return vector


def _historical_var(pnl: Sequence[Decimal], confidence_pct: Decimal) -> Decimal:
    """Nearest-rank VaR: the loss at the ``(100 - confidence)``th percentile.

    The tail rank is ``ceil((1 - confidence) * n)`` (1-based) into the ascending
    P&L, reported as a positive loss and floored at zero when the tail is a gain.
    """
    if not pnl:
        return _ZERO
    count = len(pnl)
    tail_fraction = (_HUNDRED - confidence_pct) / _HUNDRED
    rank = int((tail_fraction * Decimal(count)).to_integral_value(rounding=ROUND_CEILING))
    rank = max(1, min(rank, count))
    worst = sorted(pnl)[rank - 1]
    return money(max(_ZERO, -worst))


def _as_decimal(value: float | Decimal) -> Decimal:
    """Cross the float -> Decimal boundary exactly once, at the point of use."""
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _pct_text(value: Decimal) -> str:
    if value == 0:
        return "0"
    return format(value.normalize(), "f")


def _describe(value: str) -> str:
    return value.replace("_", " ").title()
