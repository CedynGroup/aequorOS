"""3-year enterprise stress projection (docs/stress.md §3.4, Phase 2 item 2).

The directive's core Part IV deliverable (¶68, ¶75, ¶77): project the balance
sheet, P&L, regulatory capital (CET1/AT1/Tier2 with the CRD caps + deductions)
and RWA (Pillar-1 credit/operational/market + Pillar-2) forward at least three
years under a BASE and an ADVERSE macro scenario, and assess whether the bank
remains above ALL minima — CAR 13%, CET1, Tier1, leverage, paid-up (¶77).

This is a PURE, deterministic engine. It does **not** reinvent the capital
tiering or the balance-sheet roll-forward: it reuses the validated forecasting
engine's roll-forward primitives (``app.domain.forecasting.engine``) so the
projected book is byte-identical in structure to the production 5-year forecast,
and it drives the pure capital/liquidity engines (``compute_rwa``,
``compute_capital_ratios``, ``compute_lcr``, ``compute_nsfr``) for every
projected year — the same engines the standalone official runs use. Where the
forecasting engine hides its per-year fact set behind aggregate rows, this engine
re-derives the full ``RwaResult``/``CapitalRatiosResult`` per year so the
Appendix II builders (``app.domain.stress.appendix_ii``) have the granular
capital build and RWA-by-type they need.

**Base vs stress legs.** The base leg runs the bank's approved business plan
(``ForecastAssumptions``) held constant across the horizon. The stress leg
perturbs the plan, per year, by the scenario's stress-vs-base macro deltas
through a documented elasticity register (GDP → loan/deposit growth; interest
rate → NIM; inflation → cost/income; PD/LGD multipliers → cost of risk). Because
every perturbation is a stress-MINUS-base DELTA, a base scenario (stress == base
everywhere) leaves the two legs identical — a zero stress impact, which is
correct.

**IFRS 9 ECL under stress (¶48–49, AppI¶5–6).** The per-year impairment is the
plan cost of risk scaled by that year's macro PD/LGD multipliers. This wires the
two directive modes into the projection: **Perfect Foresight** — each of the ≥3
projected years knows its own macro from day one (the multipliers are computed
per year off the full authored path); **Single Scenario** — the stress path
carries 100% weight, with no probability blend across scenarios.

**Pre-management-action.** This engine produces the "results WITHOUT management
actions" the directive requires as a distinct output (¶67(f), AppII Table 1):
dividends follow the ordinary plan policy (a dividend cut is a management action),
paid-up capital stays constant (an issuance is a management action), and no RWA
relief or asset sale is modelled. The Phase 3 overlay layers management actions
on top of this projection.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from decimal import ROUND_HALF_UP, Decimal

from app.domain.capital.engine import (
    GENERAL_PROVISIONS_CATEGORY,
    TIER_AT1,
    TIER_CET1,
    TIER_T2,
    CapitalParams,
    CapitalRatiosResult,
    RwaResult,
    compute_capital_ratios,
    compute_rwa,
)
from app.domain.forecasting.engine import (
    RETAINED_EARNINGS_CATEGORY,
    ForecastAssumptions,
    ForecastFact,
    ForecastParams,
    _apply_funding_plug,
    _Meta,
    _parse_facts,
    _scale_in_place,
    _State,
    _state_facts,
    _to_capital_facts,
    _to_liquidity_facts,
)
from app.domain.liquidity.engine import compute_lcr, compute_nsfr
from app.domain.stress.translation import MacroPathPoint, ShockMapping, translate

MONEY = Decimal("0.0001")
RATIO_PCT = Decimal("0.000001")
_HUNDRED = Decimal("100")
_ZERO = Decimal("0")
_ONE = Decimal("1")
_TWO = Decimal("2")

HORIZON_DEFAULT = 3
MIN_HORIZON = 3  # ¶75 mandates a ≥3-year horizon.

# --- macro → business-plan-assumption elasticities (stress leg) --------------
# Every coefficient multiplies a stress-MINUS-base macro delta, so a base
# scenario yields a zero perturbation (base leg == stress leg). See §3.2/§3.4.

#: Loan growth (pp) per unit of GDP-growth delta (fraction). 100 ⇒ a −5pp GDP
#: shock (delta −0.05) cuts loan growth 5pp.
LOAN_GROWTH_PER_GDP = Decimal("100")
#: Deposit growth (pp) per unit of GDP-growth delta. Deposits track nominal
#: activity slightly less than credit.
DEPOSIT_GROWTH_PER_GDP = Decimal("80")
#: NIM (pp) per unit of interest-rate delta. −10 ⇒ a +5pp rate rise (delta
#: +0.05) compresses the margin 0.5pp (liabilities reprice faster short-term).
NIM_PER_RATE = Decimal("-10")
#: Cost-to-income (pp) per unit of inflation delta. 20 ⇒ a +5pp inflation shock
#: lifts the cost ratio 1pp.
CTI_PER_INFLATION = Decimal("20")

#: Minimum growth the elasticities may drive an assumption to (a severe stress
#: deleverages the book but never to an implausible collapse).
_MIN_GROWTH_PCT = Decimal("-40")


def money(value: Decimal) -> Decimal:
    return value.quantize(MONEY, rounding=ROUND_HALF_UP)


def ratio_pct(value: Decimal) -> Decimal:
    return value.quantize(RATIO_PCT, rounding=ROUND_HALF_UP)


class ProjectionInputError(Exception):
    """The projection request is structurally invalid."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class EnterpriseProjectionInputs:
    """The full domain input set for one base+stress 3-year projection."""

    scenario_code: str
    scenario_paths: Sequence[MacroPathPoint]
    facts: Sequence[ForecastFact]
    params: ForecastParams
    plan: ForecastAssumptions
    horizon_years: int = HORIZON_DEFAULT
    paid_up_min: Decimal = _ZERO
    overrides: Mapping[str, Sequence[ShockMapping]] | None = None
    # Phase 4: per-stress-year credit-RWA uplift factor (≥ 1.0) from the
    # exposure-level bottom-up credit stress — rating migration + FX-loan
    # revaluation. Applied ONLY to the stress leg's credit RWA, so the stressed
    # capital ratios erode from RWA growth, not just from P&L losses (closing
    # the Phase-2 limitation). ``None`` (or a factor of 1.0) leaves the stress
    # leg byte-identical to Phase 2 — the base leg is never touched.
    credit_rwa_uplift: Mapping[int, Decimal] | None = None


