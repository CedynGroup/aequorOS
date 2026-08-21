"""Hand-verified tests for the management-actions overlay (Phase 3, ¶78–81).

Each expected value is derived independently against the projection's stress leg
(the "results WITHOUT management actions") and explicit Decimal literals, so the
overlay arithmetic — capital raise, dividend preservation, RWA relief, severity
scaling, triggers, timeline, and fill-residual sizing — is proven, not asserted
against itself.
"""

from __future__ import annotations

import json
from decimal import Decimal

from app.domain.stress.management_actions import (
    ActionTrigger,
    ManagementAction,
    ManagementActionPlan,
    apply_management_actions,
    default_action_plan,
    money,
    ratio_pct,
)
from app.domain.stress.projection import EnterpriseProjectionInputs, project_enterprise
from tests.domain.stress_fixtures import (
    BASE_ASSUMPTIONS,
    base_paths,
    bog_capital_params,
    bog_forecast_params,
    sample_bank_latest_facts,
    severe_paths,
)

_CAR_TARGET = Decimal("13")


def _projection(paths, *, paid_up_min=Decimal("0"), horizon=3):
    return project_enterprise(
        EnterpriseProjectionInputs(
            scenario_code="SEVERE-2027",
            scenario_paths=paths,
            facts=sample_bank_latest_facts(),
            params=bog_forecast_params(),
            plan=BASE_ASSUMPTIONS,
            horizon_years=horizon,
            paid_up_min=paid_up_min,
        )
    )


def _apply(projection, plan, *, severity: str | None = "severe", paid_up_min=Decimal("0")):
    return apply_management_actions(
        projection,
        plan,
        severity=severity,
        capital_params=bog_capital_params(),
        paid_up_min=paid_up_min,
        car_target_pct=_CAR_TARGET,
    )


def _one(action: ManagementAction, plan_id: str = "p") -> ManagementActionPlan:
    return ManagementActionPlan(plan_id=plan_id, name="test", actions=(action,))


# --- Triggers ----------------------------------------------------------------


def test_base_scenario_triggers_no_actions_and_with_equals_without() -> None:
    """Base scenario clears every minimum ⇒ nothing fires ⇒ WITH == WITHOUT."""
    projection = _projection(base_paths())
    result = _apply(projection, default_action_plan(), severity=None)
    assert all(not action.fired for action in result.actions)
    assert result.stays_above_all_minima is True
    assert result.residual_capital_required == Decimal("0")
    for post, stress in zip(result.post_action, projection.stress, strict=True):
        assert post.car_pct == stress.ratios.car_pct
        assert post.total_capital == stress.ratios.total_capital
        assert post.total_rwa == stress.rwa.total_rwa
        assert post.paid_up == stress.paid_up


def test_on_breach_watch_minima_targets_a_specific_floor() -> None:
    """A paid-up-only breach fires a paid-up watcher but not a CAR watcher."""
    projection = _projection(severe_paths(), paid_up_min=Decimal("400000000"))
    car_watcher = ManagementAction(
        action_id="car_only",
        kind="raise_capital",
        label="CAR watch",
        trigger=ActionTrigger(kind="on_breach", watch_minima=("car",)),
        capital_raise_ghs=Decimal("50000000"),
    )
    paid_up_watcher = ManagementAction(
        action_id="paid_up_only",
        kind="raise_capital",
        label="Paid-up watch",
        trigger=ActionTrigger(kind="on_breach", watch_minima=("paid_up",)),
        capital_raise_ghs=Decimal("50000000"),
    )
    car_result = _apply(
        projection, _one(car_watcher), paid_up_min=Decimal("400000000")
    )
    paid_up_result = _apply(
        projection, _one(paid_up_watcher), paid_up_min=Decimal("400000000")
    )
    assert car_result.actions[0].fired is False
    assert paid_up_result.actions[0].fired is True


