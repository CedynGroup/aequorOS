"""Concentration methods: the benchmark reference case, the envelope, the label."""

from __future__ import annotations

import dataclasses
from decimal import Decimal

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from app.domain.icaap.pillar2.bands import BandTable, parse_band_table
from app.domain.icaap.pillar2.concentration import (
    DIMENSION_SECTOR,
    DIMENSION_SINGLE_NAME,
    HEURISTIC_LABEL,
    METRIC_CRN,
    METRIC_HHI,
    DimensionVector,
    aggregate_dimension,
    benchmark_mapped,
    dimension_metrics,
    hhi_proportional_heuristic,
)
from app.domain.icaap.pillar2.types import MethodStatus, MissingParameter
from app.domain.icaap.units import Basis, Denominators
from app.domain.stress.concentration import ConcentrationExposure
from tests.domain.icaap.pillar2.conftest import (
    CR_N,
    METRIC_SET,
    MIN_COVERAGE_PCT,
    NAME_CRN_BODY,
    NAME_HHI_BODY,
    SECTOR_HHI_BODY,
)

NAMES = DimensionVector(DIMENSION_SINGLE_NAME, tuple([Decimal(2)] * 50), Decimal(0))
SECTORS = DimensionVector(DIMENSION_SECTOR, (Decimal(45), Decimal(45)), Decimal(10))


def mapped(
    tables: dict[tuple[str, str], BandTable],
    baseline: Denominators,
    stressed: Denominators | None = None,
    *,
    names: DimensionVector = NAMES,
    sectors: DimensionVector = SECTORS,
):
    return benchmark_mapped(
        name_vector=names,
        sector_vector=sectors,
        metric_set=METRIC_SET,
        cr_n=CR_N,
        tables=tables,
        min_coverage_pct=MIN_COVERAGE_PCT,
        baseline=baseline,
        stressed=stressed,
    )


def test_the_reference_case_charges_the_designed_amounts(
    tables: dict[tuple[str, str], BandTable],
    baseline: Denominators,
    stressed: Denominators,
) -> None:
    result = mapped(tables, baseline, stressed)
    assert result.status is MethodStatus.COMPUTED
    assert result.basis is Basis.PCT_PILLAR1_CREDIT_CAPITAL
    assert result.basis_value == Decimal(18)
    assert result.baseline_amount == Decimal("18.72")
    assert result.stressed_amount == Decimal("21.06")
    assert result.stressed_derivation == "method"


def test_a_dimension_takes_its_worst_metric_and_the_dimensions_are_summed(
    tables: dict[tuple[str, str], BandTable], baseline: Denominators
) -> None:
    """Name HHI earns 5 and CR20 earns 6, so the name charge is 6, plus 12."""
    result = mapped(tables, baseline)
    assert result.detail["single_name_addon_pct"] == "6"
    assert result.detail["single_name_deciding_metric"] == METRIC_CRN
    assert result.detail["sector_addon_pct"] == "12"
    assert result.basis_value == Decimal(18)


def test_the_envelope_takes_whichever_reading_is_worse(
    tables: dict[tuple[str, str], BandTable], baseline: Denominators
) -> None:
    """D-035: one-bucket is usually conservative, and sometimes it is not."""
    e1_stated = tuple([Decimal("1.8")] * 50)
    e1 = DimensionVector(DIMENSION_SINGLE_NAME, e1_stated, Decimal(10))
    values = {
        value.metric: value for value in dimension_metrics(e1, metrics=[METRIC_HHI], cr_n=None)
    }
    assert values[METRIC_HHI].stated_only == Decimal("0.02")
    assert values[METRIC_HHI].with_unstated == Decimal("0.0262")
    assert values[METRIC_HHI].value == Decimal("0.0262")

    e2 = DimensionVector(DIMENSION_SECTOR, (Decimal(30), Decimal(30)), Decimal(40))
    counter = dimension_metrics(e2, metrics=[METRIC_HHI], cr_n=None)[0]
    assert counter.with_unstated == Decimal("0.34")
    assert counter.stated_only == Decimal("0.50")
    assert counter.value == Decimal("0.50")


def test_a_dimension_below_the_coverage_floor_takes_the_top_band(
    tables: dict[tuple[str, str], BandTable], baseline: Denominators
) -> None:
    """A measurement of half the book is not a measurement of the book."""
    thin = DimensionVector(DIMENSION_SECTOR, (Decimal(30), Decimal(30)), Decimal(40))
    result = mapped(tables, baseline, sectors=thin)
    assert result.detail["sector_coverage_pct"] == "60.000000"
    assert result.detail["sector_coverage_floor_applied"] == "true"
    assert result.detail["sector_addon_pct"] == "12"
    assert "coverage_floor_applied:sector" in result.reasons


def test_without_stressed_denominators_the_stressed_column_is_not_assessed(
    tables: dict[tuple[str, str], BandTable], baseline: Denominators
) -> None:
    result = mapped(tables, baseline)
    assert result.stressed_amount is None
    assert result.stressed_derivation == "not_assessed"
    assert "stressed_denominators_missing" in result.reasons


def test_an_absent_dimension_contributes_nothing_and_says_so(
    tables: dict[tuple[str, str], BandTable], baseline: Denominators
) -> None:
    empty = DimensionVector(DIMENSION_SECTOR, (), Decimal(0))
    result = mapped(tables, baseline, sectors=empty)
    assert "dimension_absent:sector" in result.reasons
    assert result.basis_value == Decimal(6)


