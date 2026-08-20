"""Enterprise-wide stress orchestrator (docs/stress.md §3.3, Phase 2 item 1).

The load-bearing gap the directive names: replace the per-module parameter
overrides with ONE governed macro scenario that fans out coherently into every
engine and rolls up into a single enterprise outcome (¶40, ¶50), coupling
solvency and liquidity (¶59(f)). This module is PURE — Decimal-only,
deterministic, free of any database or tenant concern. The service layer
(``app/services/enterprise_stress.py``) assembles the domain inputs from the
canonical book and persists the outcome as an immutable ``RegulatoryRun``.

One scenario drives four engines from the SAME macro paths:

- **Capital (solvency).** ``translate("capital")`` emits only the ECL PD/LGD
  conditioning multipliers (¶48) — the cleanly macro-derivable credit-risk
  parameters. The capital engine's four ABSOLUTE quarterly path keys
  (``quarterly_rwa_growth_pct``/``quarterly_income_m``/``quarterly_credit_loss_m``/
  ``fx_rwa_multiplier``) are GHS amounts tied to the balance sheet, so the
  translation layer deliberately does NOT emit them. Composing them from the
  macro scenario + the projected book is this orchestrator's job
  (``compose_capital_shocks``). Both a BASELINE four-quarter path (neutral
  overlays) and a STRESSED path are run, so the enterprise delta is the
  stressed-minus-baseline erosion and a base scenario produces a zero delta.
- **Liquidity.** ``translate("liquidity")`` feeds ``apply_liquidity_stress``
  (inflow multiplier + HQLA haircut on the LCR) and the currency-gap /
  behavioural-ladder layer (FX depreciation + NMD run-off schedule). Baseline
  vs stressed LCR/NSFR are both reported.
- **IRR.** ``translate("irr")`` → a parallel curve shift; ΔEVE and ΔNII are
  re-priced on the banking book.
- **FX.** ``translate("fx")`` → a cedi-depreciation shock; the net-open-position
  is revalued vs Tier 1.

The credit-loss composition uses the pure IFRS 9 engine in **Perfect Foresight /
Single Scenario** form (¶48–49, AppI¶5–6): one stress scenario at 100% weight,
its PD/LGD conditioned by the macro multipliers, contrasted with the baseline.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

from app.domain.capital.ecl import (
    BASE_SCENARIO,
    EclAssumption,
    EclExposure,
    EclScenario,
    compute_ecl,
)
from app.domain.capital.engine import (
    SHOCK_FX_RWA_MULTIPLIER,
    SHOCK_QUARTERLY_CREDIT_LOSS_M,
    SHOCK_QUARTERLY_INCOME_M,
    SHOCK_QUARTERLY_RWA_GROWTH_PCT,
    CapitalFact,
    CapitalParams,
    CapitalStressResult,
    run_capital_stress,
)
from app.domain.fx.engine import (
    FxPosition,
    NopResult,
    compute_nop,
    run_fx_scenarios,
)
from app.domain.irr.engine import (
    GapResult,
    IrrPosition,
    compute_ear,
    compute_eve,
    compute_gap,
    scenario_shifts,
)
from app.domain.liquidity.engine import (
    LcrResult,
    LiquidityFact,
    LiquidityParams,
    NsfrResult,
    apply_liquidity_stress,
    compute_currency_gaps,
    compute_lcr,
    compute_nsfr,
)
from app.domain.stress.concentration import (
    ConcentrationInputs,
    ConcentrationResult,
    compute_concentration,
)
from app.domain.stress.contingent_leverage import (
    ContingentLeverageInputs,
    ContingentLeverageResult,
    compute_contingent_leverage,
)
from app.domain.stress.credit_bottom_up import (
    BottomUpCreditInputs,
    BottomUpCreditResult,
    result_at_peak,
)
from app.domain.stress.operational import (
    OperationalConfig,
    OperationalInputs,
    OperationalResult,
    compute_operational,
)
from app.domain.stress.translation import (
    MacroPathPoint,
    ShockMapping,
    frac_change,
    signed_peak_delta,
    translate,
)

MONEY = Decimal("0.0001")
RATIO_PCT = Decimal("0.000001")
MILLION = Decimal("1000000")
_HUNDRED = Decimal("100")
_ZERO = Decimal("0")
_ONE = Decimal("1")

ENGINE_VERSION = "enterprise-stress-v1.0.0"

# --- Capital-path composition coefficients -----------------------------------
# Documented, defensible linear elasticities that turn the macro scenario into
# the capital engine's four absolute quarterly path keys (¶45 justify overlays).
# Every coefficient is neutral under a base scenario (all macro deltas zero), so
# ``compose_capital_shocks`` returns baseline == stressed for a base case.

#: Quarterly credit-RWA growth per unit of fractional cedi depreciation
#: (FX-denominated exposures inflate in GHS terms). 5 ⇒ a 20% depreciation adds
#: 1.0% of quarterly credit-RWA growth.
RWA_GROWTH_PER_FX_FRAC = Decimal("5")
#: Quarterly credit-RWA growth per unit of PD-multiplier uplift above 1.0
#: (rating migration lifts risk weights). 2 ⇒ a doubling of PD (mult 2.0) adds
#: 2.0% of quarterly credit-RWA growth.
RWA_GROWTH_PER_PD_UPLIFT = Decimal("2")
#: Pre-provision income compression per unit of the interest-rate rise
#: (liabilities reprice faster than assets at the short end). 3.0 ⇒ a +5pp rate
#: rise compresses income 15%.
INCOME_COMPRESSION_PER_RATE = Decimal("3.0")
#: Pre-provision income compression per unit of GDP contraction (lower business
#: volumes and fees). 2.0 ⇒ a 5pp GDP contraction compresses income 10%.
INCOME_COMPRESSION_PER_GDP = Decimal("2.0")
#: Market (FX) RWA scales one-for-one with the cedi value of the open FX
#: position under depreciation (mirrors the FX engine's revaluation factor).
FX_RWA_MULT_PER_FX_FRAC = Decimal("1")

_QUARTERS_PER_YEAR = Decimal("4")


def money(value: Decimal) -> Decimal:
    return value.quantize(MONEY, rounding=ROUND_HALF_UP)


def ratio_pct(value: Decimal) -> Decimal:
    return value.quantize(RATIO_PCT, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class CapitalShockComposition:
    """The four absolute capital-path keys plus their derivation provenance.

    ``stressed`` and ``baseline`` are both full four-key shock maps the capital
    engine accepts; under a base scenario they are equal.
    """

    stressed: dict[str, Decimal]
    baseline: dict[str, Decimal]
    pd_multiplier: Decimal
    lgd_multiplier: Decimal
    ecl_base: Decimal
    ecl_stress: Decimal
    annual_incremental_credit_loss: Decimal
    annual_baseline_credit_loss: Decimal
    income_stress_factor: Decimal
    annual_baseline_after_tax_income: Decimal
    annual_stressed_after_tax_income: Decimal
    ecl_source: str  # "ecl_engine" | "allowance_proxy"
    rationale: dict[str, str] = field(default_factory=dict)


def _capital_multipliers(
    scenario_paths: Sequence[MacroPathPoint],
    overrides: Mapping[str, Sequence[ShockMapping]] | None,
) -> tuple[Decimal, Decimal]:
    shocks = translate(scenario_paths, "capital", overrides)
    pd_mult = shocks.get("ecl_pd_multiplier", _ONE)
    lgd_mult = shocks.get("ecl_lgd_multiplier", _ONE)
    return pd_mult, lgd_mult


def _stress_ecl(
    exposures: Sequence[EclExposure],
    assumptions: Sequence[EclAssumption],
    pd_mult: Decimal,
    lgd_mult: Decimal,
) -> tuple[Decimal, Decimal]:
    """(baseline_ecl, stressed_ecl) in Perfect-Foresight / Single-Scenario form.

    Baseline is the through-the-cycle ECL (100% weight, no conditioning);
    stressed ascribes 100% weight to the single macro scenario, its PD/LGD
    scaled by the macro multipliers (¶48–49). Deterministic — no probability
    blend across scenarios.
    """
    base = compute_ecl(exposures, assumptions, (BASE_SCENARIO,))
    stressed = compute_ecl(
        exposures,
        assumptions,
        (
            EclScenario(
                code="single_stress",
                weight_pct=_HUNDRED,
                pd_multiplier=pd_mult,
                lgd_multiplier=lgd_mult,
            ),
        ),
    )
    return base.total_ecl, stressed.total_ecl


def compose_capital_shocks(  # noqa: PLR0913 - the composition names its full input set
    *,
    scenario_paths: Sequence[MacroPathPoint],
    baseline_annual_preprovision_income: Decimal,
    baseline_annual_credit_loss: Decimal = _ZERO,
    baseline_credit_allowance: Decimal = _ZERO,
    tax_rate_pct: Decimal = Decimal("25"),
    ecl_exposures: Sequence[EclExposure] = (),
    ecl_assumptions: Sequence[EclAssumption] = (),
    overrides: Mapping[str, Sequence[ShockMapping]] | None = None,
) -> CapitalShockComposition:
    """Compose the capital engine's four absolute quarterly shock keys.

    The absolute keys the translation layer cannot emit are derived here from
    the macro scenario plus the projected book:

    - ``quarterly_credit_loss_m`` — from the ECL-under-stress path: the annual
      incremental impairment (stressed minus baseline ECL) spread over four
      quarters, on top of the through-the-cycle baseline loss. When no staged
      ``ecl_exposure`` data is supplied it falls back to
      ``allowance × (pd·lgd − 1)`` on the existing credit allowance.
    - ``quarterly_income_m`` — from the projected P&L: pre-provision operating
      profit, compressed by the macro income-stress factor, taxed, quartered.
    - ``quarterly_rwa_growth_pct`` — from the macro FX and PD drivers (FX-loan
      revaluation + rating migration).
    - ``fx_rwa_multiplier`` — from the macro FX driver (open-position revaluation).

    All four are neutral under a base scenario, so ``baseline`` == ``stressed``.
    """
    pd_mult, lgd_mult = _capital_multipliers(scenario_paths, overrides)
    fx_frac = frac_change(scenario_paths, "fx_usd_ghs")
    rate_delta = signed_peak_delta(scenario_paths, "interest_rate")
    gdp_delta = signed_peak_delta(scenario_paths, "gdp_growth")

    # Cedi depreciation and rating migration only ever ADD RWA growth; a benign
    # move floors the growth at zero (never a negative stress).
    fx_uplift = max(fx_frac, _ZERO)
    pd_uplift = max(pd_mult - _ONE, _ZERO)
    quarterly_rwa_growth_pct = money(
        RWA_GROWTH_PER_FX_FRAC * fx_uplift + RWA_GROWTH_PER_PD_UPLIFT * pd_uplift
    )
    fx_rwa_multiplier = money(_ONE + FX_RWA_MULT_PER_FX_FRAC * fx_uplift)

    # Income compression: a rate RISE and a GDP CONTRACTION both erode
    # pre-provision income; the factor floors at zero (income cannot go
    # negative through this channel).
    rate_up = max(rate_delta, _ZERO)
    gdp_down = max(-gdp_delta, _ZERO)
    income_stress_factor = max(
        _ONE
        - INCOME_COMPRESSION_PER_RATE * rate_up
        - INCOME_COMPRESSION_PER_GDP * gdp_down,
        _ZERO,
    )
    tax_factor = (_HUNDRED - tax_rate_pct) / _HUNDRED
    baseline_after_tax = money(max(baseline_annual_preprovision_income, _ZERO) * tax_factor)
    stressed_after_tax = money(
        max(baseline_annual_preprovision_income, _ZERO) * income_stress_factor * tax_factor
    )

    if ecl_exposures and ecl_assumptions:
        ecl_base, ecl_stress = _stress_ecl(ecl_exposures, ecl_assumptions, pd_mult, lgd_mult)
        ecl_source = "ecl_engine"
    else:
        ecl_base = money(baseline_credit_allowance)
        ecl_stress = money(baseline_credit_allowance * pd_mult * lgd_mult)
        ecl_source = "allowance_proxy"
    annual_incremental_loss = max(money(ecl_stress - ecl_base), _ZERO)

    def _keys(after_tax_income: Decimal, credit_loss: Decimal, rwa_growth: Decimal,
              fx_mult: Decimal) -> dict[str, Decimal]:
        return {
            SHOCK_QUARTERLY_RWA_GROWTH_PCT: rwa_growth,
            SHOCK_QUARTERLY_INCOME_M: money(after_tax_income / _QUARTERS_PER_YEAR / MILLION),
            SHOCK_QUARTERLY_CREDIT_LOSS_M: money(credit_loss / _QUARTERS_PER_YEAR / MILLION),
            SHOCK_FX_RWA_MULTIPLIER: fx_mult,
        }

    baseline_keys = _keys(
        baseline_after_tax, money(baseline_annual_credit_loss), _ZERO, _ONE
    )
    stressed_keys = _keys(
        stressed_after_tax,
        money(baseline_annual_credit_loss + annual_incremental_loss),
        quarterly_rwa_growth_pct,
        fx_rwa_multiplier,
    )
    return CapitalShockComposition(
        stressed=stressed_keys,
        baseline=baseline_keys,
        pd_multiplier=pd_mult,
        lgd_multiplier=lgd_mult,
        ecl_base=ecl_base,
        ecl_stress=ecl_stress,
        annual_incremental_credit_loss=annual_incremental_loss,
        annual_baseline_credit_loss=money(baseline_annual_credit_loss),
        income_stress_factor=income_stress_factor,
        annual_baseline_after_tax_income=baseline_after_tax,
        annual_stressed_after_tax_income=stressed_after_tax,
        ecl_source=ecl_source,
        rationale={
            "quarterly_rwa_growth_pct": (
                f"{RWA_GROWTH_PER_FX_FRAC}×fx_frac({fx_uplift}) + "
                f"{RWA_GROWTH_PER_PD_UPLIFT}×pd_uplift({pd_uplift})"
            ),
            "fx_rwa_multiplier": f"1 + {FX_RWA_MULT_PER_FX_FRAC}×fx_frac({fx_uplift})",
            "quarterly_income_m": (
                f"pre-provision income × factor({income_stress_factor}) × "
                f"after-tax({tax_factor})"
            ),
            "quarterly_credit_loss_m": (
                f"baseline {money(baseline_annual_credit_loss)} + incremental "
                f"{annual_incremental_loss} ({ecl_source})"
            ),
        },
    )


# --- IRR / FX inputs ---------------------------------------------------------


@dataclass(frozen=True)
class IrrStressInputs:
    positions: Sequence[IrrPosition]
    curve: Mapping[Decimal, Decimal]
    tier1: Decimal


@dataclass(frozen=True)
class FxStressInputs:
    positions: Sequence[FxPosition]
    tier1: Decimal
    single_limit_pct: Decimal
    aggregate_limit_pct: Decimal


@dataclass(frozen=True)
class LiquidityCouplingInputs:
    """Optional currency-ladder coupling (FX funding under cedi depreciation)."""

    ladders: dict[str, dict[str, list[Decimal]]]
    base_currency: str
    demand_by_currency: dict[str, Decimal] = field(default_factory=dict)


@dataclass(frozen=True)
class EnterpriseStressInputs:
    """The full domain input set for one enterprise stress run."""

    scenario_code: str
    scenario_paths: Sequence[MacroPathPoint]
    capital_facts: Sequence[CapitalFact]
    capital_params: CapitalParams
    liquidity_facts: Sequence[LiquidityFact]
    liquidity_params: LiquidityParams
    baseline_annual_preprovision_income: Decimal
    baseline_annual_credit_loss: Decimal = _ZERO
    baseline_credit_allowance: Decimal = _ZERO
    tax_rate_pct: Decimal = Decimal("25")
    ecl_exposures: Sequence[EclExposure] = ()
    ecl_assumptions: Sequence[EclAssumption] = ()
    irr: IrrStressInputs | None = None
    fx: FxStressInputs | None = None
    liquidity_coupling: LiquidityCouplingInputs | None = None
    overrides: Mapping[str, Sequence[ShockMapping]] | None = None
    # Phase 4 per-risk methods (all optional; absent ⇒ the section is None and
    # the enterprise outcome is unchanged from Phase 2). Each degrades to a
    # no-op / zero when the bank carries no such positions.
    bottom_up_credit: BottomUpCreditInputs | None = None
    concentration: ConcentrationInputs | None = None
    operational: OperationalConfig | None = None
    contingent_leverage: ContingentLeverageInputs | None = None


# --- Enterprise outcome ------------------------------------------------------


@dataclass(frozen=True)
class CapitalOutcome:
    composition: CapitalShockComposition
    baseline: CapitalStressResult
    stressed: CapitalStressResult
    baseline_car_end_pct: Decimal
    stressed_car_end_pct: Decimal
    car_erosion_pp: Decimal
    baseline_cet1_end_pct: Decimal
    stressed_cet1_end_pct: Decimal
    any_trigger_fired: bool


@dataclass(frozen=True)
class LiquidityOutcome:
    baseline_lcr: LcrResult
    stressed_lcr: LcrResult
    baseline_nsfr: NsfrResult
    stressed_nsfr: NsfrResult
    lcr_erosion_pp: Decimal
    nsfr_erosion_pp: Decimal
    fx_depreciation_pct: Decimal
    stressed_fx_funding_gap: Decimal | None


@dataclass(frozen=True)
class IrrOutcome:
    base_eve: Decimal
    stressed_eve: Decimal
    delta_eve: Decimal
    delta_eve_pct_tier1: Decimal
    parallel_bp: Decimal
    delta_nii: Decimal


@dataclass(frozen=True)
class FxOutcome:
    base_nop_pct_tier1: Decimal
    stressed_nop_pct_tier1: Decimal
    shock_pct: Decimal
    stressed_within_aggregate_limit: bool


@dataclass(frozen=True)
class SolvencyLiquidityCoupling:
    """The directive's solvency–liquidity interlinkage (¶59(f))."""

    stressed_car_end_pct: Decimal
    car_min_pct: Decimal
    car_breached: bool
    stressed_lcr_pct: Decimal
    lcr_min_pct: Decimal
    lcr_breached: bool
    both_breached: bool
    narrative: str


