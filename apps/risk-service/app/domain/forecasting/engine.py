"""Pure balance-sheet forecasting engine (5-year projection, optimizer, what-if).

Every function here is deterministic, Decimal-only, and free of database or
tenant concerns: callers supply the bank facts, the active liquidity/capital
parameter sets, and the scenario assumptions, and receive a fully materialized
five-year projection. Monetary amounts quantize to ``MONEY`` (4 dp) and ratio
percentages quantize to ``RATIO_PCT`` (6 dp) with ``ROUND_HALF_UP``.

Cross-module consistency is the headline feature: the projected regulatory
ratios are NOT re-implemented here. Each projected year builds a full bank
fact set (the same ``fact_group``/``category`` structure the other engines
consume) and calls ``compute_lcr``/``compute_nsfr`` from
``app.domain.liquidity.engine`` and ``compute_rwa``/``compute_capital_ratios``
from ``app.domain.capital.engine`` on it, with the unshocked baseline
parameters. Year 0 of every projection is the as-of fact set itself, so a
projection's year-0 ratios equal the standalone engines' baseline outputs.

Year ``t`` mechanics (t = 1..years, year 0 = as-of facts):

- Loans: every ``loan_exposure`` category, ``off_balance`` commitment, and
  ``lcr_inflow`` fact scales by ``1 + loan_growth_pct/100``; the balance-sheet
  ``loans_gross`` row is re-derived as the sum of the scaled exposures.
- Deposits: each balance-sheet deposit category scales by
  ``1 + deposit_growth_pct/100``. ``secured_funding_l1`` is constant and
  ``term_borrowings_gt_1y`` becomes the funding plug (below).
- Securities: the balance-sheet securities rows and their marketable
  securities-group HQLA mirrors scale by
  ``1 + (deposit_growth_pct + securities_shift_pp)/100``; the cash-derived
  HQLA rows and the balance-sheet cash/reserve rows scale with deposits.
- ``other_assets`` (and any unrecognized balance-sheet row) stays constant.
- P&L: ``earning_assets = loans + securities``;
  ``nii = nim_pct/100 x avg(earning_assets_{t-1}, earning_assets_t)``;
  ``fees = fee_income_pct_assets/100 x avg total assets``;
  ``opex = cost_to_income_pct/100 x total_income``;
  ``credit_losses = credit_loss_rate_pct/100 x loans_t``;
  ``tax = tax_rate_pct/100 x max(pre_tax, 0)``;
  ``dividends = dividend_payout_pct/100 x max(net_income, 0)``.
- Equity: ``capital_total_t = capital_total_{t-1} + retained`` and the CET1
  ``retained_earnings`` capital component grows by the same amount (AT1/T2
  components are constant).
- Funding plug: ``term_borrowings_t = assets_t - (deposits_t +
  secured_funding + capital_total_t)``. When the plug is negative the bank
  has surplus funding, so borrowings floor at zero and the BoG excess-reserve
  cash row (plus its HQLA mirror) absorbs the residual so that assets equal
  liabilities plus equity Decimal-exactly. NOTE: the build brief described
  this branch as "scale cash down by the shortfall"; a downward cash
  adjustment cannot re-balance a funding surplus (assets must RISE to meet
  the surplus), so cash absorbs the residual upward and the documented 5%
  cash floor is kept as a guard that raises
  ``ProjectionError('balance_sheet_infeasible')`` if the adjusted cash ever
  falls below 5% of assets.
- Operational income roll-forward for the BIA charge: the gross-income
  history seeds from the as-of ``operational_income`` facts; each projected
  year appends ``GI_t = total_income_t`` and the capital engine consumes the
  trailing three years.
- ``fx_depreciation_pct`` revalues the ``market_risk`` open FX positions once
  at t=1 by ``1 + fx/100`` (FX-denominated position revaluation);
  ``securities_mtm_haircut_pct`` (what-if only) marks the marketable
  securities down once at t=1 after growth.
"""

from __future__ import annotations

import itertools
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from decimal import ROUND_HALF_UP, Decimal

from app.domain.capital.engine import (
    CapitalFact,
    CapitalParams,
    compute_capital_ratios,
    compute_rwa,
)
from app.domain.liquidity.engine import (
    LiquidityFact,
    LiquidityParams,
    compute_lcr,
    compute_nsfr,
)

MONEY = Decimal("0.0001")
RATIO_PCT = Decimal("0.000001")
PROJECTION_YEARS = 5
CASH_FLOOR_PCT = Decimal("5")
_HUNDRED = Decimal("100")
_ZERO = Decimal("0")
_ONE = Decimal("1")
_TWO = Decimal("2")

FACT_GROUP_BALANCE_SHEET = "balance_sheet"
FACT_GROUP_LOAN_EXPOSURE = "loan_exposure"
FACT_GROUP_SECURITIES = "securities"
FACT_GROUP_OFF_BALANCE = "off_balance"
FACT_GROUP_LCR_INFLOW = "lcr_inflow"
FACT_GROUP_MARKET_RISK = "market_risk"
FACT_GROUP_OPERATIONAL_INCOME = "operational_income"
FACT_GROUP_CAPITAL_COMPONENT = "capital_component"

