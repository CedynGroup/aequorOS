"""A band table is refused unless it can answer for every possible metric."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from app.domain.icaap.pillar2.bands import BandTableError, lookup, parse_band_table
from app.domain.icaap.units import Basis
from tests.domain.icaap.pillar2.conftest import CREDIT_CAPITAL_BASIS, NAME_HHI_BODY

CODE = "ccr_name_bands_hhi"


def body(bands: list[dict[str, Any]], mode: str = "step") -> dict[str, Any]:
    return {"mode": mode, "basis": CREDIT_CAPITAL_BASIS, "bands": bands}


def test_the_seed_table_parses_with_its_basis_and_top_band() -> None:
    table = parse_band_table(NAME_HHI_BODY, param_code=CODE)
    assert table.basis is Basis.PCT_PILLAR1_CREDIT_CAPITAL
    assert table.mode == "step"
    assert table.top_addon == Decimal(15)
    assert table.param_code == CODE


def test_lower_is_inclusive_and_upper_exclusive() -> None:
    table = parse_band_table(NAME_HHI_BODY, param_code=CODE)
    assert lookup(table, Decimal("0.02")) == Decimal(5)
    assert lookup(table, Decimal("0.0199999")) == Decimal(2)
    assert lookup(table, Decimal("0.05")) == Decimal(10)
    assert lookup(table, Decimal(1)) == Decimal(15)


def test_linear_mode_interpolates_across_a_band_and_holds_the_last_one_flat() -> None:
    table = parse_band_table(
        body(
            [
                {"lower": 0, "upper": 0.02, "addon": 0},
                {"lower": 0.02, "upper": 0.05, "addon": 5},
                {"lower": 0.05, "upper": None, "addon": 10},
            ],
            mode="linear",
        ),
        param_code=CODE,
    )
    assert lookup(table, Decimal("0.035")) == Decimal("7.5")
    assert lookup(table, Decimal("0.02")) == Decimal(5)
    assert lookup(table, Decimal(1)) == Decimal(10)


@pytest.mark.parametrize(
    ("bands", "code"),
    [
        ([], "bands_empty"),
        ([{"lower": 0.01, "upper": None, "addon": 0}], "bands_not_starting_at_zero"),
        ([{"lower": 0, "upper": 0.02, "addon": 0}], "last_band_not_unbounded"),
        (
            [
                {"lower": 0, "upper": 0.02, "addon": 0},
                {"lower": 0.03, "upper": None, "addon": 5},
            ],
            "bands_not_contiguous",
        ),
        (
            [
                {"lower": 0, "upper": 0.02, "addon": 0},
                {"lower": 0.01, "upper": None, "addon": 5},
            ],
            "bands_not_contiguous",
        ),
        (
            [
                {"lower": 0, "upper": None, "addon": 0},
                {"lower": 0.02, "upper": None, "addon": 5},
            ],
            "unbounded_band_not_last",
        ),
        (
            [
                {"lower": 0, "upper": 0.02, "addon": 5},
                {"lower": 0.02, "upper": None, "addon": 1},
            ],
            "addon_decreasing",
        ),
        ([{"lower": 0, "upper": None, "addon": -1}], "addon_negative"),
        (
            [{"lower": 0, "upper": 0, "addon": 0}, {"lower": 0, "upper": None, "addon": 1}],
            "band_not_ascending",
        ),
    ],
)
def test_a_table_that_cannot_answer_is_refused(bands: list[dict[str, Any]], code: str) -> None:
    with pytest.raises(BandTableError) as error:
        parse_band_table(body(bands), param_code=CODE)
    assert error.value.code == code
    assert error.value.param_code == CODE


@pytest.mark.parametrize(
    ("payload", "code"),
    [
        (None, "band_table_malformed"),
        ({"mode": "sigmoid", "basis": CREDIT_CAPITAL_BASIS, "bands": []}, "mode_unknown"),
        ({"mode": "step", "basis": "pct_of_vibes", "bands": []}, "basis_unknown"),
        (
            {"mode": "step", "basis": CREDIT_CAPITAL_BASIS, "bands": [{"lower": 0}]},
            "band_table_malformed",
        ),
    ],
)
def test_a_malformed_body_is_refused(payload: dict[str, Any] | None, code: str) -> None:
    with pytest.raises(BandTableError) as error:
        parse_band_table(payload, param_code=CODE)
    assert error.value.code == code


def test_a_metric_below_the_table_is_out_of_range() -> None:
    table = parse_band_table(NAME_HHI_BODY, param_code=CODE)
    with pytest.raises(BandTableError) as error:
        lookup(table, Decimal(-1))
    assert error.value.code == "metric_out_of_range"


@settings(max_examples=300, deadline=None)
@given(
    first=st.decimals(min_value=Decimal(0), max_value=Decimal(1), places=6),
    second=st.decimals(min_value=Decimal(0), max_value=Decimal(1), places=6),
    mode=st.sampled_from(["step", "linear"]),
)
def test_the_add_on_never_falls_as_the_metric_rises(
    first: Decimal, second: Decimal, mode: str
) -> None:
    table = parse_band_table(
        body(
            [
                {"lower": 0, "upper": 0.01, "addon": 0},
                {"lower": 0.01, "upper": 0.02, "addon": 2},
                {"lower": 0.02, "upper": 0.05, "addon": 5},
                {"lower": 0.05, "upper": None, "addon": 15},
            ],
            mode=mode,
        ),
        param_code=CODE,
    )
    low, high = sorted((first, second))
    assert lookup(table, low) <= lookup(table, high)