@dataclass(frozen=True)
class EnterpriseStressOutcome:
    scenario_code: str
    engine_version: str
    capital: CapitalOutcome
    liquidity: LiquidityOutcome
    coupling: SolvencyLiquidityCoupling
    irr: IrrOutcome | None
    fx: FxOutcome | None
    # Phase 4 per-risk methods (None when not supplied / no positions).
    bottom_up_credit: BottomUpCreditResult | None = None
    concentration: ConcentrationResult | None = None
    operational: OperationalResult | None = None
    contingent_leverage: ContingentLeverageResult | None = None

    def serialize(self) -> dict[str, object]:
        """A JSON-safe dict (all Decimals stringified) for ``RegulatoryRun.metrics``."""
        return _serialize_outcome(self)


def run_enterprise_stress(inputs: EnterpriseStressInputs) -> EnterpriseStressOutcome:
    """Drive every engine from one macro scenario into a single enterprise outcome."""
    composition = compose_capital_shocks(
        scenario_paths=inputs.scenario_paths,
        baseline_annual_preprovision_income=inputs.baseline_annual_preprovision_income,
        baseline_annual_credit_loss=inputs.baseline_annual_credit_loss,
        baseline_credit_allowance=inputs.baseline_credit_allowance,
        tax_rate_pct=inputs.tax_rate_pct,
        ecl_exposures=inputs.ecl_exposures,
        ecl_assumptions=inputs.ecl_assumptions,
        overrides=inputs.overrides,
    )
    ecl_base_override = composition.ecl_base if composition.ecl_source == "ecl_engine" else None
    ecl_stress_override = (
        composition.ecl_stress if composition.ecl_source == "ecl_engine" else None
    )
    baseline_capital = run_capital_stress(
        f"{inputs.scenario_code}:baseline",
        inputs.capital_facts,
        inputs.capital_params,
        composition.baseline,
        general_provisions_override=ecl_base_override,
    )
    stressed_capital = run_capital_stress(
        inputs.scenario_code,
        inputs.capital_facts,
        inputs.capital_params,
        composition.stressed,
        general_provisions_override=ecl_stress_override,
    )
    capital = CapitalOutcome(
        composition=composition,
        baseline=baseline_capital,
        stressed=stressed_capital,
        baseline_car_end_pct=baseline_capital.path[-1].car,
        stressed_car_end_pct=stressed_capital.path[-1].car,
        car_erosion_pp=ratio_pct(
            stressed_capital.path[-1].car - baseline_capital.path[-1].car
        ),
        baseline_cet1_end_pct=baseline_capital.path[-1].cet1_ratio,
        stressed_cet1_end_pct=stressed_capital.path[-1].cet1_ratio,
        any_trigger_fired=any(trigger.fired for trigger in stressed_capital.triggers),
    )

    liquidity = _run_liquidity(inputs)
    irr = _run_irr(inputs) if inputs.irr is not None else None
    fx = _run_fx(inputs) if inputs.fx is not None else None
    coupling = _couple(
        capital,
        liquidity,
        inputs.capital_params.car_min_pct,
        inputs.liquidity_params.lcr_min_pct,
    )

    bottom_up_credit = (
        result_at_peak(
            inputs.bottom_up_credit.exposures,
            inputs.scenario_paths,
            overrides=inputs.overrides,
            params=inputs.bottom_up_credit.params,
        )
        if inputs.bottom_up_credit is not None
        else None
    )
    concentration = (
        compute_concentration(inputs.concentration)
        if inputs.concentration is not None
        else None
    )
    operational = _run_operational(inputs, capital, liquidity)
    contingent_leverage = (
        compute_contingent_leverage(inputs.contingent_leverage)
        if inputs.contingent_leverage is not None
        else None
    )

    return EnterpriseStressOutcome(
        scenario_code=inputs.scenario_code,
        engine_version=ENGINE_VERSION,
        capital=capital,
        liquidity=liquidity,
        coupling=coupling,
        irr=irr,
        fx=fx,
        bottom_up_credit=bottom_up_credit,
        concentration=concentration,
        operational=operational,
        contingent_leverage=contingent_leverage,
    )


