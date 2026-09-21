"""Every capitalising method refuses, by code, when a governed value is absent.

D-024 §4: there is no fallback anywhere. This sweeps the methods in one place
so a new one cannot quietly ship with a default — the sweep is parametrised by
method, and a method that returns a number from nothing fails here.
"""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal

import pytest

from app.domain.icaap.pillar2.concentration import (
    DIMENSION_SECTOR,
    DIMENSION_SINGLE_NAME,
    METRIC_HHI,
    DimensionVector,
    benchmark_mapped,
    hhi_proportional_heuristic,
)
from app.domain.icaap.pillar2.fx import CurrencyPosition, FxShockSet, fx_nop_addon
from app.domain.icaap.pillar2.irrbb import ScenarioDelta, interim_delta_eve
from app.domain.icaap.pillar2.judgemental import (
    COMPONENT_DIVERSIFICATION,
    validate_judgemental,
)
from app.domain.icaap.pillar2.operational import operational_scenario_net_p1
from app.domain.icaap.pillar2.sovereign import SovereignHolding, sovereign_stress_addon
from app.domain.icaap.pillar2.types import MissingParameter
from app.domain.icaap.units import Denominators

NAMES = DimensionVector(DIMENSION_SINGLE_NAME, (Decimal(50), Decimal(50)), Decimal(0))
SECTORS = DimensionVector(DIMENSION_SECTOR, (Decimal(50), Decimal(50)), Decimal(0))
DENOMINATORS = Denominators(
    total_rwa=Decimal(1000), credit_rwa=Decimal(800), car_min_pct=Decimal(13)
)


def _benchmark_without_metric_set() -> object:
    return benchmark_mapped(
        name_vector=NAMES,
        sector_vector=SECTORS,
        metric_set=None,
        cr_n=None,
        tables={},
        min_coverage_pct=Decimal(80),
        baseline=DENOMINATORS,
    )


def _benchmark_without_coverage_floor() -> object:
    return benchmark_mapped(
        name_vector=NAMES,
        sector_vector=SECTORS,
        metric_set={DIMENSION_SINGLE_NAME: [METRIC_HHI]},
        cr_n=None,
        tables={},
        min_coverage_pct=None,
        baseline=DENOMINATORS,
    )


def _heuristic_without_coefficients() -> object:
    return hhi_proportional_heuristic(
        name_vector=NAMES,
        sector_vector=SECTORS,
        name_coeff=None,
        sector_coeff=Decimal("0.5"),
        baseline=DENOMINATORS,
    )


def _irrbb_without_shock_set() -> object:
    return interim_delta_eve(
        deltas=(ScenarioDelta("parallel_up_450", Decimal(-10)),),
        scenario_codes=None,
        required_codes=None,
        tier1=Decimal(700),
        outlier_threshold_pct_tier1=Decimal(15),
    )


def _fx_without_shocks() -> object:
    return fx_nop_addon(
        positions=[CurrencyPosition("USD", Decimal(100))],
        shocks=None,
        market_rwa=Decimal(50),
        car_min_pct=Decimal(13),
    )


def _fx_without_car_min() -> object:
    return fx_nop_addon(
        positions=[CurrencyPosition("USD", Decimal(100))],
        shocks=FxShockSet.from_payload({"depreciation": {"default": 30}}),
        market_rwa=Decimal(50),
        car_min_pct=None,
    )


def _operational_without_severities() -> object:
    return operational_scenario_net_p1(
        gross_income=Decimal(1000),
        severities_pct_gross_income=None,
        operational_rwa=Decimal(500),
        car_min_pct=Decimal(13),
    )


def _sovereign_without_grid() -> object:
    return sovereign_stress_addon(
        holdings=[SovereignHolding("a", "reporting", "le_1y", Decimal(100))],
        grid=None,
        car_min_pct=Decimal(13),
    )


def _judgemental_without_the_switch() -> object:
    return validate_judgemental(
        component=COMPONENT_DIVERSIFICATION,
        baseline=Decimal(-1),
        rationale="x",
        evidence_count=1,
    )


CASES: list[tuple[str, Callable[[], object], str]] = [
    ("benchmark_mapped/metric_set", _benchmark_without_metric_set, "ccr_metric_set"),
    (
        "benchmark_mapped/coverage",
        _benchmark_without_coverage_floor,
        "ccr_min_dimension_coverage_pct",
    ),
    ("hhi_proportional_heuristic", _heuristic_without_coefficients, "ccr_name_hhi_coeff"),
    ("irrbb_interim_delta_eve", _irrbb_without_shock_set, "icaap_irrbb_interim_scenarios"),
    ("fx_nop_addon/shocks", _fx_without_shocks, "fx_p2_shock_pct"),
    ("fx_nop_addon/car_min", _fx_without_car_min, "car_min"),
    (
        "operational_scenario_net_p1",
        _operational_without_severities,
        "op_p2_scenario_severity_pct_gross_income",
    ),
    ("sovereign_stress_addon", _sovereign_without_grid, "sov_p2_haircut_pct"),
    (
        "judgemental/diversification",
        _judgemental_without_the_switch,
        "icaap_diversification_benefit_allowed",
    ),
]


@pytest.mark.parametrize(("name", "call", "expected_code"), CASES, ids=[case[0] for case in CASES])
def test_a_method_refuses_by_code_rather_than_defaulting(
    name: str, call: Callable[[], object], expected_code: str
) -> None:
    with pytest.raises(MissingParameter) as error:
        call()
    assert error.value.param_code == expected_code, name