def test_a_band_table_that_was_not_resolved_is_a_typed_refusal(
    tables: dict[tuple[str, str], BandTable], baseline: Denominators
) -> None:
    del tables[(DIMENSION_SECTOR, METRIC_HHI)]
    with pytest.raises(MissingParameter) as error:
        mapped(tables, baseline)
    assert error.value.param_code == "ccr_sector_bands_hhi"


def test_the_heuristic_is_never_called_a_granularity_adjustment(
    baseline: Denominators, stressed: Denominators
) -> None:
    result = hhi_proportional_heuristic(
        name_vector=NAMES,
        sector_vector=SECTORS,
        name_coeff=Decimal("0.5"),
        sector_coeff=Decimal("0.5"),
        baseline=baseline,
        stressed=stressed,
    )
    assert result.basis is Basis.PCT_CREDIT_RWA
    assert result.baseline_amount == Decimal(208)
    assert result.stressed_amount == Decimal(234)
    assert result.detail["label"] == HEURISTIC_LABEL
    assert "granularity" in HEURISTIC_LABEL
    assert "not a granularity adjustment" in HEURISTIC_LABEL


def test_the_heuristic_dwarfs_the_benchmark_on_the_same_book(
    tables: dict[tuple[str, str], BandTable], baseline: Denominators
) -> None:
    """Why the label matters: the same book, two calibrations, ten times apart."""
    benchmark = mapped(tables, baseline)
    heuristic = hhi_proportional_heuristic(
        name_vector=NAMES,
        sector_vector=SECTORS,
        name_coeff=Decimal("0.5"),
        sector_coeff=Decimal("0.5"),
        baseline=baseline,
    )
    assert benchmark.baseline_amount is not None and heuristic.baseline_amount is not None
    assert heuristic.baseline_amount > benchmark.baseline_amount


def test_aggregation_keeps_the_unstated_remainder_and_ignores_expected_loss() -> None:
    """fn 2: the input type carries EAD only, so provisions cannot move it."""
    fields = {field.name for field in dataclasses.fields(ConcentrationExposure)}
    assert not {name for name in fields if "provision" in name or "ecl" in name}

    exposures = [
        ConcentrationExposure("e1", Decimal(60), "group-a", sector="agriculture"),
        ConcentrationExposure("e2", Decimal(30), "group-a", sector=None),
        ConcentrationExposure("e3", Decimal(10), "group-b", sector="mining"),
        ConcentrationExposure("e4", Decimal(0), "group-c", sector="mining"),
    ]
    names = aggregate_dimension(exposures, DIMENSION_SINGLE_NAME)
    assert sorted(names.stated) == [Decimal(10), Decimal(90)]
    assert names.unstated == Decimal(0)
    sectors = aggregate_dimension(exposures, DIMENSION_SECTOR)
    assert sorted(sectors.stated) == [Decimal(10), Decimal(60)]
    assert sectors.unstated == Decimal(30)
    assert sectors.coverage_pct == Decimal(70)


def test_an_unknown_dimension_is_a_programming_error_not_a_zero() -> None:
    with pytest.raises(ValueError, match="unknown_concentration_dimension"):
        aggregate_dimension([], "astrology")


@settings(max_examples=200, deadline=None)
@given(
    sizes=st.lists(st.integers(min_value=1, max_value=1000), min_size=2, max_size=25),
    delta=st.integers(min_value=1, max_value=100),
)
def test_a_more_concentrated_book_never_charges_less(sizes: list[int], delta: int) -> None:
    tables = {
        (DIMENSION_SINGLE_NAME, METRIC_HHI): parse_band_table(
            NAME_HHI_BODY, param_code="ccr_name_bands_hhi"
        ),
        (DIMENSION_SINGLE_NAME, METRIC_CRN): parse_band_table(
            NAME_CRN_BODY, param_code="ccr_name_bands_crn"
        ),
        (DIMENSION_SECTOR, METRIC_HHI): parse_band_table(
            SECTOR_HHI_BODY, param_code="ccr_sector_bands_hhi"
        ),
    }
    baseline = Denominators(
        total_rwa=Decimal(1000), credit_rwa=Decimal(800), car_min_pct=Decimal(13)
    )
    before = sorted(Decimal(size) for size in sizes)
    if before[0] <= Decimal(delta):
        return
    after = list(before)
    after[0] -= Decimal(delta)
    after[-1] += Decimal(delta)
    charge_before = mapped(
        tables,
        baseline,
        names=DimensionVector(DIMENSION_SINGLE_NAME, tuple(before), Decimal(0)),
        sectors=DimensionVector(DIMENSION_SECTOR, (Decimal(1),), Decimal(0)),
    )
    charge_after = mapped(
        tables,
        baseline,
        names=DimensionVector(DIMENSION_SINGLE_NAME, tuple(after), Decimal(0)),
        sectors=DimensionVector(DIMENSION_SECTOR, (Decimal(1),), Decimal(0)),
    )
    assert charge_before.baseline_amount is not None
    assert charge_after.baseline_amount is not None
    assert charge_after.baseline_amount >= charge_before.baseline_amount
