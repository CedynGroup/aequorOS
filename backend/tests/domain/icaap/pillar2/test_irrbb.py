"""IRRBB interim: losses only, the regulator's shock, and the honest label."""

from __future__ import annotations

from decimal import Decimal

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from app.domain.icaap.pillar2.irrbb import ScenarioDelta, interim_delta_eve
from app.domain.icaap.pillar2.types import MethodStatus, MissingParameter
from tests.domain.icaap.pillar2.conftest import OUTLIER_THRESHOLD_PCT

#: The audit §6.2 ladder, in the engine's sign convention.
LADDER = (
    ScenarioDelta("parallel_up_450", Decimal("-116.759902")),
    ScenarioDelta("parallel_down_450", Decimal("142.996097")),
    ScenarioDelta("parallel_up_200", Decimal("-54.823916")),
    ScenarioDelta("parallel_down_200", Decimal("59.992189")),
)
CODES = [delta.code for delta in LADDER]
REQUIRED = ["parallel_up_450", "parallel_down_450"]


def run(
    deltas: tuple[ScenarioDelta, ...] = LADDER,
    *,
    tier1: Decimal | None = Decimal(700),
    overlay: Decimal | None = None,
):
    return interim_delta_eve(
        deltas=deltas,
        scenario_codes=CODES,
        required_codes=REQUIRED,
        tier1=tier1,
        outlier_threshold_pct_tier1=OUTLIER_THRESHOLD_PCT,
        overlay_stressed_loss=overlay,
    )


def test_the_worst_loss_is_the_add_on_and_gains_never_count() -> None:
    result = run()
    assert result.status is MethodStatus.INTERIM_NON_SF
    assert result.baseline_amount == Decimal("116.7599")
    assert result.detail["worst_scenario"] == "parallel_up_450"
    assert result.detail["loss:parallel_down_450"] == "0"
    assert result.detail["loss:parallel_down_200"] == "0"


def test_the_outlier_statement_is_measured_against_tier_one() -> None:
    assert run(tier1=Decimal(700)).detail["irrbb_outlier_measure_pct"] == "16.679986"
    assert run(tier1=Decimal(700)).detail["outlier"] == "true"
    assert run(tier1=Decimal(800)).detail["irrbb_outlier_measure_pct"] == "14.594988"
    assert run(tier1=Decimal(800)).detail["outlier"] == "false"


def test_the_legacy_absolute_rule_would_have_flagged_a_gain() -> None:
    """D-013: |down450| is the largest move and it is a GAIN, not a breach."""
    largest_absolute = max(abs(delta.delta_eve) for delta in LADDER)
    assert largest_absolute == Decimal("142.996097")
    assert run(tier1=Decimal(800)).detail["outlier"] == "false"


def test_a_missing_mandatory_shock_leaves_the_figure_incomplete() -> None:
    result = run(LADDER[1:])
    assert result.status is MethodStatus.INCOMPLETE
    assert "required_scenario_missing:parallel_up_450" in result.reasons
    assert result.baseline_amount == Decimal("54.8239")


def test_no_governed_scenario_in_the_run_is_not_computable() -> None:
    result = interim_delta_eve(
        deltas=(ScenarioDelta("something_else", Decimal(-10)),),
        scenario_codes=CODES,
        required_codes=REQUIRED,
        tier1=Decimal(700),
        outlier_threshold_pct_tier1=OUTLIER_THRESHOLD_PCT,
    )
    assert result.status is MethodStatus.NOT_COMPUTABLE
    assert "no_governed_scenario_available" in result.reasons
    assert result.baseline_amount is None


def test_the_stressed_column_takes_the_worse_of_the_two() -> None:
    assert run(overlay=None).stressed_derivation == "same_as_baseline"
    higher = run(overlay=Decimal(200))
    assert higher.stressed_amount == Decimal(200)
    assert higher.stressed_derivation == "max_of_baseline_and_scenario"
    lower = run(overlay=Decimal(10))
    assert lower.stressed_amount == Decimal("116.7599")


def test_the_scenario_definition_names_the_governed_set() -> None:
    definition = run().scenario_definition
    assert definition is not None
    assert definition["source"] == "governed_parameter"
    assert definition["required"] == REQUIRED
    assert run().detail["currency_aggregation"] == "single_curve_engine_one_term"


def test_tier_one_is_data_so_its_absence_is_not_a_parameter_refusal() -> None:
    result = run(tier1=None)
    assert result.baseline_amount == Decimal("116.7599")
    assert result.detail["irrbb_outlier_measure_pct"] is None
    assert result.detail["outlier"] is None


@pytest.mark.parametrize(
    ("codes", "required", "threshold", "expected"),
    [
        (None, REQUIRED, Decimal(15), "icaap_irrbb_interim_scenarios"),
        (CODES, None, Decimal(15), "icaap_irrbb_interim_scenarios"),
        (CODES, REQUIRED, None, "irrbb_outlier_threshold_pct_tier1"),
    ],
)
def test_an_unresolved_parameter_is_a_typed_refusal(
    codes: list[str] | None,
    required: list[str] | None,
    threshold: Decimal | None,
    expected: str,
) -> None:
    with pytest.raises(MissingParameter) as error:
        interim_delta_eve(
            deltas=LADDER,
            scenario_codes=codes,
            required_codes=required,
            tier1=Decimal(700),
            outlier_threshold_pct_tier1=threshold,
        )
    assert error.value.param_code == expected


@settings(max_examples=300, deadline=None)
@given(
    values=st.lists(
        st.decimals(min_value=Decimal(-1000), max_value=Decimal(1000), places=4),
        min_size=1,
        max_size=6,
    )
)
def test_an_improving_scenario_can_never_raise_the_charge(values: list[Decimal]) -> None:
    codes = [f"s{index}" for index, _ in enumerate(values)]
    deltas = tuple(ScenarioDelta(code, value) for code, value in zip(codes, values, strict=True))
    result = interim_delta_eve(
        deltas=deltas,
        scenario_codes=codes,
        required_codes=[],
        tier1=Decimal(1000),
        outlier_threshold_pct_tier1=OUTLIER_THRESHOLD_PCT,
    )
    worst_loss = max((-value for value in values), default=Decimal(0))
    if worst_loss <= Decimal(0):
        assert result.baseline_amount == Decimal(0)
    else:
        assert result.baseline_amount == worst_loss.quantize(Decimal("0.0001"))