PAID_UP_CATEGORIES: frozenset[str] = frozenset(
    {"paid_up_capital", "stated_capital", "ordinary_shares", "common_equity", "share_capital"}
)


@dataclass(frozen=True)
class Pnl:
    nii: Decimal
    fees: Decimal
    total_income: Decimal
    opex: Decimal
    credit_losses: Decimal
    pre_tax: Decimal
    tax: Decimal
    net_income: Decimal
    dividends: Decimal
    retained: Decimal


@dataclass(frozen=True)
class BalanceSheet:
    cash: Decimal
    short_term_investments: Decimal
    loans: Decimal
    other_assets: Decimal
    total_assets: Decimal
    demand_deposits: Decimal
    savings_deposits: Decimal
    time_deposits: Decimal
    other_deposits: Decimal
    borrowings: Decimal
    total_liabilities: Decimal
    capital: Decimal


@dataclass(frozen=True)
class MinimaCheck:
    car_pct: Decimal
    car_min_pct: Decimal
    car_ok: bool
    cet1_pct: Decimal
    cet1_min_pct: Decimal
    cet1_ok: bool
    tier1_pct: Decimal
    tier1_min_pct: Decimal
    tier1_ok: bool
    leverage_pct: Decimal
    leverage_min_pct: Decimal
    leverage_ok: bool
    paid_up: Decimal
    paid_up_min: Decimal
    paid_up_ok: bool

    @property
    def all_ok(self) -> bool:
        return (
            self.car_ok
            and self.cet1_ok
            and self.tier1_ok
            and self.leverage_ok
            and self.paid_up_ok
        )

    @property
    def binding(self) -> tuple[str, ...]:
        breaches: list[str] = []
        if not self.car_ok:
            breaches.append("car")
        if not self.cet1_ok:
            breaches.append("cet1")
        if not self.tier1_ok:
            breaches.append("tier1")
        if not self.leverage_ok:
            breaches.append("leverage")
        if not self.paid_up_ok:
            breaches.append("paid_up")
        return tuple(breaches)


@dataclass(frozen=True)
class ProjectedYear:
    year: int
    leg: str  # "current" | "base" | "stress"
    pnl: Pnl
    balance_sheet: BalanceSheet
    rwa: RwaResult
    ratios: CapitalRatiosResult
    paid_up: Decimal
    lcr_pct: Decimal
    nsfr_pct: Decimal
    minima: MinimaCheck
    # The per-year macro PD/LGD multipliers that drove the stress cost of risk
    # (both 1.0 on the base leg). Perfect-foresight evidence for the audit trail.
    pd_multiplier: Decimal
    lgd_multiplier: Decimal


