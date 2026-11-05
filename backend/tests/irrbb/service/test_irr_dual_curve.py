"""Dual-curve discounting: the published AGD wires into the IRR engines.

Curve platform spec §6/§13 Stage 2: discounting is a SEPARATE selection from
projection. When the desk publishes a discounting curve for the bank's
currency (``AEQ.GHS.OIS`` — the Aequor Ghana Discounting Curve), every
EVE/duration present value discounts on it while the parameter-table base
curve keeps its projection role (floating-leg repricing, EaR gap arithmetic).
Absent a published discount curve, behavior is byte-identical to the
single-curve engine — that graceful fallback is what keeps the hermetic
golden suite green, because the sample-bank seed publishes no desk curves.

Hand-derived engine arithmetic (one asset, midpoint 1.9y, face 1,000,000):

- single-curve: projection zero 20%  → PV = 1,000,000 / 1.20^1.9 = 707,221.7890
- dual-curve:   discount   zero 24%  → PV = 1,000,000 / 1.24^1.9 = 664,505.8340
  (the discount rate sits ABOVE the projection rate, so the PV — and an
  asset-only book's EVE — is strictly LOWER under the discount curve)
- the SAME +200 bp shift map shocks the discounting curve:
  PV = 1,000,000 / 1.26^1.9 = 644,608.4055
- modified duration divides by (1 + y) of the DISCOUNTING curve:
  1.9 / 1.24 = 1.5323 (vs 1.9 / 1.20 = 1.5833 single-curve)

This module's other-suite cases live in ``test_irr_dual_curve_integration.py``.
"""

from __future__ import annotations

from decimal import Decimal

from app.irrbb.domain.engine import (
    IRR_BUCKETS,
    IrrPosition,
    compute_duration,
    compute_eve,
)
from app.irrbb.service.regulatory_irr import discount_curve_midpoints_pct

# One-position book for the hand-derived engine arithmetic in the module
# docstring: an asset repricing at the 1-3y midpoint.
ASSET = IrrPosition(
    side="asset",
    bucket="1-3y",
    amount=Decimal("1000000"),
    rate_pct=Decimal("20"),
    fixed_or_float="fixed",
    midpoint_years=Decimal("1.9"),
    source="unit",
)
PROJECTION = {Decimal("1.9"): Decimal("20")}
DISCOUNT = {Decimal("1.9"): Decimal("24")}
# AGD published nodes for the official-run tests: (tenor_months → decimal
# fraction), deliberately ABOVE the seed's 25.5–29.5% parameter base curve.
AGD_RATES: dict[int, str] = {
    1: "0.29",
    3: "0.295",
    6: "0.30",
    12: "0.305",
    24: "0.31",
    48: "0.315",
    84: "0.32",
}
# Hand-derived nine-midpoint conversion of AGD_RATES (linear on zero rates,
# flat past both ends, percent, 6 dp):
# - 0.003 / 0.014 / 0.06 y sit below the 1m node (1/12 y)          → flat 29
# - 0.17 y between 1m and 3m: 29 + 0.5·(0.17−1/12)/(0.25−1/12)     → 29.26
# - 0.38 y between 3m and 6m: 29.5 + 0.5·(0.38−0.25)/0.25          → 29.76
# - 0.75 y between 6m and 1y: 30 + 0.5·(0.75−0.5)/0.5              → 30.25
# - 1.9  y between 1y and 2y: 30.5 + 0.5·(1.9−1.0)/1.0             → 30.95
# - 4.0  y is the 48m node                                          → 31.50
# - 7.0  y is the 84m node                                          → 32.00
EXPECTED_AGD_MIDPOINTS_PCT: dict[str, str] = {
    "0.003": "29.000000",
    "0.014": "29.000000",
    "0.06": "29.000000",
    "0.17": "29.260000",
    "0.38": "29.760000",
    "0.75": "30.250000",
    "1.9": "30.950000",
    "4.0": "31.500000",
    "7.0": "32.000000",
}


def test_compute_eve_discounts_on_the_published_curve() -> None:
    single = compute_eve([ASSET], PROJECTION, {})
    assert single == Decimal("707221.7890")  # 1,000,000 / 1.20^1.9

    dual = compute_eve([ASSET], PROJECTION, {}, discount_curve=DISCOUNT)
    assert dual == Decimal("664505.8340")  # 1,000,000 / 1.24^1.9
    # Discount rate above the projection rate ⇒ strictly lower asset PV.
    assert dual < single


def test_the_same_shift_map_shocks_the_discounting_curve() -> None:
    # +200 bp (decimal 0.02) keyed by the shared 1.9y midpoint moves the
    # DISCOUNT rate from 24% to 26%: 1,000,000 / 1.26^1.9.
    shifted = compute_eve(
        [ASSET], PROJECTION, {Decimal("1.9"): Decimal("0.02")}, discount_curve=DISCOUNT
    )
    assert shifted == Decimal("644608.4055")


def test_absent_or_identical_discount_curve_is_byte_identical() -> None:
    single = compute_eve([ASSET], PROJECTION, {})
    assert compute_eve([ASSET], PROJECTION, {}, discount_curve=None) == single
    assert compute_eve([ASSET], PROJECTION, {}, discount_curve=dict(PROJECTION)) == single


def test_compute_duration_discounts_on_the_published_curve() -> None:
    dual = compute_duration([ASSET], PROJECTION, discount_curve=DISCOUNT)
    assert dual.pv_assets == Decimal("664505.8340")
    assert dual.asset_macaulay == Decimal("1.9000")
    assert dual.asset_modified == Decimal("1.5323")  # 1.9 / 1.24

    single = compute_duration([ASSET], PROJECTION)
    assert single.pv_assets == Decimal("707221.7890")
    assert single.asset_modified == Decimal("1.5833")  # 1.9 / 1.20


def test_midpoint_conversion_reproduces_node_values_exactly() -> None:
    # 48m and 84m sit exactly on the 4.0y and 7.0y bucket midpoints.
    curve = discount_curve_midpoints_pct(((48, Decimal("0.289")), (84, Decimal("0.295"))))
    assert curve[Decimal("4.0")] == Decimal("28.900000")
    assert curve[Decimal("7.0")] == Decimal("29.500000")
    # Flat extrapolation below the first node.
    assert curve[Decimal("0.003")] == Decimal("28.900000")


def test_midpoint_conversion_interpolates_linearly_between_nodes() -> None:
    curve = discount_curve_midpoints_pct(((6, Decimal("0.20")), (18, Decimal("0.30"))))
    # 0.75y between 0.5y and 1.5y: 20 + 10 · (0.75 − 0.5)/(1.5 − 0.5) = 22.5.
    assert curve[Decimal("0.75")] == Decimal("22.500000")
    assert curve[Decimal("0.38")] == Decimal("20.000000")  # flat below 0.5y
    assert curve[Decimal("1.9")] == Decimal("30.000000")  # flat above 1.5y
    # Every one of the nine canonical midpoints is keyed — exact-lookup safe.
    assert set(curve) == {midpoint for _, midpoint in IRR_BUCKETS}


def test_midpoint_conversion_of_the_agd_fixture_matches_hand_derivation() -> None:
    points = tuple((months, Decimal(rate)) for months, rate in sorted(AGD_RATES.items()))
    curve = discount_curve_midpoints_pct(points)
    assert {str(midpoint): str(rate) for midpoint, rate in curve.items()} == (
        EXPECTED_AGD_MIDPOINTS_PCT
    )