CASH_CATEGORIES = ("cash_vault", "bog_required_reserves", "bog_excess_reserves")
SECURITIES_BS_CATEGORIES = ("securities_bog_bills", "securities_gog_bonds")
LOANS_GROSS_CATEGORY = "loans_gross"
SECURED_FUNDING_CATEGORY = "secured_funding_l1"
TERM_BORROWINGS_CATEGORY = "term_borrowings_gt_1y"
CAPITAL_TOTAL_CATEGORY = "capital_total"
RETAINED_EARNINGS_CATEGORY = "retained_earnings"
CASH_PLUG_CATEGORY = "bog_excess_reserves"
# Cash-derived securities-group HQLA rows mirror these balance-sheet sources.
CASH_HQLA_MIRRORS = {
    "bog_excess_reserves": "bog_excess_reserves_hqla",
    "cash_vault": "cash_vault_hqla",
}
TIER_CET1 = "CET1"

GI_TRAILING_YEARS = 3

DEFAULT_FEE_INCOME_PCT_ASSETS = Decimal("1.2")
DEFAULT_TAX_RATE_PCT = Decimal("25")
DEFAULT_SECURITIES_SHIFT_PP = Decimal("0")

# Strategic optimizer decision grid: 4 x 3 x 3 x 3 = 108 candidates.
OPTIMIZER_LOAN_GROWTH_GRID = (Decimal("8"), Decimal("12"), Decimal("16"), Decimal("20"))
OPTIMIZER_SECURITIES_SHIFT_GRID = (Decimal("-5"), Decimal("0"), Decimal("5"))
OPTIMIZER_DEPOSIT_PREMIUM_GRID = (0, 50, 100)
OPTIMIZER_DIVIDEND_PAYOUT_GRID = (Decimal("0"), Decimal("30"), Decimal("50"))
OPTIMIZER_TOP_LIMIT = 10
# deposit premium (bps) -> (deposit growth delta pp, NIM delta pp).
DEPOSIT_PREMIUM_EFFECTS: dict[int, tuple[Decimal, Decimal]] = {
    0: (Decimal("0"), Decimal("0")),
    50: (Decimal("2"), Decimal("-0.10")),
    100: (Decimal("4"), Decimal("-0.20")),
}

CONSTRAINT_CAR = "car"
CONSTRAINT_LCR = "lcr"
CONSTRAINT_NSFR = "nsfr"
OPTIMIZER_CONSTRAINT_CODES = (CONSTRAINT_CAR, CONSTRAINT_LCR, CONSTRAINT_NSFR)

WHATIF_RATE_SHOCK_UP_400 = "rate_shock_up_400"
WHATIF_CEDI_DEPRECIATION_20 = "cedi_depreciation_20"
WHATIF_DEFAULT_SPIKE = "default_spike"
WHATIF_MPR_CUT_200 = "mpr_cut_200"
WHATIF_SHOCK_CODES = (
    WHATIF_RATE_SHOCK_UP_400,
    WHATIF_CEDI_DEPRECIATION_20,
    WHATIF_DEFAULT_SPIKE,
    WHATIF_MPR_CUT_200,
)
# Shock effect keys applied on top of the base assumptions.
WHATIF_SHOCKS: dict[str, dict[str, Decimal]] = {
    WHATIF_RATE_SHOCK_UP_400: {
        "nim_delta": Decimal("-0.5"),
        "securities_mtm_haircut_pct": Decimal("6"),
    },
    WHATIF_CEDI_DEPRECIATION_20: {
        "fx_depreciation_pct": Decimal("20"),
        "credit_loss_delta": Decimal("0.3"),
    },
    WHATIF_DEFAULT_SPIKE: {"credit_loss_multiplier": Decimal("2.5")},
    WHATIF_MPR_CUT_200: {"nim_delta": Decimal("-0.4"), "loan_growth_delta": Decimal("4")},
}


class ProjectionError(Exception):
    """The projection cannot produce a coherent balance sheet."""

    def __init__(self, code: str, message: str | None = None) -> None:
        super().__init__(message or code)
        self.code = code


class UnknownShockError(Exception):
    """An unrecognized what-if shock code was requested."""

    def __init__(self, shock_code: str) -> None:
        super().__init__(f"Unknown what-if shock code '{shock_code}'.")
        self.shock_code = shock_code


@dataclass(frozen=True)
class ForecastFact:
    """One bank financial fact, carrying the union of engine-relevant fields."""

    fact_group: str
    category: str
    amount: Decimal
    risk_weight_code: str | None = None
    hqla_level: str | None = None
    ccf_pct: Decimal | None = None
    income_year: int | None = None
    capital_tier: str | None = None
    is_deduction: bool = False
    side: str | None = None
    cash_derived: bool = False


@dataclass(frozen=True)
class ForecastAssumptions:
    """Scenario assumptions; all values are Decimal percentages (pp for shift)."""

    loan_growth_pct: Decimal
    deposit_growth_pct: Decimal
    nim_pct: Decimal
    cost_to_income_pct: Decimal
    credit_loss_rate_pct: Decimal
    fx_depreciation_pct: Decimal
    dividend_payout_pct: Decimal
    fee_income_pct_assets: Decimal = DEFAULT_FEE_INCOME_PCT_ASSETS
    tax_rate_pct: Decimal = DEFAULT_TAX_RATE_PCT
    securities_shift_pp: Decimal = DEFAULT_SECURITIES_SHIFT_PP


@dataclass(frozen=True)
class ForecastParams:
    """Unshocked baseline parameter sets for both downstream engines."""

    liquidity: LiquidityParams
    capital: CapitalParams