def _run_operational(
    inputs: EnterpriseStressInputs,
    capital: CapitalOutcome,
    liquidity: LiquidityOutcome,
) -> OperationalResult | None:
    """Simulate the seven operational scenarios, coupled to capital + liquidity.

    The op-loss is charged against the as-of capital position and the outflows
    against the current HQLA buffer, so the operational stress is expressed as a
    CAR impact and a liquidity drain (¶13 capital + liquidity impact).
    """
    if inputs.operational is None:
        return None
    return compute_operational(
        OperationalInputs(
            annual_gross_income=inputs.operational.annual_gross_income,
            liquidity_base=liquidity.baseline_lcr.hqla_total,
            total_capital=capital.baseline.ratios.total_capital,
            total_rwa=capital.baseline.rwa.total_rwa,
            severities=inputs.operational.severities,
        )
    )


def _run_liquidity(inputs: EnterpriseStressInputs) -> LiquidityOutcome:
    shocks = translate(inputs.scenario_paths, "liquidity", inputs.overrides)
    base_lcr = compute_lcr(inputs.liquidity_facts, inputs.liquidity_params)
    base_nsfr = compute_nsfr(inputs.liquidity_facts, inputs.liquidity_params)
    stressed_facts, stressed_params = apply_liquidity_stress(
        inputs.scenario_code, inputs.liquidity_facts, inputs.liquidity_params, shocks
    )
    stressed_lcr = compute_lcr(stressed_facts, stressed_params)
    stressed_nsfr = compute_nsfr(stressed_facts, stressed_params)
    fx_depreciation_pct = shocks.get("fx_depreciation_pct", _ZERO)
    stressed_fx_gap: Decimal | None = None
    if inputs.liquidity_coupling is not None:
        gaps = compute_currency_gaps(
            inputs.liquidity_coupling.ladders,
            inputs.liquidity_coupling.base_currency,
            fx_depreciation_pct,
        )
        stressed_fx_gap = gaps.stressed_fx_funding_gap
    return LiquidityOutcome(
        baseline_lcr=base_lcr,
        stressed_lcr=stressed_lcr,
        baseline_nsfr=base_nsfr,
        stressed_nsfr=stressed_nsfr,
        lcr_erosion_pp=ratio_pct(stressed_lcr.lcr_pct - base_lcr.lcr_pct),
        nsfr_erosion_pp=ratio_pct(stressed_nsfr.nsfr_pct - base_nsfr.nsfr_pct),
        fx_depreciation_pct=fx_depreciation_pct,
        stressed_fx_funding_gap=stressed_fx_gap,
    )