@dataclass(frozen=True)
class EnterpriseProjection:
    scenario_code: str
    horizon_years: int
    current: ProjectedYear
    base: tuple[ProjectedYear, ...]
    stress: tuple[ProjectedYear, ...]
    stress_stays_above_all_minima: bool
    first_breach_year: int | None
    binding_minima: tuple[str, ...]


def _paths_at_year(
    paths: Sequence[MacroPathPoint], year_index: int
) -> list[MacroPathPoint]:
    return [point for point in paths if point.year_index == year_index]


def _year_multipliers(
    paths: Sequence[MacroPathPoint],
    year_index: int,
    overrides: Mapping[str, Sequence[ShockMapping]] | None,
) -> tuple[Decimal, Decimal]:
    """Perfect-foresight PD/LGD multipliers for one projected year.

    ``translate`` on the single year's macro points yields the multipliers that
    year's macro implies — so each projected year knows its own macro (¶48–49).
    """
    year_paths = _paths_at_year(paths, year_index)
    shocks = translate(year_paths, "capital", overrides) if year_paths else {}
    return shocks.get("ecl_pd_multiplier", _ONE), shocks.get("ecl_lgd_multiplier", _ONE)


def _delta(paths: Sequence[MacroPathPoint], variable: str, year_index: int) -> Decimal:
    for point in paths:
        if point.variable == variable and point.year_index == year_index:
            return point.stress_value - point.base_value
    return _ZERO


def _frac_delta_fx(paths: Sequence[MacroPathPoint], year_index: int) -> Decimal:
    """Stress-vs-base fractional cedi depreciation at one year (0 if base==stress)."""
    for point in paths:
        if point.variable == "fx_usd_ghs" and point.year_index == year_index:
            if point.base_value == _ZERO:
                return _ZERO
            return (point.stress_value - point.base_value) / point.base_value
    return _ZERO


def _stress_assumptions_for_year(
    inputs: EnterpriseProjectionInputs, year_index: int
) -> tuple[ForecastAssumptions, Decimal, Decimal]:
    """Plan perturbed by the stress-vs-base macro deltas at ``year_index``.

    Returns (assumptions, pd_multiplier, lgd_multiplier). Under a base scenario
    every delta is zero, so the result equals the plan and the multipliers are
    1.0 — the stress leg collapses onto the base leg.
    """
    plan = inputs.plan
    paths = inputs.scenario_paths
    gdp_delta = _delta(paths, "gdp_growth", year_index)
    rate_delta = _delta(paths, "interest_rate", year_index)
    infl_delta = _delta(paths, "inflation", year_index)
    pd_mult, lgd_mult = _year_multipliers(paths, year_index, inputs.overrides)

    loan_growth = max(plan.loan_growth_pct + LOAN_GROWTH_PER_GDP * gdp_delta, _MIN_GROWTH_PCT)
    deposit_growth = max(
        plan.deposit_growth_pct + DEPOSIT_GROWTH_PER_GDP * gdp_delta, _MIN_GROWTH_PCT
    )
    nim = plan.nim_pct + NIM_PER_RATE * rate_delta
    cost_to_income = plan.cost_to_income_pct + CTI_PER_INFLATION * infl_delta
    # ECL under stress drives the cost of risk multiplicatively (¶48).
    credit_loss_rate = plan.credit_loss_rate_pct * pd_mult * lgd_mult
    assumptions = replace(
        plan,
        loan_growth_pct=loan_growth,
        deposit_growth_pct=deposit_growth,
        nim_pct=nim,
        cost_to_income_pct=cost_to_income,
        credit_loss_rate_pct=credit_loss_rate,
    )
    return assumptions, pd_mult, lgd_mult