@dataclass(frozen=True)
class ProjectionYear:
    year: int
    period_label: str
    total_assets: Decimal
    loans: Decimal
    securities: Decimal
    cash: Decimal
    deposits: Decimal
    borrowings_plug: Decimal
    equity: Decimal
    nii: Decimal
    fees: Decimal
    total_income: Decimal
    opex: Decimal
    credit_losses: Decimal
    net_income: Decimal
    dividends: Decimal
    roe_pct: Decimal | None
    car_pct: Decimal
    tier1_ratio_pct: Decimal
    cet1_ratio_pct: Decimal
    lcr_pct: Decimal
    nsfr_pct: Decimal


@dataclass(frozen=True)
class ProjectionSummary:
    avg_roe_pct: Decimal
    year5_car_pct: Decimal
    year5_lcr_pct: Decimal
    year5_nsfr_pct: Decimal
    cumulative_net_income: Decimal
    min_car_pct: Decimal
    min_lcr_pct: Decimal
    min_nsfr_pct: Decimal


@dataclass(frozen=True)
class ProjectionResult:
    assumptions: ForecastAssumptions
    years: tuple[ProjectionYear, ...]
    summary: ProjectionSummary


@dataclass(frozen=True)
class OptimizerDecision:
    loan_growth_pct: Decimal
    securities_shift_pp: Decimal
    deposit_premium_bps: int
    dividend_payout_pct: Decimal
    deposit_growth_delta_pct: Decimal
    nim_delta_pct: Decimal


@dataclass(frozen=True)
class OptimizerConstraints:
    car_min_pct: Decimal
    lcr_min_pct: Decimal
    nsfr_min_pct: Decimal


@dataclass(frozen=True)
class ConstraintStatus:
    constraint: str
    minimum_pct: Decimal
    observed_min_pct: Decimal
    passed: bool


@dataclass(frozen=True)
class OptimizerCandidateResult:
    decision: OptimizerDecision
    summary: ProjectionSummary
    constraint_status: tuple[ConstraintStatus, ...]
    feasible: bool


@dataclass(frozen=True)
class OptimizerResult:
    candidates_evaluated: int
    feasible_count: int
    top: tuple[OptimizerCandidateResult, ...]
    binding_constraint_histogram: dict[str, int]


@dataclass(frozen=True)
class WhatIfYearDelta:
    year: int
    car_delta_pp: Decimal
    lcr_delta_pp: Decimal
    nsfr_delta_pp: Decimal
    net_income_delta: Decimal


@dataclass(frozen=True)
class WhatIfMetricComparison:
    base: Decimal
    shocked: Decimal
    delta: Decimal


@dataclass(frozen=True)
class WhatIfYear5Comparison:
    car_pct: WhatIfMetricComparison
    lcr_pct: WhatIfMetricComparison
    nsfr_pct: WhatIfMetricComparison
    net_income: WhatIfMetricComparison


@dataclass(frozen=True)
class WhatIfResult:
    shock_code: str
    base: ProjectionResult
    shocked: ProjectionResult
    deltas: tuple[WhatIfYearDelta, ...]
    year5: WhatIfYear5Comparison


def money(value: Decimal) -> Decimal:
    return value.quantize(MONEY, rounding=ROUND_HALF_UP)


def ratio_pct(value: Decimal) -> Decimal:
    return value.quantize(RATIO_PCT, rounding=ROUND_HALF_UP)


@dataclass
class _State:
    """Mutable per-year projection state; every amount is quantized to MONEY."""

    cash: dict[str, Decimal]
    securities: dict[str, Decimal]
    constant_assets: dict[str, Decimal]
    deposits: dict[str, Decimal]
    constant_liabilities: dict[str, Decimal]
    borrowings: Decimal
    constant_equity: dict[str, Decimal]
    equity: Decimal
    loans: dict[str, Decimal]
    off_balance: dict[str, Decimal]
    inflows: dict[str, Decimal]
    securities_group: dict[str, Decimal]
    market: dict[str, Decimal]
    components: dict[str, Decimal]
    gi_history: list[tuple[int, Decimal]]

    def loans_total(self) -> Decimal:
        return sum(self.loans.values(), _ZERO)

    def securities_total(self) -> Decimal:
        return sum(self.securities.values(), _ZERO)

    def cash_total(self) -> Decimal:
        return sum(self.cash.values(), _ZERO)

    def deposits_total(self) -> Decimal:
        return sum(self.deposits.values(), _ZERO)

    def assets_total(self) -> Decimal:
        return (
            self.cash_total()
            + self.securities_total()
            + self.loans_total()
            + sum(self.constant_assets.values(), _ZERO)
        )

    def funding_without_borrowings(self) -> Decimal:
        return (
            self.deposits_total()
            + sum(self.constant_liabilities.values(), _ZERO)
            + sum(self.constant_equity.values(), _ZERO)
            + self.equity
        )


@dataclass(frozen=True)
class _Meta:
    """Immutable per-category attributes carried over from the as-of facts."""

    loan_risk_weights: Mapping[str, str | None]
    off_balance_ccf: Mapping[str, Decimal | None]
    off_balance_risk_weights: Mapping[str, str | None]
    securities_group_hqla: Mapping[str, str | None]
    securities_group_cash_derived: Mapping[str, bool]
    component_tiers: Mapping[str, str | None]
    component_deductions: Mapping[str, bool]


