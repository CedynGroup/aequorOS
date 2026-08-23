"""Every stress substitution site refuses instead of inventing a benign number.

Independent re-audit 2026-08-22 **D-8**: the stress engine substituted a plausible
value wherever a required input was missing. In a stress test that is the worst
possible direction — the substituted value is invariably the benign one, so a
bank with incomplete data receives a *reassuring* result rather than a refusal.

Each test below names one substitution site, states the number the old code
manufactured, and pins the ``NotComputable`` that replaces it. Every one of them
FAILS against the pre-fix engine, which returned a complete, well-formed,
flattering result.

Sibling module ``test_stress_scenario_fail_closed`` covers the earlier P0-9 /
D-8b / D-23 shapes (empty and incomplete scenarios, the post-management-action
denominator, the unresolvable elasticity register).
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.domain.authority.outcomes import NotComputable, OutcomeState
from app.domain.stress.appendix_ii import build_appendix_ii
from app.domain.stress.contingent_leverage import (
    ContingentLeverageInputs,
    DerivativePosition,
    compute_contingent_leverage,
)
from app.domain.stress.credit_bottom_up import CreditExposure, result_at_peak, result_for_year
from app.domain.stress.management_actions import (
    ActionTrigger,
    ManagementAction,
    ManagementActionError,
    ManagementActionPlan,
    apply_management_actions,
)
from app.domain.stress.orchestrator import (
    EnterpriseStressInputs,
    IrrStressInputs,
    compose_capital_shocks,
    run_enterprise_stress,
)
from app.domain.stress.projection import (
    EnterpriseProjectionInputs,
    project_enterprise,
)
from app.domain.stress.translation import MacroPathPoint, frac_change
from tests.domain.stress_fixtures import (
    BASE_ASSUMPTIONS,
    bog_capital_params,
    bog_forecast_params,
    bog_liquidity_params,
    capital_facts,
    liquidity_facts,
    sample_bank_latest_facts,
    severe_paths,
)

M = Decimal("1000000")
_INCOME = Decimal("180") * M
_ALLOWANCE = Decimal("15") * M


def _without(variable: str, *, year: int | None = None) -> tuple[MacroPathPoint, ...]:
    """The severe scenario with one variable dropped (entirely, or at one year)."""
    return tuple(
        point
        for point in severe_paths()
        if not (
            point.variable == variable and (year is None or point.year_index == year)
        )
    )


def _projection(paths=None, **overrides):
    defaults = {
        "scenario_code": "SEVERE-2027",
        "scenario_paths": severe_paths() if paths is None else paths,
        "facts": sample_bank_latest_facts(),
        "params": bog_forecast_params(),
        "plan": BASE_ASSUMPTIONS,
        "horizon_years": 3,
    }
    defaults.update(overrides)
    return project_enterprise(EnterpriseProjectionInputs(**defaults))  # type: ignore[arg-type]


# --- 1. The capital-path composition's own macro contract --------------------
#
# ``translate("capital")`` validates only the capital elasticity register's own
# drivers (GDP / unemployment / equity index). ``compose_capital_shocks`` reads
# THREE more straight off the paths through ``frac_change`` / ``signed_peak_delta``,
# which answer 0 for an absent variable.


@pytest.mark.parametrize("variable", ["fx_usd_ghs", "gdp_growth", "interest_rate"])
def test_the_capital_path_refuses_a_driver_it_reads_but_never_checked(variable: str) -> None:
    """Old behaviour: a benign, complete four-key capital path from a broken scenario.

    Dropping ``fx_usd_ghs`` produced ``fx_rwa_multiplier = 1.0`` and no FX-driven
    RWA growth; dropping ``interest_rate`` produced no income compression at all.
    """
    with pytest.raises(NotComputable) as exc:
        compose_capital_shocks(
            scenario_paths=_without(variable),
            baseline_annual_preprovision_income=_INCOME,
            baseline_credit_allowance=_ALLOWANCE,
        )
    assert exc.value.state is OutcomeState.MISSING_REQUIRED_INPUT
    assert exc.value.blocks_filing is True
    assert f"macro:{variable}" in exc.value.details[0].items


def test_a_flat_driver_still_satisfies_the_composition_contract() -> None:
    """The escape hatch: irrelevant means AUTHORED FLAT, not omitted."""
    flat_fx = tuple(
        MacroPathPoint(point.variable, point.year_index, point.base_value, point.base_value)
        if point.variable == "fx_usd_ghs"
        else point
        for point in severe_paths()
    )
    composition = compose_capital_shocks(
        scenario_paths=flat_fx,
        baseline_annual_preprovision_income=_INCOME,
        baseline_credit_allowance=_ALLOWANCE,
    )
    assert composition.stressed["fx_rwa_multiplier"] == Decimal("1.0000")


# --- 2. The stressed credit loss ---------------------------------------------


def test_no_ecl_data_and_no_allowance_refuses_rather_than_a_costless_stress() -> None:
    """Old behaviour: ``0 x pd x lgd`` — a credit stress with no credit loss.

    The single most reassuring figure a credit stress can manufacture, and
    indistinguishable from a book that genuinely does not deteriorate.
    """
    with pytest.raises(NotComputable) as exc:
        compose_capital_shocks(
            scenario_paths=severe_paths(),
            baseline_annual_preprovision_income=_INCOME,
        )
    assert exc.value.state is OutcomeState.MISSING_REQUIRED_INPUT
    assert exc.value.details[0].metric_id == "stressed_credit_loss"
    assert "fact:credit_allowance" in exc.value.details[0].items


# --- 3. ΔEVE against a Tier 1 that is not there -------------------------------


def test_the_irr_impact_refuses_a_zero_tier1_instead_of_reporting_zero_percent() -> None:
    """Old behaviour: ``delta_eve_pct_tier1 = 0.000000`` — "costs nothing"."""
    with pytest.raises(NotComputable) as exc:
        run_enterprise_stress(
            EnterpriseStressInputs(
                scenario_code="SEVERE-2027",
                scenario_paths=severe_paths(),
                capital_facts=capital_facts(),
                capital_params=bog_capital_params(),
                liquidity_facts=liquidity_facts(),
                liquidity_params=bog_liquidity_params(),
                baseline_annual_preprovision_income=_INCOME,
                baseline_credit_allowance=_ALLOWANCE,
                irr=IrrStressInputs(
                    positions=(), curve={Decimal("1"): Decimal("0.2")}, tier1=Decimal("0")
                ),
            )
        )
    assert exc.value.state is OutcomeState.NOT_COMPUTABLE
    assert exc.value.details[0].metric_id == "delta_eve_pct_tier1"


# --- 4. The projection's per-year plan perturbation ---------------------------
#
# The service pre-check validates the WHOLE path for the capital + liquidity
# registers and, per year, only the CAPITAL register's drivers. A scenario whose
# rate path stops after year 1 therefore passes every service guard.


@pytest.mark.parametrize("variable", ["gdp_growth", "inflation", "interest_rate"])
def test_the_stress_leg_refuses_a_year_missing_a_driver_it_perturbs_the_plan_with(
    variable: str,
) -> None:
    """Old behaviour: year 2 projected on the bank's own unstressed business plan."""
    with pytest.raises(NotComputable) as exc:
        _projection(_without(variable, year=2))
    assert exc.value.state is OutcomeState.MISSING_REQUIRED_INPUT
    assert f"macro:{variable}" in exc.value.details[0].items