def _run_irr(inputs: EnterpriseStressInputs) -> IrrOutcome:
    assert inputs.irr is not None
    irr = inputs.irr
    shocks = translate(inputs.scenario_paths, "irr", inputs.overrides)
    parallel_bp = shocks.get("parallel_bp", _ZERO)
    gap: GapResult = compute_gap(irr.positions)
    base_eve = compute_eve(irr.positions, irr.curve, {})
    shifts = (
        scenario_shifts(inputs.scenario_code, {"parallel_bp": parallel_bp}, irr.curve)
        if parallel_bp != _ZERO
        else {}
    )
    stressed_eve = compute_eve(irr.positions, irr.curve, shifts)
    delta_eve = money(stressed_eve - base_eve)
    pct = ratio_pct(delta_eve / irr.tier1 * _HUNDRED) if irr.tier1 > _ZERO else _ZERO
    delta_nii = compute_ear(gap, parallel_bp)
    return IrrOutcome(
        base_eve=base_eve,
        stressed_eve=stressed_eve,
        delta_eve=delta_eve,
        delta_eve_pct_tier1=pct,
        parallel_bp=parallel_bp,
        delta_nii=delta_nii,
    )


def _run_fx(inputs: EnterpriseStressInputs) -> FxOutcome:
    assert inputs.fx is not None
    fx = inputs.fx
    shocks = translate(inputs.scenario_paths, "fx", inputs.overrides)
    shock_pct = shocks.get("ghs_usd_shock_pct", _ZERO)
    base_nop: NopResult = compute_nop(
        fx.positions, fx.tier1, fx.single_limit_pct, fx.aggregate_limit_pct
    )
    scenarios = run_fx_scenarios(
        fx.positions,
        fx.tier1,
        {inputs.scenario_code: shock_pct},
        fx.single_limit_pct,
        fx.aggregate_limit_pct,
    )
    stressed = scenarios[0]
    return FxOutcome(
        base_nop_pct_tier1=base_nop.nop_pct_tier1,
        stressed_nop_pct_tier1=stressed.nop_pct_tier1,
        shock_pct=shock_pct,
        stressed_within_aggregate_limit=stressed.within_aggregate_limit,
    )