def project(  # noqa: PLR0913, PLR0915
    facts: Sequence[ForecastFact],
    params: ForecastParams,
    assumptions: ForecastAssumptions,
    years: int = PROJECTION_YEARS,
    *,
    securities_mtm_haircut_pct: Decimal = _ZERO,
    period_labels: Sequence[str] | None = None,
) -> ProjectionResult:
    """Project the balance sheet ``years`` years forward from the as-of facts.

    ``period_labels`` (optional) supplies ``years + 1`` display labels; the
    default is ``Year 0`` .. ``Year N``. ``securities_mtm_haircut_pct`` is the
    one-off what-if mark-to-market haircut applied to marketable securities at
    t=1. Missing engine parameters propagate as the downstream engines'
    ``MissingParameterError``.
    """
    if years < 1:
        raise ProjectionError("invalid_horizon", "The projection horizon must be at least 1 year.")
    labels = _resolve_labels(period_labels, years)
    state, meta = _parse_facts(facts)

    loan_factor = _ONE + assumptions.loan_growth_pct / _HUNDRED
    deposit_factor = _ONE + assumptions.deposit_growth_pct / _HUNDRED
    securities_factor = (
        _ONE + (assumptions.deposit_growth_pct + assumptions.securities_shift_pp) / _HUNDRED
    )
    fx_factor = _ONE + assumptions.fx_depreciation_pct / _HUNDRED
    haircut_factor = (_HUNDRED - securities_mtm_haircut_pct) / _HUNDRED

    rows: list[ProjectionYear] = [_year_zero_row(state, meta, params, labels[0])]
    earning_assets_prev = state.loans_total() + state.securities_total()
    assets_prev = state.assets_total()
    equity_prev = state.equity

    for year in range(1, years + 1):
        _scale_in_place(state.loans, loan_factor)
        _scale_in_place(state.off_balance, loan_factor)
        _scale_in_place(state.inflows, loan_factor)
        _scale_in_place(state.deposits, deposit_factor)
        _scale_in_place(state.securities, securities_factor)
        _scale_in_place(state.cash, deposit_factor)
        for category in state.securities_group:
            factor = (
                deposit_factor
                if meta.securities_group_cash_derived.get(category, False)
                else securities_factor
            )
            state.securities_group[category] = money(state.securities_group[category] * factor)
        if year == 1:
            if securities_mtm_haircut_pct != _ZERO:
                _scale_in_place(state.securities, haircut_factor)
                for category in state.securities_group:
                    if not meta.securities_group_cash_derived.get(category, False):
                        state.securities_group[category] = money(
                            state.securities_group[category] * haircut_factor
                        )
            if assumptions.fx_depreciation_pct != _ZERO:
                _scale_in_place(state.market, fx_factor)

        earning_assets = state.loans_total() + state.securities_total()
        nii = money(assumptions.nim_pct / _HUNDRED * (earning_assets_prev + earning_assets) / _TWO)
        assets_pre_plug = state.assets_total()
        fees = money(
            assumptions.fee_income_pct_assets / _HUNDRED * (assets_prev + assets_pre_plug) / _TWO
        )
        total_income = nii + fees
        opex = money(assumptions.cost_to_income_pct / _HUNDRED * total_income)
        credit_losses = money(assumptions.credit_loss_rate_pct / _HUNDRED * state.loans_total())
        pre_tax = total_income - opex - credit_losses
        tax = money(assumptions.tax_rate_pct / _HUNDRED * max(pre_tax, _ZERO))
        net_income = pre_tax - tax
        dividends = money(assumptions.dividend_payout_pct / _HUNDRED * max(net_income, _ZERO))
        retained = net_income - dividends

        state.equity = state.equity + retained
        state.components[RETAINED_EARNINGS_CATEGORY] = (
            state.components.get(RETAINED_EARNINGS_CATEGORY, _ZERO) + retained
        )

        _apply_funding_plug(state)
        assets_total = state.assets_total()
        if assets_total != state.funding_without_borrowings() + state.borrowings:
            raise ProjectionError(
                "balance_sheet_untied",
                f"Year {year}: projected assets do not equal liabilities plus equity.",
            )

        state.gi_history.append((state.gi_history[-1][0] + 1, total_income))

        ratios = _regulatory_ratios(state, meta, params)
        average_equity = (equity_prev + state.equity) / _TWO
        if average_equity <= _ZERO:
            raise ProjectionError(
                "non_positive_equity",
                f"Year {year}: average equity must be positive to compute the ROE.",
            )
        roe = ratio_pct(net_income / average_equity * _HUNDRED)

        rows.append(
            ProjectionYear(
                year=year,
                period_label=labels[year],
                total_assets=assets_total,
                loans=state.loans_total(),
                securities=state.securities_total(),
                cash=state.cash_total(),
                deposits=state.deposits_total(),
                borrowings_plug=state.borrowings,
                equity=state.equity,
                nii=nii,
                fees=fees,
                total_income=total_income,
                opex=opex,
                credit_losses=credit_losses,
                net_income=net_income,
                dividends=dividends,
                roe_pct=roe,
                car_pct=ratios[0],
                tier1_ratio_pct=ratios[1],
                cet1_ratio_pct=ratios[2],
                lcr_pct=ratios[3],
                nsfr_pct=ratios[4],
            )
        )
        earning_assets_prev = earning_assets
        assets_prev = assets_total
        equity_prev = state.equity

    return ProjectionResult(
        assumptions=assumptions,
        years=tuple(rows),
        summary=_summarize(rows),
    )