def test_the_stress_leg_refuses_a_year_with_no_fx_path() -> None:
    """Old behaviour: the year-1 FX revaluation silently fell back to the plan's own."""
    with pytest.raises(NotComputable) as exc:
        _projection(_without("fx_usd_ghs", year=1))
    assert exc.value.state is OutcomeState.MISSING_REQUIRED_INPUT
    assert "macro:fx_usd_ghs" in exc.value.details[0].items


def test_a_partial_credit_rwa_uplift_refuses_instead_of_unstressing_the_tail() -> None:
    """Old behaviour: year 3 fell back to a 1.0 factor while years 1-2 carried the uplift."""
    with pytest.raises(NotComputable) as exc:
        _projection(credit_rwa_uplift={1: Decimal("1.10"), 2: Decimal("1.12")})
    assert exc.value.state is OutcomeState.MISSING_REQUIRED_INPUT
    assert exc.value.details[0].items == ("input:credit_rwa_uplift@y3",)


def test_a_complete_credit_rwa_uplift_still_applies() -> None:
    projection = _projection(
        credit_rwa_uplift={1: Decimal("1.10"), 2: Decimal("1.12"), 3: Decimal("1.15")}
    )
    unlifted = _projection()
    assert projection.stress[2].rwa.credit_rwa > unlifted.stress[2].rwa.credit_rwa


