"""A broken macro scenario must FAIL, never neutralise (enterprise audit P0-9).

Before 2026-08-21 ``translate`` emitted a shock only when it deviated from
neutral and every consumer read ``shocks.get(key, NEUTRAL)``. Nothing checked
that ``scenario_paths`` was non-empty or that it carried the variables the
elasticity register names, so a scenario that failed to load — or whose
``variable`` strings were mis-keyed — produced ``{}``: every multiplier neutral,
``stress_stays_above_all_minima=True``, ``first_breach_year=None``, and a
complete, well-formed outcome showing zero impact. **A configuration failure was
indistinguishable from a resilient bank.**

This module pins the three fixes:

1. an empty or incomplete scenario raises ``NotComputable`` /
   ``MISSING_REQUIRED_INPUT`` in every module and at every entry point;
2. the peak of a direction-sensitive driver is taken over the ADVERSE moves, so
   a benign year cannot cancel a stressed one;
3. the post-management-action minima carry the institution's own regime.
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest

from app.domain.authority.outcomes import NotComputable, OutcomeState
from app.domain.stress.credit_bottom_up import (
    CreditExposure,
    compute_bottom_up_credit,
    result_at_peak,
    result_for_year,
)
from app.domain.stress.management_actions import (
    ActionTrigger,
    ManagementAction,
    ManagementActionError,
    ManagementActionPlan,
    apply_management_actions,
)
from app.domain.stress.orchestrator import compose_capital_shocks
from app.domain.stress.projection import EnterpriseProjectionInputs, project_enterprise
from app.domain.stress.translation import (
    ADVERSE_DOWN,
    ADVERSE_UP,
    TRANSLATION_MODULES,
    ElasticityTerm,
    MacroPathPoint,
    ShockMapping,
    frac_change,
    missing_variables,
    required_variables,
    signed_peak_delta,
    translate,
)
from tests.domain.stress_fixtures import (
    BASE_ASSUMPTIONS,
    bog_capital_params,
    bog_forecast_params,
    sample_bank_latest_facts,
    severe_paths,
)

M = Decimal("1000000")


def _p(variable: str, year: int, base: str, stress: str) -> MacroPathPoint:
    return MacroPathPoint(variable, year, Decimal(base), Decimal(stress))


# --- 1. Empty / incomplete scenarios fail closed -----------------------------


@pytest.mark.parametrize("module", TRANSLATION_MODULES)
def test_an_empty_scenario_refuses_in_every_module(module: str) -> None:
    with pytest.raises(NotComputable) as exc:
        translate((), module)
    assert exc.value.state is OutcomeState.MISSING_REQUIRED_INPUT
    assert exc.value.blocks_filing is True
    assert exc.value.code == f"missing_required_input:stress_shocks:{module}"


@pytest.mark.parametrize("module", TRANSLATION_MODULES)
def test_a_scenario_missing_one_driver_refuses(module: str) -> None:
    """Drop exactly one required variable from a complete scenario."""
    required = required_variables(module)
    dropped = required[0]
    paths = tuple(point for point in severe_paths() if point.variable != dropped)
    with pytest.raises(NotComputable) as exc:
        translate(paths, module)
    assert exc.value.details[0].items == (f"macro:{dropped}",)


def test_a_mis_keyed_scenario_refuses_rather_than_reporting_zero_impact() -> None:
    """The exact defect: every ``variable`` string is wrong, so nothing matches."""
    mis_keyed = tuple(
        MacroPathPoint(f"{point.variable}_typo", point.year_index, point.base_value,
                       point.stress_value)
        for point in severe_paths()
    )
    for module in TRANSLATION_MODULES:
        with pytest.raises(NotComputable):
            translate(mis_keyed, module)


def test_a_complete_scenario_still_translates() -> None:
    assert missing_variables(severe_paths(), "liquidity") == ()
    shocks = translate(severe_paths(), "liquidity")
    assert shocks  # the severe fixture really does stress liquidity


def test_a_variable_authored_flat_satisfies_the_contract_and_contributes_zero() -> None:
    """The documented escape hatch: an irrelevant driver is authored, not omitted."""
    paths = (
        _p("interest_rate", 1, "0.20", "0.25"),
        _p("inflation", 1, "0.15", "0.15"),  # flat
        _p("gdp_growth", 1, "0.05", "0.05"),  # flat
        _p("fx_usd_ghs", 1, "12.5", "12.5"),  # flat
    )
    shocks = translate(paths, "liquidity")
    # Only the rate-driven shocks fire; the flat drivers contribute exactly zero.
    assert shocks["nmd_runoff:h1"] == Decimal("10")  # 0.05 x 200 + 0.00 x 100
    assert shocks["hqla_securities_haircut_pct"] == Decimal("17.5")  # 0.05 x 350
    assert "fx_depreciation_pct" not in shocks
    assert "inflow_multiplier" not in shocks


def test_the_projection_refuses_a_year_the_scenario_does_not_cover() -> None:
    """Year 2 is absent, so the projection's year-2 stress leg has no macro.

    The pre-fix code neutralised that year (``translate(...) if year_paths else
    {}``) and minted an official three-year stress whose middle year was
    unstressed.
    """
    paths = tuple(point for point in severe_paths(3) if point.year_index != 2)
    with pytest.raises(NotComputable) as exc:
        project_enterprise(
            EnterpriseProjectionInputs(
                scenario_code="severe",
                scenario_paths=paths,
                facts=sample_bank_latest_facts(),
                params=bog_forecast_params(),
                plan=BASE_ASSUMPTIONS,
                horizon_years=3,
            )
        )
    assert exc.value.state is OutcomeState.MISSING_REQUIRED_INPUT


def test_the_bottom_up_credit_stress_refuses_an_uncovered_year() -> None:
    book = (
        CreditExposure("C", "corporates", Decimal("100") * M, Decimal("2"), Decimal("45"),
                       Decimal("100")),
    )
    paths = tuple(point for point in severe_paths(3) if point.year_index != 2)
    with pytest.raises(NotComputable):
        result_for_year(book, paths, 2)
    # ...and the covered years still work.
    assert result_for_year(book, paths, 1).pd_multiplier > Decimal("1")


# --- 2. The peak is taken in the ADVERSE direction ---------------------------


MIXED_FX = (
    # Cedi DEPRECIATES 30% in year 1 (adverse) then APPRECIATES 35% in year 3
    # (benign, and larger in magnitude).
    _p("fx_usd_ghs", 1, "10.0", "13.0"),
    _p("fx_usd_ghs", 2, "10.0", "10.0"),
    _p("fx_usd_ghs", 3, "10.0", "6.5"),
)
MIXED_COMPLETE = (
    *MIXED_FX,
    *(_p("interest_rate", y, "0.20", "0.20") for y in (1, 2, 3)),
    *(_p("inflation", y, "0.15", "0.15") for y in (1, 2, 3)),
    *(_p("gdp_growth", y, "0.05", "0.05") for y in (1, 2, 3)),
    *(_p("unemployment", y, "0.06", "0.06") for y in (1, 2, 3)),
    *(_p("gse_index", y, "5000", "5000") for y in (1, 2, 3)),
)


def test_the_undirected_peak_still_picks_the_largest_magnitude() -> None:
    # The pre-fix selection, retained for genuinely signed shocks: -0.35.
    assert frac_change(MIXED_FX, "fx_usd_ghs") == Decimal("-0.35")


def test_the_adverse_peak_ignores_the_benign_year() -> None:
    assert frac_change(MIXED_FX, "fx_usd_ghs", adverse=ADVERSE_UP) == Decimal("0.30")


def test_a_scenario_with_a_larger_benign_year_still_applies_fx_stress() -> None:
    """The defect: +30% depreciation then -35% appreciation applied NO FX stress.

    ``frac_change`` returned -0.35 and every consumer floored it at zero.
    """
    assert translate(MIXED_COMPLETE, "fx") == {"ghs_usd_shock_pct": Decimal("30.00")}
    assert translate(MIXED_COMPLETE, "liquidity")["fx_depreciation_pct"] == Decimal("30")

    composition = compose_capital_shocks(
        scenario_paths=MIXED_COMPLETE,
        baseline_annual_preprovision_income=Decimal("100") * M,
        # The allowance is now a required input when no staged ECL data is
        # supplied: without either, the stress carried NO incremental credit
        # loss at all (audit 2026-08-22 D-8).
        baseline_credit_allowance=Decimal("15") * M,
    )
    # FX RWA multiplier = 1 + 1 x 0.30; quarterly credit-RWA growth = 5 x 0.30.
    assert composition.stressed["fx_rwa_multiplier"] == Decimal("1.3000")
    assert composition.stressed["quarterly_rwa_growth_pct"] == Decimal("1.5000")


def test_the_bottom_up_peak_fx_fraction_ignores_appreciation() -> None:
    book = (
        CreditExposure("C", "corporates", Decimal("100") * M, Decimal("2"), Decimal("45"),
                       Decimal("100"), is_foreign_currency=True),
    )
    result = result_at_peak(book, MIXED_COMPLETE)
    assert result.fx_fraction == Decimal("0.30")
    # Sanity: the same book with a zero FX fraction carries no revaluation RWA.
    flat = compute_bottom_up_credit(
        book, pd_multiplier=Decimal("1"), lgd_multiplier=Decimal("1"), fx_fraction=Decimal("0")
    )
    assert result.fx_revaluation_rwa > flat.fx_revaluation_rwa


def test_a_signed_shock_keeps_both_directions() -> None:
    """A rate FALL is a real IRRBB stress and must survive the change."""
    paths = (_p("interest_rate", 1, "0.20", "0.17"),)
    assert translate(paths, "irr") == {"parallel_bp": Decimal("-300.0000")}
    assert signed_peak_delta(paths, "interest_rate") == Decimal("-0.03")
    assert signed_peak_delta(paths, "interest_rate", adverse=ADVERSE_UP) == Decimal("0")
    assert signed_peak_delta(paths, "interest_rate", adverse=ADVERSE_DOWN) == Decimal("-0.03")


def test_the_severe_fixture_is_unchanged_by_the_direction_rule() -> None:
    """Monotone scenarios select the same peak as before — no golden moves."""
    paths = severe_paths()
    for variable, adverse in (
        ("interest_rate", ADVERSE_UP),
        ("inflation", ADVERSE_UP),
        ("unemployment", ADVERSE_UP),
        ("gdp_growth", ADVERSE_DOWN),
        ("gog_yield", ADVERSE_UP),
    ):
        assert signed_peak_delta(paths, variable, adverse=adverse) == signed_peak_delta(
            paths, variable
        )
    assert frac_change(paths, "fx_usd_ghs", adverse=ADVERSE_UP) == frac_change(
        paths, "fx_usd_ghs"
    )
    assert frac_change(paths, "gse_index", adverse=ADVERSE_DOWN) == frac_change(
        paths, "gse_index"
    )


# --- 3. The post-management-action minima carry the institution's regime ------


def test_management_actions_do_not_reimpose_basel_minima_on_an_sdi() -> None:
    """``_minima`` omitted ``basel_applicable``, so it defaulted True (P0-9 companion).

    The projection leg deliberately excludes CET1/Tier1/leverage for an SDI
    (Act 930 s.29 / docs/sdi.md §4.6). The post-management-action leg rebuilt the
    check WITHOUT the flag, so the same institution's "with actions" verdict was
    assessed against Basel floors that do not apply to it — an SDI could be shown
    breaching a minimum it is not subject to, purely because a plan was modelled.
    """
    projection = project_enterprise(
        EnterpriseProjectionInputs(
            scenario_code="SEVERE-2027",
            scenario_paths=severe_paths(),
            facts=sample_bank_latest_facts(),
            params=bog_forecast_params(),
            plan=BASE_ASSUMPTIONS,
            horizon_years=3,
        )
    )
    plan = ManagementActionPlan(
        plan_id="sdi-plan",
        name="Rights issue",
        actions=(
            ManagementAction(
                action_id="a1",
                kind="raise_capital",
                label="Rights issue",
                trigger=ActionTrigger(kind="always"),
                capital_raise_ghs=Decimal("10") * M,
            ),
        ),
    )

    def run(params):
        return apply_management_actions(
            projection,
            plan,
            severity="severe",
            capital_params=params,
            paid_up_min=Decimal("0"),
            car_target_pct=Decimal("10"),
        )

    # Basel sub-tier and leverage floors set impossibly high: if they are
    # (wrongly) applied to the SDI, every post-action year breaches them.
    impossible = {
        "cet1_min_pct": Decimal("95"),
        "tier1_min_pct": Decimal("95"),
        "leverage_min_pct": Decimal("95"),
    }
    sdi = run(replace(bog_capital_params(), basel_applicable=False, **impossible))
    for year in sdi.post_action:
        assert year.minima.basel_applicable is False
        assert "cet1" not in year.minima.binding
        assert "tier1" not in year.minima.binding
        assert "leverage" not in year.minima.binding
    assert "cet1" not in sdi.binding_minima

    # A BANK with the same impossible floors still breaches them — unchanged.
    bank = run(replace(bog_capital_params(), **impossible))
    assert any("cet1" in year.minima.binding for year in bank.post_action)


# --- 4. The same shape one layer up (independent re-audit 2026-08-22) ---------
#
# D-8b — the post-management-action overlay MANUFACTURED four 0% ratios whenever
# the plan drove a denominator to zero, and D-23 — an elasticity register that
# does not resolve left a module unstressed while presenting as stressed. Both
# are the P0-9 shape surviving one layer above where it was closed.


def _projection_for_actions():
    return project_enterprise(
        EnterpriseProjectionInputs(
            scenario_code="SEVERE-2027",
            scenario_paths=severe_paths(),
            facts=sample_bank_latest_facts(),
            params=bog_forecast_params(),
            plan=BASE_ASSUMPTIONS,
            horizon_years=3,
        )
    )


def _rwa_reduction_plan(amount: Decimal) -> ManagementActionPlan:
    return ManagementActionPlan(
        plan_id="shrink",
        name="Deleverage",
        actions=(
            ManagementAction(
                action_id="a1",
                kind="reduce_risk",
                label="Reduce risk-weighted assets",
                trigger=ActionTrigger(kind="always"),
                rwa_reduction_ghs=amount,
                effective_year=1,
            ),
        ),
    )


def _apply(plan: ManagementActionPlan):
    return apply_management_actions(
        _projection_for_actions(),
        plan,
        severity="severe",
        capital_params=bog_capital_params(),
        paid_up_min=Decimal("0"),
        car_target_pct=Decimal("10"),
    )


def test_a_plan_that_erases_the_rwa_denominator_refuses_instead_of_filing_zero() -> None:
    """D-8b: the overlay used to report CAR = 0.000000% here, not a refusal.

    ``_position`` computed ``total / rwa if rwa > 0 else 0`` for all four ratios,
    so a plan whose modelled RWA relief exceeded credit RWA (with no market or
    operational charge to floor it) produced a complete, well-formed Appendix II
    "Post-capitalisation" block reading 0% CAR / 0% CET1 / 0% Tier 1 — four
    manufactured regulatory figures where the registered authority
    ``compute_capital_ratios`` raises ``CapitalComputationError``.
    """
    # No market or operational charge, so nothing floors the RWA reduction.
    credit_only = replace(
        bog_capital_params(), fx_charge_pct=Decimal("0"), bia_alpha_pct=Decimal("0")
    )
    projection = project_enterprise(
        EnterpriseProjectionInputs(
            scenario_code="SEVERE-2027",
            scenario_paths=severe_paths(),
            facts=sample_bank_latest_facts(),
            params=replace(bog_forecast_params(), capital=credit_only),
            plan=BASE_ASSUMPTIONS,
            horizon_years=3,
        )
    )
    assert projection.stress[0].rwa.market_rwa == Decimal("0")
    assert projection.stress[0].rwa.operational_rwa == Decimal("0")
    huge = projection.stress[0].rwa.total_rwa * Decimal("10")
    with pytest.raises(NotComputable) as exc:
        apply_management_actions(
            projection,
            _rwa_reduction_plan(huge),
            severity="severe",
            capital_params=credit_only,
            paid_up_min=Decimal("0"),
            car_target_pct=Decimal("10"),
        )
    assert exc.value.state is OutcomeState.NOT_COMPUTABLE
    assert exc.value.blocks_filing is True
    assert exc.value.details[0].metric_id == "post_action_car_pct"
    # Doubly typed: every existing boundary that refuses an invalid plan is unchanged.
    assert isinstance(exc.value, ManagementActionError)
    assert exc.value.code == "post_action_rwa_not_positive"


def test_a_credible_rwa_reduction_still_produces_a_position() -> None:
    """The refusal is about the denominator, not about RWA relief as such."""
    stressed = _projection_for_actions().stress[0]
    modest = stressed.rwa.credit_rwa / Decimal("10")
    result = _apply(_rwa_reduction_plan(modest))
    assert all(year.car_pct > Decimal("0") for year in result.post_action)
    assert all(year.total_rwa > Decimal("0") for year in result.post_action)


def test_an_unresolvable_elasticity_register_refuses_rather_than_unstressing() -> None:
    """D-23: ``{"capital": ()}`` used to translate to ``{}`` — no shocks at all.

    ``translate`` returned early on an empty register, BEFORE both P0-9 guards, so
    a module whose elasticities failed to resolve reported a complete, neutral
    result: every multiplier 1.0, and a bank that "survives" a scenario it was
    never subjected to.
    """
    overrides: dict[str, tuple] = {"capital": ()}
    with pytest.raises(NotComputable) as exc:
        translate(severe_paths(), "capital", overrides)
    assert exc.value.state is OutcomeState.POLICY_UNRESOLVED
    assert exc.value.blocks_filing is True
    assert exc.value.details[0].items == ("register:elasticities:capital",)


@pytest.mark.parametrize("entry", [required_variables, missing_variables])
def test_the_contract_queries_refuse_the_same_empty_register(entry) -> None:
    """The refusal sits at the register, so the pre-flight guard cannot pass it.

    ``missing_variables`` reported "nothing missing" and ``required_variables``
    reported "needs nothing" for an unresolvable register, so the service's
    scenario-completeness pre-check waved the run through before ``translate``
    ever ran.
    """
    overrides: dict[str, tuple] = {"liquidity": ()}
    with pytest.raises(NotComputable):
        if entry is required_variables:
            entry("liquidity", overrides)
        else:
            entry(severe_paths(), "liquidity", overrides)


def test_a_supplied_override_register_still_translates() -> None:
    """A non-empty override is unaffected — the seam stays open for its purpose."""
    mapping = ShockMapping(
        shock_key="parallel_bp",
        kind="additive",
        terms=(
            ElasticityTerm(
                variable="interest_rate", driver="delta", coefficient=Decimal("10000")
            ),
        ),
    )
    shocks = translate(severe_paths(), "irr", {"irr": (mapping,)})
    assert set(shocks) == {"parallel_bp"}