def enumerate_optimizer_candidates() -> list[OptimizerDecision]:
    """Enumerate the 108-candidate strategic decision grid in a stable order."""
    decisions: list[OptimizerDecision] = []
    for loan_growth, shift, premium_bps, payout in itertools.product(
        OPTIMIZER_LOAN_GROWTH_GRID,
        OPTIMIZER_SECURITIES_SHIFT_GRID,
        OPTIMIZER_DEPOSIT_PREMIUM_GRID,
        OPTIMIZER_DIVIDEND_PAYOUT_GRID,
    ):
        deposit_delta, nim_delta = DEPOSIT_PREMIUM_EFFECTS[premium_bps]
        decisions.append(
            OptimizerDecision(
                loan_growth_pct=loan_growth,
                securities_shift_pp=shift,
                deposit_premium_bps=premium_bps,
                dividend_payout_pct=payout,
                deposit_growth_delta_pct=deposit_delta,
                nim_delta_pct=nim_delta,
            )
        )
    return decisions


def apply_decision(
    base_assumptions: ForecastAssumptions, decision: OptimizerDecision
) -> ForecastAssumptions:
    """Overlay one optimizer decision onto the base scenario assumptions."""
    return replace(
        base_assumptions,
        loan_growth_pct=decision.loan_growth_pct,
        deposit_growth_pct=base_assumptions.deposit_growth_pct + decision.deposit_growth_delta_pct,
        nim_pct=base_assumptions.nim_pct + decision.nim_delta_pct,
        dividend_payout_pct=decision.dividend_payout_pct,
        securities_shift_pp=decision.securities_shift_pp,
    )


def run_optimizer(
    facts: Sequence[ForecastFact],
    params: ForecastParams,
    base_assumptions: ForecastAssumptions,
    constraints: OptimizerConstraints,
) -> OptimizerResult:
    """Project every decision candidate and rank the feasible set by average ROE.

    A candidate is feasible iff EVERY projected year satisfies
    ``car >= car_min``, ``lcr >= lcr_min``, and ``nsfr >= nsfr_min``.
    Candidates whose projection raises ``ProjectionError`` are counted as
    infeasible under the ``projection_error`` histogram bucket.
    """
    decisions = enumerate_optimizer_candidates()
    histogram = {code: 0 for code in OPTIMIZER_CONSTRAINT_CODES}
    feasible: list[OptimizerCandidateResult] = []
    for decision in decisions:
        try:
            projection = project(facts, params, apply_decision(base_assumptions, decision))
        except ProjectionError:
            histogram["projection_error"] = histogram.get("projection_error", 0) + 1
            continue
        statuses = _constraint_statuses(projection.summary, constraints)
        is_feasible = all(status.passed for status in statuses)
        for status in statuses:
            if not status.passed:
                histogram[status.constraint] += 1
        if is_feasible:
            feasible.append(
                OptimizerCandidateResult(
                    decision=decision,
                    summary=projection.summary,
                    constraint_status=statuses,
                    feasible=True,
                )
            )
    feasible.sort(
        key=lambda candidate: (
            -candidate.summary.avg_roe_pct,
            candidate.decision.loan_growth_pct,
            candidate.decision.securities_shift_pp,
            candidate.decision.deposit_premium_bps,
            candidate.decision.dividend_payout_pct,
        )
    )
    return OptimizerResult(
        candidates_evaluated=len(decisions),
        feasible_count=len(feasible),
        top=tuple(feasible[:OPTIMIZER_TOP_LIMIT]),
        binding_constraint_histogram=histogram,
    )


def apply_whatif_shock(
    shock_code: str, base_assumptions: ForecastAssumptions
) -> tuple[ForecastAssumptions, Decimal]:
    """Resolve one shock code into shocked assumptions plus the MTM haircut."""
    shock = WHATIF_SHOCKS.get(shock_code)
    if shock is None:
        raise UnknownShockError(shock_code)
    shocked = base_assumptions
    if "nim_delta" in shock:
        shocked = replace(shocked, nim_pct=shocked.nim_pct + shock["nim_delta"])
    if "loan_growth_delta" in shock:
        shocked = replace(
            shocked, loan_growth_pct=shocked.loan_growth_pct + shock["loan_growth_delta"]
        )
    if "credit_loss_delta" in shock:
        shocked = replace(
            shocked,
            credit_loss_rate_pct=shocked.credit_loss_rate_pct + shock["credit_loss_delta"],
        )
    if "credit_loss_multiplier" in shock:
        shocked = replace(
            shocked,
            credit_loss_rate_pct=shocked.credit_loss_rate_pct * shock["credit_loss_multiplier"],
        )
    if "fx_depreciation_pct" in shock:
        shocked = replace(shocked, fx_depreciation_pct=shock["fx_depreciation_pct"])
    return shocked, shock.get("securities_mtm_haircut_pct", _ZERO)


