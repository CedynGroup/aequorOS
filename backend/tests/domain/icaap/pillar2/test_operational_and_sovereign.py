"""Operational and sovereign: severe scenarios, netted against Pillar 1."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.domain.icaap.pillar2.operational import (
    OperationalScenario,
    operational_scenario_net_p1,
)
from app.domain.icaap.pillar2.sovereign import (
    FILL_BOTH_UNKNOWN,
    FILL_CURRENCY_UNKNOWN,
    FILL_EXACT,
    FILL_TENOR_UNKNOWN,
    SovereignHolding,
    parse_haircut_grid,
    resolve_haircut,
    sovereign_stress_addon,
)
from app.domain.icaap.pillar2.types import MethodStatus, MissingParameter
from tests.domain.icaap.pillar2.conftest import (
    CAR_MIN_PCT,
    OPERATIONAL_SEVERITIES,
    SOVEREIGN_GRID_BODY,
)

GROSS_INCOME = Decimal(1000)


def operational(
    *,
    operational_rwa: Decimal | None = Decimal(500),
    bank_scenarios: tuple[OperationalScenario, ...] = (),
    gross_income: Decimal | None = GROSS_INCOME,
    stressed_rwa: Decimal | None = None,
):
    return operational_scenario_net_p1(
        gross_income=gross_income,
        severities_pct_gross_income=OPERATIONAL_SEVERITIES,
        operational_rwa=operational_rwa,
        car_min_pct=CAR_MIN_PCT,
        bank_scenarios=bank_scenarios,
        stressed_operational_rwa=stressed_rwa,
    )


def test_the_worst_governed_scenario_net_of_pillar_one() -> None:
    result = operational()
    assert result.status is MethodStatus.COMPUTED
    assert result.detail["worst_loss"] == "120.0000"
    assert result.detail["pillar1_operational_capital"] == "65.0000"
    assert result.baseline_amount == Decimal(55)


def test_a_pillar_one_charge_that_already_covers_the_scenario_adds_nothing() -> None:
    assert operational(operational_rwa=Decimal(1000)).baseline_amount == Decimal(0)


def test_a_worse_bank_defined_scenario_wins() -> None:
    result = operational(
        bank_scenarios=(
            OperationalScenario(
                key="core_banking_outage",
                definition="Five-day outage of the core banking platform",
                loss_amount=Decimal(300),
                evidence_attachment_id="att-1",
            ),
        )
    )
    assert result.scenario_definition is not None
    assert result.scenario_definition["worst_scenario"] == "core_banking_outage"
    assert result.baseline_amount == Decimal(235)


def test_a_bank_scenario_without_a_written_definition_is_incomplete() -> None:
    result = operational(
        bank_scenarios=(OperationalScenario(key="unnamed", loss_amount=Decimal(300)),)
    )
    assert result.status is MethodStatus.INCOMPLETE
    assert "scenario_definition_missing:unnamed" in result.reasons


def test_without_gross_income_the_governed_scenarios_cannot_be_sized() -> None:
    result = operational(gross_income=None)
    assert result.status is MethodStatus.NOT_COMPUTABLE
    assert "gross_income_unavailable" in result.reasons


def test_the_stressed_column_is_recomputed_on_the_stressed_charge() -> None:
    result = operational(stressed_rwa=Decimal(400))
    assert result.stressed_derivation == "method"
    assert result.stressed_amount == Decimal(68)


def test_an_unresolved_severity_table_is_a_typed_refusal() -> None:
    with pytest.raises(MissingParameter) as error:
        operational_scenario_net_p1(
            gross_income=GROSS_INCOME,
            severities_pct_gross_income=None,
            operational_rwa=Decimal(500),
            car_min_pct=CAR_MIN_PCT,
        )
    assert error.value.param_code == "op_p2_scenario_severity_pct_gross_income"


def grid():
    return parse_haircut_grid(SOVEREIGN_GRID_BODY)


def test_the_sovereign_reference_case() -> None:
    result = sovereign_stress_addon(
        holdings=[
            SovereignHolding("domestic_notes", "reporting", "1y_to_5y", Decimal(400)),
            SovereignHolding("eurobond", "foreign", "up_to_1y", Decimal(100)),
        ],
        grid=grid(),
        car_min_pct=CAR_MIN_PCT,
    )
    assert result.baseline_amount == Decimal(115)
    assert result.detail["fill:domestic_notes"] == FILL_EXACT
    assert result.reasons == ()
    assert result.stressed_derivation == "same_as_baseline"
    assert result.stressed_amount == Decimal(115)


def test_the_pillar_one_charge_on_the_same_holdings_is_netted() -> None:
    result = sovereign_stress_addon(
        holdings=[
            SovereignHolding(
                "eurobond", "foreign", "up_to_1y", Decimal(100), pillar1_rwa=Decimal(100)
            )
        ],
        grid=grid(),
        car_min_pct=CAR_MIN_PCT,
    )
    assert result.detail["pillar1_sovereign_capital"] == "13.0000"
    assert result.baseline_amount == Decimal(2)


@pytest.mark.parametrize(
    ("currency_kind", "tenor", "expected", "fill"),
    [
        ("reporting", "over_5y", Decimal(35), FILL_EXACT),
        ("unknown", "up_to_1y", Decimal(15), FILL_CURRENCY_UNKNOWN),
        ("reporting", None, Decimal(35), FILL_TENOR_UNKNOWN),
        ("unknown", None, Decimal(45), FILL_BOTH_UNKNOWN),
    ],
)
def test_an_unclassified_holding_is_filled_with_the_worst_matching_cell(
    currency_kind: str, tenor: str | None, expected: Decimal, fill: str
) -> None:
    haircut, applied = resolve_haircut(grid(), currency_kind, tenor)
    assert (haircut, applied) == (expected, fill)


def test_a_conservative_fill_is_reported_so_it_can_be_replaced() -> None:
    result = sovereign_stress_addon(
        holdings=[SovereignHolding("unclassified", "unknown", None, Decimal(100))],
        grid=grid(),
        car_min_pct=CAR_MIN_PCT,
    )
    assert result.baseline_amount == Decimal(45)
    assert "conservative_fill" in result.reasons
    assert result.detail["conservative_fill"] == "true"


def test_no_sovereign_exposure_is_not_computable() -> None:
    result = sovereign_stress_addon(holdings=[], grid=grid(), car_min_pct=CAR_MIN_PCT)
    assert result.status is MethodStatus.NOT_COMPUTABLE
    assert result.reasons == ("no_sovereign_exposure",)


def test_an_unresolved_haircut_grid_is_a_typed_refusal() -> None:
    with pytest.raises(MissingParameter) as error:
        sovereign_stress_addon(holdings=[], grid=None, car_min_pct=CAR_MIN_PCT)
    assert error.value.param_code == "sov_p2_haircut_pct"
    with pytest.raises(MissingParameter):
        parse_haircut_grid(None)
    with pytest.raises(MissingParameter):
        parse_haircut_grid({})