def test_on_severity_trigger_respects_the_threshold() -> None:
    projection = _projection(severe_paths())
    action = ManagementAction(
        action_id="sev",
        kind="reduce_risk",
        label="Severity-gated risk reduction",
        trigger=ActionTrigger(kind="on_severity", min_severity="severe"),
        rwa_reduction_ghs=Decimal("100000000"),
    )
    assert _apply(projection, _one(action), severity="severe").actions[0].fired is True
    assert _apply(projection, _one(action), severity="moderate").actions[0].fired is False
    assert _apply(projection, _one(action), severity=None).actions[0].fired is False


# --- Quantified effects ------------------------------------------------------


def test_fixed_capital_raise_lifts_capital_paid_up_and_car() -> None:
    projection = _projection(severe_paths())
    raise_amount = Decimal("100000000")
    action = ManagementAction(
        action_id="equity",
        kind="raise_capital",
        label="Rights issue",
        trigger=ActionTrigger(kind="always"),
        effective_year=1,
        capital_raise_ghs=raise_amount,
        counts_as_paid_up=True,
    )
    # severity=None ⇒ factor 1.0, so the raise lands at its full authored amount.
    result = _apply(projection, _one(action), severity=None)
    assert result.actions[0].fired is True
    assert result.actions[0].resolved_capital_raise == raise_amount
    for post, stress in zip(result.post_action, projection.stress, strict=True):
        assert post.cet1 == money(stress.ratios.cet1_capital + raise_amount)
        assert post.total_capital == money(stress.ratios.total_capital + raise_amount)
        assert post.paid_up == money(stress.paid_up + raise_amount)
        expected_car = ratio_pct(
            (stress.ratios.total_capital + raise_amount) / stress.rwa.total_rwa * Decimal("100")
        )
        assert post.car_pct == expected_car
    # Year-1 golden (independently computed): total 359,882,114.8750 + 100M over
    # RWA 2,189,361,250 ⇒ 21.005310%.
    assert result.post_action[0].car_pct == Decimal("21.005310")


def test_rwa_reduction_raises_car_and_shrinks_leverage_exposure() -> None:
    projection = _projection(severe_paths())
    reduction = Decimal("200000000")
    action = ManagementAction(
        action_id="derisk",
        kind="reduce_risk",
        label="Tighten lending / cut limits",
        trigger=ActionTrigger(kind="always"),
        effective_year=1,
        rwa_reduction_ghs=reduction,
        shrinks_leverage_exposure=True,
    )
    result = _apply(projection, _one(action), severity=None)
    for post, stress in zip(result.post_action, projection.stress, strict=True):
        # Credit RWA relief flows straight through to total RWA (relief < credit).
        assert post.total_rwa == money(stress.rwa.total_rwa - reduction)
        assert post.leverage_exposure == money(stress.ratios.leverage_exposure - reduction)
        assert post.car_pct > stress.ratios.car_pct
        assert post.aggregate.rwa_reduction_risk_reduction == reduction
    # Year-1 golden: RWA 2,189,361,250 − 200M = 1,989,361,250 ⇒ CAR 18.090335%.
    assert result.post_action[0].total_rwa == Decimal("1989361250.0000")
    assert result.post_action[0].car_pct == Decimal("18.090335")


def test_dividend_reduction_accumulates_into_cet1() -> None:
    projection = _projection(severe_paths())
    action = ManagementAction(
        action_id="div_cut",
        kind="revise_dividend",
        label="Suspend distributions",
        trigger=ActionTrigger(kind="always"),
        effective_year=1,
        dividend_reduction_pct=Decimal("100"),
    )
    result = _apply(projection, _one(action), severity=None)
    cumulative = Decimal("0")
    for post, stress in zip(result.post_action, projection.stress, strict=True):
        cumulative = money(cumulative + stress.pnl.dividends)
        assert post.cet1 == money(stress.ratios.cet1_capital + cumulative)
    # 100% of each stress year's distribution preserved and carried forward.
    assert result.actions[0].dividend_preserved_total == Decimal("28034312.5849")
    assert result.post_action[0].cet1 == Decimal("288403021.2500")