def _couple(
    capital: CapitalOutcome,
    liquidity: LiquidityOutcome,
    car_min_pct: Decimal,
    lcr_min_pct: Decimal,
) -> SolvencyLiquidityCoupling:
    car = capital.stressed_car_end_pct
    lcr = liquidity.stressed_lcr.lcr_pct
    car_breached = car < car_min_pct
    lcr_breached = lcr < lcr_min_pct
    both = car_breached and lcr_breached
    narrative = (
        f"Under the stress the solvency path ends at a {car}% CAR "
        f"({'below' if car_breached else 'above'} the {car_min_pct}% minimum) while the "
        f"liquidity coverage ratio falls to {lcr}% "
        f"({'below' if lcr_breached else 'above'} its {lcr_min_pct}% floor)."
    )
    if both:
        narrative += (
            " Solvency and liquidity breach together — a second-round funding-cost"
            " feedback loop is the material risk."
        )
    return SolvencyLiquidityCoupling(
        stressed_car_end_pct=car,
        car_min_pct=car_min_pct,
        car_breached=car_breached,
        stressed_lcr_pct=lcr,
        lcr_min_pct=lcr_min_pct,
        lcr_breached=lcr_breached,
        both_breached=both,
        narrative=narrative,
    )


def _serialize_capital(capital: CapitalOutcome) -> dict[str, object]:
    comp = capital.composition
    return {
        "baseline_car_end_pct": str(capital.baseline_car_end_pct),
        "stressed_car_end_pct": str(capital.stressed_car_end_pct),
        "car_erosion_pp": str(capital.car_erosion_pp),
        "baseline_cet1_end_pct": str(capital.baseline_cet1_end_pct),
        "stressed_cet1_end_pct": str(capital.stressed_cet1_end_pct),
        "any_trigger_fired": capital.any_trigger_fired,
        "composed_shocks": {key: str(value) for key, value in comp.stressed.items()},
        "baseline_shocks": {key: str(value) for key, value in comp.baseline.items()},
        "pd_multiplier": str(comp.pd_multiplier),
        "lgd_multiplier": str(comp.lgd_multiplier),
        "ecl_base": str(comp.ecl_base),
        "ecl_stress": str(comp.ecl_stress),
        "annual_incremental_credit_loss": str(comp.annual_incremental_credit_loss),
        "income_stress_factor": str(comp.income_stress_factor),
        "ecl_source": comp.ecl_source,
        "rationale": comp.rationale,
        "stressed_path_car_pct": [str(quarter.car) for quarter in capital.stressed.path],
        "triggers": [
            {
                "code": trigger.code,
                "threshold_pct": str(trigger.threshold_pct),
                "fired": trigger.fired,
                "first_quarter": trigger.first_quarter,
            }
            for trigger in capital.stressed.triggers
        ],
    }