# --- 5. A fractional change against a zero base -------------------------------


def test_a_zero_base_refuses_rather_than_dropping_the_year_from_the_peak() -> None:
    """Old behaviour: the zero-base year was SKIPPED, silently, in every consumer."""
    paths = (
        MacroPathPoint("fx_usd_ghs", 1, Decimal("0"), Decimal("15")),
        MacroPathPoint("fx_usd_ghs", 2, Decimal("12.5"), Decimal("13")),
    )
    with pytest.raises(NotComputable) as exc:
        frac_change(paths, "fx_usd_ghs")
    assert exc.value.state is OutcomeState.DATA_QUALITY_BLOCK
    assert exc.value.details[0].items == ("macro:fx_usd_ghs@y1",)


# --- 6. The bottom-up book's FX revaluation ------------------------------------


def _fc_book() -> tuple[CreditExposure, ...]:
    return (
        CreditExposure("E1", "corporates", Decimal("100") * M, Decimal("2"), Decimal("45"),
                       Decimal("100"), is_foreign_currency=True),
    )


def _domestic_book() -> tuple[CreditExposure, ...]:
    return (
        CreditExposure("E1", "corporates", Decimal("100") * M, Decimal("2"), Decimal("45"),
                       Decimal("100")),
    )


def test_a_foreign_currency_book_refuses_a_scenario_with_no_fx_path() -> None:
    """Old behaviour: every FC exposure kept its base EAD — no revaluation at all."""
    paths = _without("fx_usd_ghs")
    with pytest.raises(NotComputable) as exc:
        result_at_peak(_fc_book(), paths)
    assert exc.value.state is OutcomeState.MISSING_REQUIRED_INPUT
    assert exc.value.details[0].items == ("macro:fx_usd_ghs",)
    with pytest.raises(NotComputable):
        result_for_year(_fc_book(), paths, 1)


def test_a_purely_domestic_book_does_not_need_the_fx_path() -> None:
    """The refusal is scoped to books the FX channel actually revalues."""
    result = result_at_peak(_domestic_book(), _without("fx_usd_ghs"))
    assert result.fx_revaluation_rwa == Decimal("0.0000")


# --- 7. Management-action plan vocabularies + severity pricing -----------------


def _plan(action: ManagementAction) -> ManagementActionPlan:
    return ManagementActionPlan(plan_id="p", name="Plan", actions=(action,))


def test_an_action_kind_outside_the_vocabulary_refuses() -> None:
    """Old behaviour: the action fired, reported a trigger reason, and did NOTHING.

    ``ActionKind`` is a typing-only Literal, erased at runtime, and a governed
    plan arrives from the database as plain strings.
    """
    with pytest.raises(ManagementActionError) as exc:
        ManagementAction(
            action_id="a1",
            kind="reduce_rwa",  # type: ignore[arg-type] - the exact typo this pins
            label="Reduce risk-weighted assets",
            trigger=ActionTrigger(kind="always"),
            rwa_reduction_ghs=Decimal("100") * M,
        )
    assert exc.value.code == "invalid_action_kind"


def test_an_rwa_relief_on_a_kind_that_cannot_carry_one_refuses() -> None:
    """Old behaviour: ``_apply_rwa`` filtered on kind and dropped the relief silently."""
    with pytest.raises(ManagementActionError) as exc:
        ManagementAction(
            action_id="a1",
            kind="raise_capital",
            label="Rights issue",
            trigger=ActionTrigger(kind="always"),
            capital_raise_ghs=Decimal("10") * M,
            rwa_reduction_ghs=Decimal("100") * M,
        )
    assert exc.value.code == "effect_not_supported_by_kind"