def run_whatif(
    shock_code: str,
    facts: Sequence[ForecastFact],
    params: ForecastParams,
    base_assumptions: ForecastAssumptions,
    *,
    period_labels: Sequence[str] | None = None,
) -> WhatIfResult:
    """Project the base scenario and one shocked variant, and diff them."""
    shocked_assumptions, haircut_pct = apply_whatif_shock(shock_code, base_assumptions)
    base = project(facts, params, base_assumptions, period_labels=period_labels)
    shocked = project(
        facts,
        params,
        shocked_assumptions,
        securities_mtm_haircut_pct=haircut_pct,
        period_labels=period_labels,
    )
    deltas = tuple(
        WhatIfYearDelta(
            year=base_row.year,
            car_delta_pp=shocked_row.car_pct - base_row.car_pct,
            lcr_delta_pp=shocked_row.lcr_pct - base_row.lcr_pct,
            nsfr_delta_pp=shocked_row.nsfr_pct - base_row.nsfr_pct,
            net_income_delta=shocked_row.net_income - base_row.net_income,
        )
        for base_row, shocked_row in zip(base.years, shocked.years, strict=True)
    )
    base_final = base.years[-1]
    shocked_final = shocked.years[-1]
    return WhatIfResult(
        shock_code=shock_code,
        base=base,
        shocked=shocked,
        deltas=deltas,
        year5=WhatIfYear5Comparison(
            car_pct=_comparison(base_final.car_pct, shocked_final.car_pct),
            lcr_pct=_comparison(base_final.lcr_pct, shocked_final.lcr_pct),
            nsfr_pct=_comparison(base_final.nsfr_pct, shocked_final.nsfr_pct),
            net_income=_comparison(base_final.net_income, shocked_final.net_income),
        ),
    )


def _comparison(base: Decimal, shocked: Decimal) -> WhatIfMetricComparison:
    return WhatIfMetricComparison(base=base, shocked=shocked, delta=shocked - base)


def _constraint_statuses(
    summary: ProjectionSummary, constraints: OptimizerConstraints
) -> tuple[ConstraintStatus, ...]:
    checks = (
        (CONSTRAINT_CAR, constraints.car_min_pct, summary.min_car_pct),
        (CONSTRAINT_LCR, constraints.lcr_min_pct, summary.min_lcr_pct),
        (CONSTRAINT_NSFR, constraints.nsfr_min_pct, summary.min_nsfr_pct),
    )
    return tuple(
        ConstraintStatus(
            constraint=code,
            minimum_pct=minimum,
            observed_min_pct=observed,
            passed=observed >= minimum,
        )
        for code, minimum, observed in checks
    )


def _resolve_labels(period_labels: Sequence[str] | None, years: int) -> list[str]:
    if period_labels is None:
        return [f"Year {year}" for year in range(years + 1)]
    if len(period_labels) != years + 1:
        raise ProjectionError(
            "invalid_period_labels",
            f"Expected {years + 1} period labels, received {len(period_labels)}.",
        )
    return list(period_labels)


def _scale_in_place(amounts: dict[str, Decimal], factor: Decimal) -> None:
    for category, amount in amounts.items():
        amounts[category] = money(amount * factor)


def _apply_funding_plug(state: _State) -> None:
    """Balance the funding side; borrowings plug first, then cash absorption."""
    plug = state.assets_total() - state.funding_without_borrowings()
    if plug >= _ZERO:
        state.borrowings = plug
        return
    # Funding surplus: borrowings floor at zero and the BoG excess-reserve row
    # absorbs the residual so assets equal liabilities plus equity exactly.
    state.borrowings = _ZERO
    adjustment = -plug
    state.cash[CASH_PLUG_CATEGORY] = state.cash.get(CASH_PLUG_CATEGORY, _ZERO) + adjustment
    mirror = CASH_HQLA_MIRRORS.get(CASH_PLUG_CATEGORY)
    if mirror is not None and mirror in state.securities_group:
        state.securities_group[mirror] = state.securities_group[mirror] + adjustment
    assets_total = state.assets_total()
    if (
        state.cash_total() < money(assets_total * CASH_FLOOR_PCT / _HUNDRED)
        or state.cash[CASH_PLUG_CATEGORY] < _ZERO
    ):
        raise ProjectionError(
            "balance_sheet_infeasible",
            "The funding plug pushed cash below the 5%-of-assets floor.",
        )