def project_enterprise(inputs: EnterpriseProjectionInputs) -> EnterpriseProjection:
    """Run the base and stress 3-year projections and assess the minima (¶77)."""
    if inputs.horizon_years < MIN_HORIZON:
        raise ProjectionInputError(
            "horizon_too_short",
            f"The stress projection horizon must be at least {MIN_HORIZON} years "
            f"(got {inputs.horizon_years}).",
        )

    # Year 0 (as-of) is identical for both legs — compute it once.
    zero_state, zero_meta = _parse_facts(inputs.facts)
    current = _snapshot_year(inputs, zero_state, zero_meta, 0, "current", _ONE, _ONE)

    base_years = _run_leg(inputs, "base")
    stress_years = _run_leg(inputs, "stress")

    first_breach = next(
        (year.year for year in stress_years if not year.minima.all_ok), None
    )
    binding: set[str] = set()
    for year in stress_years:
        binding.update(year.minima.binding)
    return EnterpriseProjection(
        scenario_code=inputs.scenario_code,
        horizon_years=inputs.horizon_years,
        current=current,
        base=tuple(base_years),
        stress=tuple(stress_years),
        stress_stays_above_all_minima=first_breach is None,
        first_breach_year=first_breach,
        binding_minima=tuple(sorted(binding)),
    )


def _run_leg(inputs: EnterpriseProjectionInputs, leg: str) -> list[ProjectedYear]:
    state, meta = _parse_facts(inputs.facts)
    earning_prev = state.loans_total() + state.securities_total()
    assets_prev = state.assets_total()
    equity_prev = state.equity
    rows: list[ProjectedYear] = []
    for year in range(1, inputs.horizon_years + 1):
        if leg == "base":
            assumptions = inputs.plan
            pd_mult = lgd_mult = _ONE
            fx_pct = inputs.plan.fx_depreciation_pct if year == 1 else _ZERO
            mtm_pct = _ZERO
            credit_rwa_factor = _ONE
        else:
            assumptions, pd_mult, lgd_mult = _stress_assumptions_for_year(inputs, year)
            fx_pct = _stress_fx_year1(inputs) if year == 1 else _ZERO
            mtm_pct = _stress_mtm_year1(inputs) if year == 1 else _ZERO
            credit_rwa_factor = _credit_rwa_factor(inputs, year)
        earning_prev, assets_prev, equity_prev, row = _project_one_year(
            inputs,
            state,
            meta,
            year,
            leg,
            assumptions,
            fx_pct,
            mtm_pct,
            pd_mult,
            lgd_mult,
            earning_prev,
            assets_prev,
            equity_prev,
            credit_rwa_factor,
        )
        rows.append(row)
    return rows


def _stress_fx_year1(inputs: EnterpriseProjectionInputs) -> Decimal:
    """One-off cedi-depreciation revaluation for the stress leg (plan + stress).

    The stress delta is the peak stress-vs-base fractional depreciation across
    the horizon, added to the plan's own FX assumption. Zero under a base
    scenario, so the stress leg's year-1 FX equals the base leg's.
    """
    peak = _ZERO
    for year in range(1, inputs.horizon_years + 1):
        candidate = _frac_delta_fx(inputs.scenario_paths, year)
        if abs(candidate) > abs(peak):
            peak = candidate
    return inputs.plan.fx_depreciation_pct + max(peak, _ZERO) * _HUNDRED


def _credit_rwa_factor(inputs: EnterpriseProjectionInputs, year: int) -> Decimal:
    """The stress leg's credit-RWA uplift for one year (1.0 when unmodelled)."""
    if inputs.credit_rwa_uplift is None:
        return _ONE
    return inputs.credit_rwa_uplift.get(year, _ONE)


def _apply_credit_rwa_uplift(rwa: RwaResult, factor: Decimal) -> RwaResult:
    """Scale a year's credit RWA (and total, and the credit line items) by ``factor``.

    The uplift is the bottom-up rating-migration + FX-loan revaluation overlay.
    Market and operational RWA are untouched (their stresses live elsewhere), so
    the higher total RWA erodes the capital ratios computed downstream.
    """
    if factor == _ONE:
        return rwa
    scaled_credit = money(rwa.credit_rwa * factor)
    line_items = tuple(
        replace(item, weighted_amount=money(item.weighted_amount * factor))
        if item.section == "credit_rwa"
        else item
        for item in rwa.line_items
    )
    return replace(
        rwa,
        credit_rwa=scaled_credit,
        total_rwa=money(scaled_credit + rwa.market_rwa + rwa.operational_rwa),
        line_items=line_items,
    )


def _stress_mtm_year1(inputs: EnterpriseProjectionInputs) -> Decimal:
    """One-off marketable-securities mark-down for the stress leg (rate shock).

    Reuses the macro-derived HQLA haircut (duration × Δyield) the liquidity
    translation emits, applied once to the securities book. Zero under base.
    """
    shocks = translate(inputs.scenario_paths, "liquidity", inputs.overrides)
    return shocks.get("hqla_securities_haircut_pct", _ZERO)