def test_an_on_severity_trigger_without_a_recognised_threshold_refuses() -> None:
    """Old behaviour: an unrecognised threshold ranked 0, so the action fired always."""
    with pytest.raises(ManagementActionError) as exc:
        ActionTrigger(kind="on_severity", min_severity="catastrophic")
    assert exc.value.code == "invalid_min_severity"


def test_a_trigger_watching_an_unknown_minimum_refuses() -> None:
    """Old behaviour: the name never matched a breach, so the action never fired."""
    with pytest.raises(ManagementActionError) as exc:
        ActionTrigger(kind="on_breach", watch_minima=("car", "liquidity_coverage"))
    assert exc.value.code == "invalid_watch_minima"


def test_a_severity_the_plan_never_priced_refuses_instead_of_the_fullest_lever() -> None:
    """Old behaviour: factor 1.0 — which IS the severe factor, the largest lever."""
    action = ManagementAction(
        action_id="a1",
        kind="raise_capital",
        label="Rights issue",
        trigger=ActionTrigger(kind="always"),
        capital_raise_ghs=Decimal("100") * M,
        severity_factors={"mild": Decimal("0.5")},
    )
    with pytest.raises(NotComputable) as exc:
        apply_management_actions(
            _projection(),
            _plan(action),
            severity="severe",
            capital_params=bog_capital_params(),
            paid_up_min=Decimal("0"),
            car_target_pct=Decimal("10"),
        )
    assert exc.value.state is OutcomeState.POLICY_UNRESOLVED
    assert exc.value.details[0].items == ("action:a1", "severity:severe")
    # Doubly typed, so every boundary that already refuses an invalid plan is unchanged.
    assert isinstance(exc.value, ManagementActionError)
    assert exc.value.code == "severity_factor_unresolved"


def test_a_priced_severity_still_scales_the_action() -> None:
    action = ManagementAction(
        action_id="a1",
        kind="raise_capital",
        label="Rights issue",
        trigger=ActionTrigger(kind="always"),
        capital_raise_ghs=Decimal("100") * M,
        severity_factors={"mild": Decimal("0.5"), "severe": Decimal("1")},
    )
    result = apply_management_actions(
        _projection(),
        _plan(action),
        severity="mild",
        capital_params=bog_capital_params(),
        paid_up_min=Decimal("0"),
        car_target_pct=Decimal("10"),
    )
    assert result.actions[0].resolved_capital_raise == Decimal("50000000.0000")


# --- 8. The contingent-leverage ratio -----------------------------------------


def test_a_missing_leverage_exposure_refuses_instead_of_a_zero_percent_ratio() -> None:
    """Old behaviour: base 0.000000% and stressed 0.000000%, impact a soothing 0pp."""
    with pytest.raises(NotComputable) as exc:
        compute_contingent_leverage(
            ContingentLeverageInputs(
                base_leverage_exposure=Decimal("0"),
                tier1=Decimal("100") * M,
                derivatives=(
                    DerivativePosition("D1", Decimal("400") * M, Decimal("20") * M),
                ),
            )
        )
    assert exc.value.state is OutcomeState.NOT_COMPUTABLE
    assert exc.value.details[0].metric_id == "contingent_leverage_ratio_pct"


# --- 9. Appendix II Table 1's loss decomposition ------------------------------


def test_a_partial_bottom_up_decomposition_refuses_instead_of_mixing_methodologies() -> None:
    """Old behaviour: uncovered years silently used the credit-RWA-share proxy.

    Two methodologies inside one filed Table 1, with nothing in the table saying
    which line came from which.
    """
    projection = _projection()
    losses = {1: {"corporates": Decimal("5") * M}, 2: {"corporates": Decimal("6") * M}}
    with pytest.raises(NotComputable) as exc:
        build_appendix_ii(
            projection,
            severe_paths(),
            currency="GHS",
            car_target_pct=Decimal("13"),
            paid_up_min=Decimal("400") * M,
            exposure_class_losses=losses,
        )
    assert exc.value.state is OutcomeState.MISSING_REQUIRED_INPUT
    assert exc.value.details[0].items == ("stress_year:3",)