def _parse_facts(facts: Sequence[ForecastFact]) -> tuple[_State, _Meta]:  # noqa: PLR0912, PLR0915
    cash: dict[str, Decimal] = {}
    securities: dict[str, Decimal] = {}
    constant_assets: dict[str, Decimal] = {}
    deposits: dict[str, Decimal] = {}
    constant_liabilities: dict[str, Decimal] = {}
    constant_equity: dict[str, Decimal] = {}
    borrowings = _ZERO
    equity = _ZERO
    loans: dict[str, Decimal] = {}
    off_balance: dict[str, Decimal] = {}
    inflows: dict[str, Decimal] = {}
    securities_group: dict[str, Decimal] = {}
    market: dict[str, Decimal] = {}
    components: dict[str, Decimal] = {}
    income_facts: list[tuple[int, Decimal]] = []

    loan_risk_weights: dict[str, str | None] = {}
    off_balance_ccf: dict[str, Decimal | None] = {}
    off_balance_risk_weights: dict[str, str | None] = {}
    securities_group_hqla: dict[str, str | None] = {}
    securities_group_cash_derived: dict[str, bool] = {}
    component_tiers: dict[str, str | None] = {}
    component_deductions: dict[str, bool] = {}

    for fact in facts:
        amount = money(fact.amount)
        if fact.fact_group == FACT_GROUP_BALANCE_SHEET:
            if fact.side == "asset":
                if fact.category in CASH_CATEGORIES:
                    cash[fact.category] = amount
                elif fact.category in SECURITIES_BS_CATEGORIES:
                    securities[fact.category] = amount
                elif fact.category != LOANS_GROSS_CATEGORY:
                    # other_assets plus any unrecognized asset row stays constant;
                    # loans_gross is re-derived from the loan exposures.
                    constant_assets[fact.category] = amount
            elif fact.side == "liability":
                if fact.category == TERM_BORROWINGS_CATEGORY:
                    borrowings = amount
                elif fact.category == SECURED_FUNDING_CATEGORY:
                    constant_liabilities[fact.category] = amount
                else:
                    deposits[fact.category] = amount
            elif fact.side == "equity":
                if fact.category == CAPITAL_TOTAL_CATEGORY:
                    equity = amount
                else:
                    constant_equity[fact.category] = amount
        elif fact.fact_group == FACT_GROUP_LOAN_EXPOSURE:
            loans[fact.category] = amount
            loan_risk_weights[fact.category] = fact.risk_weight_code
        elif fact.fact_group == FACT_GROUP_OFF_BALANCE:
            off_balance[fact.category] = amount
            off_balance_ccf[fact.category] = fact.ccf_pct
            off_balance_risk_weights[fact.category] = fact.risk_weight_code
        elif fact.fact_group == FACT_GROUP_LCR_INFLOW:
            inflows[fact.category] = amount
        elif fact.fact_group == FACT_GROUP_SECURITIES:
            securities_group[fact.category] = amount
            securities_group_hqla[fact.category] = fact.hqla_level
            securities_group_cash_derived[fact.category] = fact.cash_derived
        elif fact.fact_group == FACT_GROUP_MARKET_RISK:
            market[fact.category] = amount
        elif fact.fact_group == FACT_GROUP_OPERATIONAL_INCOME:
            if fact.income_year is not None:
                income_facts.append((fact.income_year, amount))
        elif fact.fact_group == FACT_GROUP_CAPITAL_COMPONENT:
            components[fact.category] = amount
            component_tiers[fact.category] = fact.capital_tier
            component_deductions[fact.category] = fact.is_deduction

    if RETAINED_EARNINGS_CATEGORY not in components:
        components[RETAINED_EARNINGS_CATEGORY] = _ZERO
        component_tiers[RETAINED_EARNINGS_CATEGORY] = TIER_CET1
        component_deductions[RETAINED_EARNINGS_CATEGORY] = False

    state = _State(
        cash=cash,
        securities=securities,
        constant_assets=constant_assets,
        deposits=deposits,
        constant_liabilities=constant_liabilities,
        borrowings=borrowings,
        constant_equity=constant_equity,
        equity=equity,
        loans=loans,
        off_balance=off_balance,
        inflows=inflows,
        securities_group=securities_group,
        market=market,
        components=components,
        gi_history=sorted(income_facts),
    )
    if not state.gi_history:
        # The capital engine requires at least one positive gross-income year;
        # give the roll-forward a year to append onto so it fails loudly there.
        state.gi_history = [(0, _ZERO)]
    meta = _Meta(
        loan_risk_weights=loan_risk_weights,
        off_balance_ccf=off_balance_ccf,
        off_balance_risk_weights=off_balance_risk_weights,
        securities_group_hqla=securities_group_hqla,
        securities_group_cash_derived=securities_group_cash_derived,
        component_tiers=component_tiers,
        component_deductions=component_deductions,
    )
    return state, meta


def _state_facts(state: _State, meta: _Meta) -> list[ForecastFact]:  # noqa: PLR0912
    """Materialize the current state as a full bank fact set for the engines."""
    rows: list[ForecastFact] = []
    for category, amount in sorted(state.cash.items()):
        rows.append(_bs_fact(category, amount, "asset"))
    for category, amount in sorted(state.securities.items()):
        rows.append(_bs_fact(category, amount, "asset"))
    rows.append(_bs_fact(LOANS_GROSS_CATEGORY, state.loans_total(), "asset"))
    for category, amount in sorted(state.constant_assets.items()):
        rows.append(_bs_fact(category, amount, "asset"))
    for category, amount in sorted(state.deposits.items()):
        rows.append(_bs_fact(category, amount, "liability"))
    for category, amount in sorted(state.constant_liabilities.items()):
        rows.append(_bs_fact(category, amount, "liability"))
    rows.append(_bs_fact(TERM_BORROWINGS_CATEGORY, state.borrowings, "liability"))
    for category, amount in sorted(state.constant_equity.items()):
        rows.append(_bs_fact(category, amount, "equity"))
    rows.append(_bs_fact(CAPITAL_TOTAL_CATEGORY, state.equity, "equity"))
    for category, amount in sorted(state.loans.items()):
        rows.append(
            ForecastFact(
                fact_group=FACT_GROUP_LOAN_EXPOSURE,
                category=category,
                amount=amount,
                risk_weight_code=meta.loan_risk_weights.get(category),
            )
        )
    for category, amount in sorted(state.off_balance.items()):
        rows.append(
            ForecastFact(
                fact_group=FACT_GROUP_OFF_BALANCE,
                category=category,
                amount=amount,
                ccf_pct=meta.off_balance_ccf.get(category),
                risk_weight_code=meta.off_balance_risk_weights.get(category),
            )
        )
    for category, amount in sorted(state.inflows.items()):
        rows.append(
            ForecastFact(fact_group=FACT_GROUP_LCR_INFLOW, category=category, amount=amount)
        )
    for category, amount in sorted(state.securities_group.items()):
        rows.append(
            ForecastFact(
                fact_group=FACT_GROUP_SECURITIES,
                category=category,
                amount=amount,
                hqla_level=meta.securities_group_hqla.get(category),
                cash_derived=meta.securities_group_cash_derived.get(category, False),
            )
        )
    for category, amount in sorted(state.market.items()):
        rows.append(
            ForecastFact(fact_group=FACT_GROUP_MARKET_RISK, category=category, amount=amount)
        )
    for income_year, amount in state.gi_history[-GI_TRAILING_YEARS:]:
        rows.append(
            ForecastFact(
                fact_group=FACT_GROUP_OPERATIONAL_INCOME,
                category=f"gross_income_{income_year}",
                amount=amount,
                income_year=income_year,
            )
        )
    for category, amount in sorted(state.components.items()):
        rows.append(
            ForecastFact(
                fact_group=FACT_GROUP_CAPITAL_COMPONENT,
                category=category,
                amount=amount,
                capital_tier=meta.component_tiers.get(category),
                is_deduction=meta.component_deductions.get(category, False),
            )
        )
    return rows