def _project_one_year(  # noqa: PLR0913, PLR0915 - the year step names its full scope
    inputs: EnterpriseProjectionInputs,
    state: _State,
    meta: _Meta,
    year: int,
    leg: str,
    assumptions: ForecastAssumptions,
    fx_pct: Decimal,
    mtm_pct: Decimal,
    pd_mult: Decimal,
    lgd_mult: Decimal,
    earning_prev: Decimal,
    assets_prev: Decimal,
    equity_prev: Decimal,
    credit_rwa_factor: Decimal = _ONE,
) -> tuple[Decimal, Decimal, Decimal, ProjectedYear]:
    loan_factor = _ONE + assumptions.loan_growth_pct / _HUNDRED
    deposit_factor = _ONE + assumptions.deposit_growth_pct / _HUNDRED
    securities_factor = (
        _ONE + (assumptions.deposit_growth_pct + assumptions.securities_shift_pp) / _HUNDRED
    )
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

    # One-off year-1 shocks (mark-down of marketable securities, FX revaluation).
    if year == 1:
        if mtm_pct != _ZERO:
            haircut_factor = (_HUNDRED - mtm_pct) / _HUNDRED
            _scale_in_place(state.securities, haircut_factor)
            for category in state.securities_group:
                if not meta.securities_group_cash_derived.get(category, False):
                    state.securities_group[category] = money(
                        state.securities_group[category] * haircut_factor
                    )
        if fx_pct != _ZERO:
            _scale_in_place(state.market, _ONE + fx_pct / _HUNDRED)

    earning_assets = state.loans_total() + state.securities_total()
    nii = money(assumptions.nim_pct / _HUNDRED * (earning_prev + earning_assets) / _TWO)
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
    state.gi_history.append((state.gi_history[-1][0] + 1, total_income))

    pnl = Pnl(
        nii=nii,
        fees=fees,
        total_income=total_income,
        opex=opex,
        credit_losses=credit_losses,
        pre_tax=pre_tax,
        tax=tax,
        net_income=net_income,
        dividends=dividends,
        retained=retained,
    )
    row = _snapshot_year(
        inputs, state, meta, year, leg, pd_mult, lgd_mult, pnl=pnl,
        credit_rwa_factor=credit_rwa_factor,
    )
    return earning_assets, state.assets_total(), state.equity, row


def _snapshot_year(  # noqa: PLR0913 - the snapshot names its full scope
    inputs: EnterpriseProjectionInputs,
    state: _State,
    meta: _Meta,
    year: int,
    leg: str,
    pd_mult: Decimal,
    lgd_mult: Decimal,
    *,
    pnl: Pnl | None = None,
    credit_rwa_factor: Decimal = _ONE,
) -> ProjectedYear:
    rows = _state_facts(state, meta)
    capital_facts = _to_capital_facts(rows)
    liquidity_facts = _to_liquidity_facts(rows)
    rwa = _apply_credit_rwa_uplift(
        compute_rwa(capital_facts, inputs.params.capital), credit_rwa_factor
    )
    ratios = compute_capital_ratios(capital_facts, rwa, inputs.params.capital)
    lcr = compute_lcr(liquidity_facts, inputs.params.liquidity)
    nsfr = compute_nsfr(liquidity_facts, inputs.params.liquidity)
    paid_up = _paid_up_capital(ratios)
    minima = _minima(ratios, paid_up, inputs.params.capital, inputs.paid_up_min)
    return ProjectedYear(
        year=year,
        leg=leg,
        pnl=pnl if pnl is not None else _zero_pnl(),
        balance_sheet=_balance_sheet(state),
        rwa=rwa,
        ratios=ratios,
        paid_up=paid_up,
        lcr_pct=lcr.lcr_pct,
        nsfr_pct=nsfr.nsfr_pct,
        minima=minima,
        pd_multiplier=pd_mult,
        lgd_multiplier=lgd_mult,
    )


def _zero_pnl() -> Pnl:
    return Pnl(_ZERO, _ZERO, _ZERO, _ZERO, _ZERO, _ZERO, _ZERO, _ZERO, _ZERO, _ZERO)