def _serialize_liquidity(liquidity: LiquidityOutcome) -> dict[str, object]:
    return {
        "baseline_lcr_pct": str(liquidity.baseline_lcr.lcr_pct),
        "stressed_lcr_pct": str(liquidity.stressed_lcr.lcr_pct),
        "lcr_erosion_pp": str(liquidity.lcr_erosion_pp),
        "baseline_nsfr_pct": str(liquidity.baseline_nsfr.nsfr_pct),
        "stressed_nsfr_pct": str(liquidity.stressed_nsfr.nsfr_pct),
        "nsfr_erosion_pp": str(liquidity.nsfr_erosion_pp),
        "fx_depreciation_pct": str(liquidity.fx_depreciation_pct),
        "stressed_fx_funding_gap": (
            None
            if liquidity.stressed_fx_funding_gap is None
            else str(liquidity.stressed_fx_funding_gap)
        ),
    }


def _serialize_outcome(outcome: EnterpriseStressOutcome) -> dict[str, object]:
    result: dict[str, object] = {
        "scenario_code": outcome.scenario_code,
        "engine_version": outcome.engine_version,
        "capital": _serialize_capital(outcome.capital),
        "liquidity": _serialize_liquidity(outcome.liquidity),
        "coupling": {
            "stressed_car_end_pct": str(outcome.coupling.stressed_car_end_pct),
            "car_min_pct": str(outcome.coupling.car_min_pct),
            "car_breached": outcome.coupling.car_breached,
            "stressed_lcr_pct": str(outcome.coupling.stressed_lcr_pct),
            "lcr_min_pct": str(outcome.coupling.lcr_min_pct),
            "lcr_breached": outcome.coupling.lcr_breached,
            "both_breached": outcome.coupling.both_breached,
            "narrative": outcome.coupling.narrative,
        },
    }
    if outcome.irr is not None:
        result["irr"] = {
            "base_eve": str(outcome.irr.base_eve),
            "stressed_eve": str(outcome.irr.stressed_eve),
            "delta_eve": str(outcome.irr.delta_eve),
            "delta_eve_pct_tier1": str(outcome.irr.delta_eve_pct_tier1),
            "parallel_bp": str(outcome.irr.parallel_bp),
            "delta_nii": str(outcome.irr.delta_nii),
        }
    if outcome.fx is not None:
        result["fx"] = {
            "base_nop_pct_tier1": str(outcome.fx.base_nop_pct_tier1),
            "stressed_nop_pct_tier1": str(outcome.fx.stressed_nop_pct_tier1),
            "shock_pct": str(outcome.fx.shock_pct),
            "stressed_within_aggregate_limit": outcome.fx.stressed_within_aggregate_limit,
        }
    if outcome.bottom_up_credit is not None:
        result["bottom_up_credit"] = outcome.bottom_up_credit.serialize()
    if outcome.concentration is not None:
        result["concentration"] = outcome.concentration.serialize()
    if outcome.operational is not None:
        result["operational"] = outcome.operational.serialize()
    if outcome.contingent_leverage is not None:
        result["contingent_leverage"] = outcome.contingent_leverage.serialize()
    return result