def test_severity_differentiation_scales_the_action() -> None:
    projection = _projection(severe_paths())
    action = ManagementAction(
        action_id="equity",
        kind="raise_capital",
        label="Rights issue",
        trigger=ActionTrigger(kind="always"),
        capital_raise_ghs=Decimal("100000000"),
    )
    mild = _apply(projection, _one(action), severity="mild")
    severe = _apply(projection, _one(action), severity="severe")
    # Default factors: mild 0.5, severe 1.0 (¶81).
    assert mild.actions[0].severity_factor == Decimal("0.5")
    assert severe.actions[0].severity_factor == Decimal("1")
    assert mild.actions[0].resolved_capital_raise == Decimal("50000000.0000")
    assert severe.actions[0].resolved_capital_raise == Decimal("100000000.0000")
    assert mild.post_action[0].total_capital < severe.post_action[0].total_capital


# --- Fill-residual sizing + timeline -----------------------------------------


def test_fill_residual_restores_a_breaching_scenario_above_all_minima() -> None:
    projection = _projection(severe_paths(), paid_up_min=Decimal("400000000"))
    assert projection.stress_stays_above_all_minima is False  # paid-up breach
    action = ManagementAction(
        action_id="fill",
        kind="raise_capital",
        label="Capital raise to restore adequacy",
        trigger=ActionTrigger(kind="on_breach"),
        effective_year=1,
        sizing="fill_residual",
        capital_raise_ghs=Decimal("1"),  # sentinel; sizing overrides
        counts_as_paid_up=True,
    )
    result = _apply(projection, _one(action), paid_up_min=Decimal("400000000"))
    # Paid-up floor 400M − 150M paid-up = 250M residual; CAR already clears 13%.
    assert result.actions[0].resolved_capital_raise == Decimal("250000000.0000")
    assert result.stays_above_all_minima is True
    assert result.first_breach_year is None
    assert result.residual_capital_required == Decimal("0.0000")
    for post in result.post_action:
        assert post.minima.all_ok is True
        assert post.paid_up == Decimal("400000000.0000")
        assert post.residual_capital_required == Decimal("0.0000")


def test_capital_raise_timeline_delays_the_effect() -> None:
    projection = _projection(severe_paths(), paid_up_min=Decimal("400000000"))
    action = ManagementAction(
        action_id="fill",
        kind="raise_capital",
        label="Capital raise (year 2)",
        trigger=ActionTrigger(kind="on_breach"),
        effective_year=2,  # an external raise takes time
        sizing="fill_residual",
        capital_raise_ghs=Decimal("1"),
        counts_as_paid_up=True,
    )
    result = _apply(projection, _one(action), paid_up_min=Decimal("400000000"))
    # Year 1 is still breached (the raise only lands from year 2).
    assert result.first_breach_year == 1
    assert result.stays_above_all_minima is False
    assert result.post_action[0].minima.all_ok is False
    assert result.post_action[1].minima.all_ok is True
    assert result.post_action[2].minima.all_ok is True
    # The worst residual is the un-remediated year-1 paid-up shortfall.
    assert result.residual_capital_required == Decimal("250000000.0000")


# --- Serialization + reproducibility -----------------------------------------


def test_result_serializes_json_safe() -> None:
    projection = _projection(severe_paths(), paid_up_min=Decimal("400000000"))
    result = _apply(projection, default_action_plan(), paid_up_min=Decimal("400000000"))
    serialized = result.serialize()
    json.dumps(serialized)
    assert serialized["plan_id"] == "default_recovery"
    assert len(serialized["post_action"]) == 3  # type: ignore[arg-type]


def test_overlay_is_reproducible() -> None:
    projection = _projection(severe_paths(), paid_up_min=Decimal("400000000"))
    first = _apply(projection, default_action_plan(), paid_up_min=Decimal("400000000")).serialize()
    second = _apply(
        projection, default_action_plan(), paid_up_min=Decimal("400000000")
    ).serialize()
    assert first == second