def _paid_up_capital(ratios: CapitalRatiosResult) -> Decimal:
    """Sum the CET1 paid-up / stated-capital components (¶77 paid-up floor)."""
    total = _ZERO
    for item in ratios.line_items:
        if item.section != "capital_component":
            continue
        tier, _, category = item.line_code.partition(":")
        if tier == TIER_CET1.lower() and category in PAID_UP_CATEGORIES:
            total += item.weighted_amount
    return money(total)


def _minima(
    ratios: CapitalRatiosResult, paid_up: Decimal, params: CapitalParams, paid_up_min: Decimal
) -> MinimaCheck:
    return MinimaCheck(
        car_pct=ratios.car_pct,
        car_min_pct=params.car_min_pct,
        car_ok=ratios.car_pct >= params.car_min_pct,
        cet1_pct=ratios.cet1_ratio_pct,
        cet1_min_pct=params.cet1_min_pct,
        cet1_ok=ratios.cet1_ratio_pct >= params.cet1_min_pct,
        tier1_pct=ratios.tier1_ratio_pct,
        tier1_min_pct=params.tier1_min_pct,
        tier1_ok=ratios.tier1_ratio_pct >= params.tier1_min_pct,
        leverage_pct=ratios.leverage_ratio_pct,
        leverage_min_pct=params.leverage_min_pct,
        leverage_ok=ratios.leverage_ratio_pct >= params.leverage_min_pct,
        paid_up=paid_up,
        paid_up_min=paid_up_min,
        paid_up_ok=paid_up >= paid_up_min,
    )


_DEMAND_TOKENS = ("demand", "current", "call", "checking")
_SAVINGS_TOKENS = ("saving",)
_TIME_TOKENS = ("time", "term", "fixed")


def _balance_sheet(state: _State) -> BalanceSheet:
    demand = savings = time = other = _ZERO
    for category, amount in state.deposits.items():
        lowered = category.lower()
        if any(token in lowered for token in _DEMAND_TOKENS):
            demand += amount
        elif any(token in lowered for token in _SAVINGS_TOKENS):
            savings += amount
        elif any(token in lowered for token in _TIME_TOKENS):
            time += amount
        else:
            other += amount
    borrowings = state.borrowings + sum(state.constant_liabilities.values(), _ZERO)
    cash = state.cash_total()
    short_term_investments = state.securities_total()
    loans = state.loans_total()
    other_assets = sum(state.constant_assets.values(), _ZERO)
    total_assets = state.assets_total()
    capital = state.equity + sum(state.constant_equity.values(), _ZERO)
    total_liabilities = money(demand + savings + time + other + borrowings)
    return BalanceSheet(
        cash=money(cash),
        short_term_investments=money(short_term_investments),
        loans=money(loans),
        other_assets=money(other_assets),
        total_assets=money(total_assets),
        demand_deposits=money(demand),
        savings_deposits=money(savings),
        time_deposits=money(time),
        other_deposits=money(other),
        borrowings=money(borrowings),
        total_liabilities=total_liabilities,
        capital=money(capital),
    )


# --- Capital-component extraction for Appendix II Table 2 --------------------


def component_breakdown(ratios: CapitalRatiosResult) -> dict[str, dict[str, Decimal]]:
    """CET1/AT1/T2 component amounts (recognized/weighted) keyed by category.

    Reads the capital engine's ``capital_component`` line items — the
    authoritative tier build (deductions negative, GP already Tier-2-capped) —
    so the Appendix II Table 2 builder never re-derives the tiering.
    """
    tiers: dict[str, dict[str, Decimal]] = {
        TIER_CET1: {},
        TIER_AT1: {},
        TIER_T2: {},
    }
    tier_by_prefix = {
        TIER_CET1.lower(): TIER_CET1,
        TIER_AT1.lower(): TIER_AT1,
        TIER_T2.lower(): TIER_T2,
    }
    for item in ratios.line_items:
        if item.section != "capital_component":
            continue
        prefix, _, category = item.line_code.partition(":")
        tier = tier_by_prefix.get(prefix)
        if tier is None:
            continue
        tiers[tier][category] = tiers[tier].get(category, _ZERO) + item.weighted_amount
    return tiers


def general_provisions_amount(ratios: CapitalRatiosResult) -> Decimal:
    """The Tier-2 recognized general-provisions (credit risk reserve) amount."""
    for item in ratios.line_items:
        if item.section != "capital_component":
            continue
        prefix, _, category = item.line_code.partition(":")
        if prefix == TIER_T2.lower() and category == GENERAL_PROVISIONS_CATEGORY:
            return item.weighted_amount
    return _ZERO
