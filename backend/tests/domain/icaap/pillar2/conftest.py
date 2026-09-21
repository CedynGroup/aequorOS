"""Governed parameter BODIES as the seed catalogue holds them.

These are fixtures, not defaults: the domain refuses without them, and every
figure a test asserts is a figure these bodies produced. Changing a band here
must change a test expectation, which is the point — the calibration lives in
the console, and the tests prove the console is what moves the number.

The two bodies whose parser reads the body itself are TAKEN from the seed
catalogue rather than restated here (``seed_body``). A hand-written copy is a
fixture that tests itself: the sovereign grid's copy carried both a different
shape and different tenor keys from the shipped row, so the whole sovereign
suite passed while the shipped row could not be parsed at all (audit W2).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from app.domain.icaap.pillar2.bands import BandTable, parse_band_table
from app.domain.icaap.pillar2.concentration import (
    DIMENSION_SECTOR,
    DIMENSION_SINGLE_NAME,
    METRIC_CRN,
    METRIC_HHI,
)
from app.domain.icaap.units import Basis, Denominators
from app.services.regulatory_parameters import ICAAP_P2_SEED_PARAMETERS


def seed_body(param_code: str) -> dict[str, Any]:
    """The shipped ``value_json`` for one governed Pillar 2 code.

    A copy, so a test that mutates it cannot reach the catalogue; the KEYS and
    the VALUES are the console's, which is the point.
    """
    for spec in ICAAP_P2_SEED_PARAMETERS:
        if spec.param_code == param_code and spec.value_json is not None:
            return dict(spec.value_json)
    raise AssertionError(f"{param_code} carries no seeded body")


CREDIT_CAPITAL_BASIS = Basis.PCT_PILLAR1_CREDIT_CAPITAL.value

NAME_HHI_BODY: dict[str, Any] = {
    "mode": "step",
    "basis": CREDIT_CAPITAL_BASIS,
    "bands": [
        {"lower": 0, "upper": 0.01, "addon": 0},
        {"lower": 0.01, "upper": 0.02, "addon": 2},
        {"lower": 0.02, "upper": 0.05, "addon": 5},
        {"lower": 0.05, "upper": 0.10, "addon": 10},
        {"lower": 0.10, "upper": None, "addon": 15},
    ],
}
NAME_CRN_BODY: dict[str, Any] = {
    "mode": "step",
    "basis": CREDIT_CAPITAL_BASIS,
    "bands": [
        {"lower": 0, "upper": 0.20, "addon": 0},
        {"lower": 0.20, "upper": 0.35, "addon": 3},
        {"lower": 0.35, "upper": 0.50, "addon": 6},
        {"lower": 0.50, "upper": None, "addon": 10},
    ],
}
SECTOR_HHI_BODY: dict[str, Any] = {
    "mode": "step",
    "basis": CREDIT_CAPITAL_BASIS,
    "taxonomy": "canonical_sector_attribute",
    "bands": [
        {"lower": 0, "upper": 0.15, "addon": 0},
        {"lower": 0.15, "upper": 0.25, "addon": 3},
        {"lower": 0.25, "upper": 0.40, "addon": 7},
        {"lower": 0.40, "upper": None, "addon": 12},
    ],
}

FX_SHOCK_BODY: dict[str, Any] = seed_body("fx_p2_shock_pct")
SOVEREIGN_GRID_BODY: dict[str, Any] = seed_body("sov_p2_haircut_pct")
OPERATIONAL_SEVERITIES: dict[str, Decimal] = {
    "cloud": Decimal(4),
    "cyber": Decimal(12),
}

METRIC_SET: dict[str, list[str]] = {
    DIMENSION_SINGLE_NAME: [METRIC_HHI, METRIC_CRN],
    DIMENSION_SECTOR: [METRIC_HHI],
}
CR_N = 20
MIN_COVERAGE_PCT = Decimal(80)
CAR_MIN_PCT = Decimal(13)
OUTLIER_THRESHOLD_PCT = Decimal(15)


@pytest.fixture
def tables() -> dict[tuple[str, str], BandTable]:
    return {
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


@pytest.fixture
def baseline() -> Denominators:
    return Denominators(total_rwa=Decimal(1000), credit_rwa=Decimal(800), car_min_pct=CAR_MIN_PCT)


@pytest.fixture
def stressed() -> Denominators:
    return Denominators(total_rwa=Decimal(1100), credit_rwa=Decimal(900), car_min_pct=CAR_MIN_PCT)