def _bs_fact(category: str, amount: Decimal, side: str) -> ForecastFact:
    return ForecastFact(
        fact_group=FACT_GROUP_BALANCE_SHEET, category=category, amount=amount, side=side
    )


def _to_liquidity_facts(rows: Sequence[ForecastFact]) -> tuple[LiquidityFact, ...]:
    relevant = (
        FACT_GROUP_BALANCE_SHEET,
        FACT_GROUP_SECURITIES,
        FACT_GROUP_LOAN_EXPOSURE,
        FACT_GROUP_OFF_BALANCE,
        FACT_GROUP_LCR_INFLOW,
    )
    return tuple(
        LiquidityFact(
            fact_group=row.fact_group,
            category=row.category,
            amount=row.amount,
            hqla_level=row.hqla_level,
            side=row.side,
            cash_derived=row.cash_derived,
        )
        for row in rows
        if row.fact_group in relevant
    )


def _to_capital_facts(rows: Sequence[ForecastFact]) -> tuple[CapitalFact, ...]:
    relevant = (
        FACT_GROUP_BALANCE_SHEET,
        FACT_GROUP_LOAN_EXPOSURE,
        FACT_GROUP_OFF_BALANCE,
        FACT_GROUP_MARKET_RISK,
        FACT_GROUP_OPERATIONAL_INCOME,
        FACT_GROUP_CAPITAL_COMPONENT,
    )
    return tuple(
        CapitalFact(
            fact_group=row.fact_group,
            category=row.category,
            amount=row.amount,
            risk_weight_code=row.risk_weight_code,
            ccf_pct=row.ccf_pct,
            income_year=row.income_year,
            capital_tier=row.capital_tier,
            is_deduction=row.is_deduction,
            side=row.side,
        )
        for row in rows
        if row.fact_group in relevant
    )


def _regulatory_ratios(
    state: _State, meta: _Meta, params: ForecastParams
) -> tuple[Decimal, Decimal, Decimal, Decimal, Decimal]:
    """Return (car, tier1, cet1, lcr, nsfr) computed by the downstream engines."""
    rows = _state_facts(state, meta)
    liquidity_facts = _to_liquidity_facts(rows)
    capital_facts = _to_capital_facts(rows)
    lcr = compute_lcr(liquidity_facts, params.liquidity)
    nsfr = compute_nsfr(liquidity_facts, params.liquidity)
    rwa = compute_rwa(capital_facts, params.capital)
    ratios = compute_capital_ratios(capital_facts, rwa, params.capital)
    return (
        ratios.car_pct,
        ratios.tier1_ratio_pct,
        ratios.cet1_ratio_pct,
        lcr.lcr_pct,
        nsfr.nsfr_pct,
    )


def _year_zero_row(
    state: _State, meta: _Meta, params: ForecastParams, label: str
) -> ProjectionYear:
    ratios = _regulatory_ratios(state, meta, params)
    return ProjectionYear(
        year=0,
        period_label=label,
        total_assets=state.assets_total(),
        loans=state.loans_total(),
        securities=state.securities_total(),
        cash=state.cash_total(),
        deposits=state.deposits_total(),
        borrowings_plug=state.borrowings,
        equity=state.equity,
        nii=_ZERO,
        fees=_ZERO,
        total_income=_ZERO,
        opex=_ZERO,
        credit_losses=_ZERO,
        net_income=_ZERO,
        dividends=_ZERO,
        roe_pct=None,
        car_pct=ratios[0],
        tier1_ratio_pct=ratios[1],
        cet1_ratio_pct=ratios[2],
        lcr_pct=ratios[3],
        nsfr_pct=ratios[4],
    )


def _summarize(rows: Sequence[ProjectionYear]) -> ProjectionSummary:
    """Summarize the projection; minimums cover the projected years (1..N)."""
    projected = [row for row in rows if row.year > 0]
    final = projected[-1]
    roe_values = [row.roe_pct for row in projected if row.roe_pct is not None]
    avg_roe = ratio_pct(sum(roe_values, _ZERO) / Decimal(len(roe_values)))
    return ProjectionSummary(
        avg_roe_pct=avg_roe,
        year5_car_pct=final.car_pct,
        year5_lcr_pct=final.lcr_pct,
        year5_nsfr_pct=final.nsfr_pct,
        cumulative_net_income=sum((row.net_income for row in projected), _ZERO),
        min_car_pct=min(row.car_pct for row in projected),
        min_lcr_pct=min(row.lcr_pct for row in projected),
        min_nsfr_pct=min(row.nsfr_pct for row in projected),
    )
