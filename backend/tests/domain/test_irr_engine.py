"""Hand-verified golden tests for the pure IRRBB engine.

The fixtures mirror the Sample Bank Ltd seed's latest reporting period (2026-03,
factor 1.0, canonical amounts), the Bank of Ghana base discount curve, and the
six Basel IRRBB stress scenarios. Gap and EaR expectations are pure arithmetic
derived by hand; EVE and duration expectations are re-derived inside the test
with an independent present-value implementation so the goldens are never a
straight echo of the engine's own output.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

import pytest

from app.domain.irr.engine import (
    IrrPosition as P,
)
from app.domain.irr.engine import (
    MissingParameterError,
    UnsupportedShockError,
    compute_duration,
    compute_ear,
    compute_eve,
    compute_gap,
    compute_nii,
    run_irr_scenarios,
)

M = Decimal("1000000")
MONEY = Decimal("0.0001")
RATIO = Decimal("0.000001")
DUR = Decimal("0.0001")

# (category, bucket, millions, rate, fixed_or_float, midpoint, source)
_ASSETS = (
    ("interbank_placements", "overnight", "70", "26.0", "float", "0.003", "interbank"),
    ("tbills_short", "1-7d", "60", "25.4", "fixed", "0.014", "securities"),
    ("tbills_1m", "8-30d", "90", "25.6", "fixed", "0.06", "securities"),
    ("tbills_3m", "1-3m", "110", "25.4", "fixed", "0.17", "securities"),
    ("corp_loans_float_1", "1-3m", "180", "29.5", "float", "0.17", "loans"),
    ("gog_bonds_short", "3-6m", "120", "26.1", "fixed", "0.38", "securities"),
    ("sme_loans_1", "3-6m", "150", "31.0", "float", "0.38", "loans"),
    ("gog_bonds_1y", "6-12m", "90", "27.0", "fixed", "0.75", "securities"),
    ("corp_loans_float_2", "6-12m", "200", "29.5", "float", "0.75", "loans"),
    ("gog_bonds_2y", "1-3y", "150", "27.8", "fixed", "1.9", "securities"),
    ("corp_loans_fixed", "1-3y", "240", "24.5", "fixed", "1.9", "loans"),
    ("gog_bonds_5y", "3-5y", "180", "28.9", "fixed", "4.0", "loans"),
    ("mortgages", "3-5y", "200", "28.2", "fixed", "4.0", "loans"),
    ("gog_bonds_long", "5y+", "100", "29.5", "fixed", "7.0", "loans"),
    ("corp_loans_long", "5y+", "100", "24.5", "fixed", "7.0", "loans"),
)
_LIABS = (
    ("call_deposits", "overnight", "240", "7.0", "float", "0.003", "deposits"),
    ("wholesale_sme", "8-30d", "200", "21.5", "fixed", "0.06", "deposits"),
    ("term_deposits_3m", "1-3m", "280", "21.5", "fixed", "0.17", "deposits"),
    ("wholesale_corp", "1-3m", "320", "23.0", "fixed", "0.17", "deposits"),
    ("savings_repricing", "3-6m", "300", "8.5", "float", "0.38", "deposits"),
    ("term_deposits_1y", "6-12m", "220", "23.4", "fixed", "0.75", "deposits"),
    ("term_borrowings", "1-3y", "100", "25.5", "fixed", "1.9", "deposits"),
    ("subordinated_debt", "5y+", "45", "26.0", "fixed", "7.0", "capital"),
)

BASE_CURVE = {
    Decimal("0.003"): Decimal("25.5"),
    Decimal("0.014"): Decimal("25.4"),
    Decimal("0.06"): Decimal("25.6"),
    Decimal("0.17"): Decimal("25.8"),
    Decimal("0.38"): Decimal("26.2"),
    Decimal("0.75"): Decimal("27.0"),
    Decimal("1.9"): Decimal("27.8"),
    Decimal("4.0"): Decimal("28.9"),
    Decimal("7.0"): Decimal("29.5"),
}
SCENARIO_SHOCKS = {
    "parallel_up_200": {"parallel_bp": Decimal("200")},
    "parallel_down_200": {"parallel_bp": Decimal("-200")},
    "short_up_250": {"short_bp": Decimal("250"), "decay_years": Decimal("3")},
    "short_down_250": {"short_bp": Decimal("-250"), "decay_years": Decimal("3")},
    "steepener": {"short_bp": Decimal("-65"), "long_bp": Decimal("90")},
    "flattener": {"short_bp": Decimal("80"), "long_bp": Decimal("-60")},
}
TIER1 = Decimal("280") * M
EVE_LIMIT = Decimal("15")


def _positions() -> list[P]:
    positions: list[P] = []
    for _cat, bucket, millions, rate, ff, mid, source in _ASSETS:
        positions.append(
            P("asset", bucket, Decimal(millions) * M, Decimal(rate), ff, Decimal(mid), source)
        )
    for _cat, bucket, millions, rate, ff, mid, source in _LIABS:
        positions.append(
            P("liability", bucket, Decimal(millions) * M, Decimal(rate), ff, Decimal(mid), source)
        )
    # Pay-fixed swap: floating receive leg (asset, 1-3m) + fixed pay leg (liability, 1-3y).
    positions.append(
        P(
            "asset",
            "1-3m",
            Decimal("120") * M,
            Decimal("25.8"),
            "float",
            Decimal("0.17"),
            "swap",
            True,
        )
    )
    positions.append(
        P(
            "liability",
            "1-3y",
            Decimal("120") * M,
            Decimal("25.3"),
            "fixed",
            Decimal("1.9"),
            "swap",
            True,
        )
    )
    return positions


def _pv(amount: Decimal, midpoint: Decimal, rate_pct: Decimal) -> Decimal:
    """Independent re-implementation of the engine's single-cash-flow PV."""
    return (amount / (Decimal("1") + rate_pct / 100) ** midpoint).quantize(
        MONEY, rounding=ROUND_HALF_UP
    )


def _independent_eve(positions: list[P], shift_pct_by_mid: dict[Decimal, Decimal]) -> Decimal:
    pv_a = Decimal("0")
    pv_l = Decimal("0")
    for position in positions:
        rate = BASE_CURVE[position.midpoint_years] + shift_pct_by_mid.get(
            position.midpoint_years, Decimal("0")
        )
        pv = _pv(position.amount, position.midpoint_years, rate)
        if position.side == "asset":
            pv_a += pv
        else:
            pv_l += pv
    return (pv_a - pv_l).quantize(MONEY, rounding=ROUND_HALF_UP)


def test_gap_golden() -> None:
    gap = compute_gap(_positions())
    by_bucket = {bucket.bucket: bucket for bucket in gap.buckets}
    # RSA/RSL by bucket (swap adds +120 asset to 1-3m and +120 liability to 1-3y).
    assert by_bucket["overnight"].rsa == Decimal("70") * M
    assert by_bucket["overnight"].rsl == Decimal("240") * M
    assert by_bucket["overnight"].gap == Decimal("-170") * M
    assert by_bucket["1-3m"].rsa == Decimal("410") * M
    assert by_bucket["1-3m"].rsl == Decimal("600") * M
    assert by_bucket["1-3m"].gap == Decimal("-190") * M
    assert by_bucket["1-3y"].rsa == Decimal("390") * M
    assert by_bucket["1-3y"].rsl == Decimal("220") * M
    assert by_bucket["6-12m"].gap == Decimal("70") * M
    assert by_bucket["5y+"].gap == Decimal("155") * M

    assert gap.rsa_total == Decimal("2160") * M
    assert gap.rsl_total == Decimal("1825") * M
    assert gap.gap_total == Decimal("335") * M

    # Cumulative 12m gap = -170 + 60 - 110 - 190 - 30 + 70 = -370M.
    assert gap.cumulative_12m_gap == Decimal("-370") * M
    assert by_bucket["6-12m"].cumulative_gap == Decimal("-370") * M
    assert [item.line_code for item in gap.line_items] == [
        "overnight",
        "1-7d",
        "8-30d",
        "1-3m",
        "3-6m",
        "6-12m",
        "1-3y",
        "3-5y",
        "5y+",
    ]
    assert all(item.section == "irr_gap" for item in gap.line_items)
    short_end = [bucket.bucket for bucket in gap.buckets if bucket.within_12m]
    assert short_end == ["overnight", "1-7d", "8-30d", "1-3m", "3-6m", "6-12m"]


def test_ear_golden() -> None:
    gap = compute_gap(_positions())
    # ΔNII up = Σ gap_i * 0.02 * (12 - midpoint*12)/12 over the six ≤12m buckets:
    #   -170*0.02*0.997 + 60*0.02*0.986 - 110*0.02*0.94 - 190*0.02*0.83
    #   - 30*0.02*0.62 + 70*0.02*0.25 = -7.4506M.
    ear_up = compute_ear(gap, Decimal("200"))
    ear_down = compute_ear(gap, Decimal("-200"))
    assert ear_up == Decimal("-7450600")
    assert ear_down == Decimal("7450600")
    assert ear_up == -ear_down


def test_base_nii_golden() -> None:
    # Σ asset(amount*rate) - Σ liability(amount*rate) over the whole book,
    # decomposed swap legs included. Hand-derived:
    #   on-balance-sheet book: assets 561.56M - liabilities 307.78M = 253.78M;
    #   swap carry = 120M × (25.8 floating index - 25.3 pay fixed)/100 = +0.6M
    #   (the floating index is the base-curve zero at the 0.17y receive-leg
    #   midpoint, the 91d T-bill reset point).
    # Total = 253,780,000 + 600,000 = 254,380,000.
    nii = compute_nii(_positions())
    assert nii == Decimal("254380000")


def test_base_nii_includes_exactly_the_swap_carry() -> None:
    # The same book with and without the swap legs must differ by exactly the
    # hand-computed carry: 120,000,000 × (25.8 - 25.3)/100 = 600,000.
    with_swap = compute_nii(_positions())
    without_swap = compute_nii([p for p in _positions() if not p.is_hedge])
    assert without_swap == Decimal("253780000")
    assert with_swap - without_swap == Decimal("600000")


def test_receive_fixed_legs_flip_the_carry_sign() -> None:
    # Mirror-image decomposition of the same swap: the fixed leg becomes the
    # asset (receive side) and the floating leg the liability (pay side), so
    # carry = 120M × (25.3 fixed - 25.8 floating)/100 = -600,000.
    receive_fixed_legs = [
        P(
            "asset",
            "1-3y",
            Decimal("120") * M,
            Decimal("25.3"),
            "fixed",
            Decimal("1.9"),
            "swap",
            True,
        ),
        P(
            "liability",
            "1-3m",
            Decimal("120") * M,
            Decimal("25.8"),
            "float",
            Decimal("0.17"),
            "swap",
            True,
        ),
    ]
    assert compute_nii(receive_fixed_legs) == Decimal("-600000")
    # Gap contributions flip buckets and sides versus the pay-fixed legs.
    gap = compute_gap(receive_fixed_legs)
    by_bucket = {bucket.bucket: bucket for bucket in gap.buckets}
    assert by_bucket["1-3y"].rsa == Decimal("120") * M
    assert by_bucket["1-3y"].rsl == Decimal("0")
    assert by_bucket["1-3m"].rsl == Decimal("120") * M
    assert by_bucket["1-3m"].rsa == Decimal("0")


def test_base_eve_reduced_hand_check() -> None:
    # A reduced two-position book validates the PV formula against explicit
    # first-principles arithmetic (independent of the engine's internals).
    reduced = [
        P(
            "asset",
            "overnight",
            Decimal("70") * M,
            Decimal("26.0"),
            "float",
            Decimal("0.003"),
            "interbank",
        ),
        P(
            "liability",
            "5y+",
            Decimal("45") * M,
            Decimal("26.0"),
            "fixed",
            Decimal("7.0"),
            "capital",
        ),
    ]
    expected_asset = (Decimal("70000000") / (Decimal("1.255")) ** Decimal("0.003")).quantize(
        MONEY, rounding=ROUND_HALF_UP
    )
    expected_liab = (Decimal("45000000") / (Decimal("1.295")) ** Decimal("7.0")).quantize(
        MONEY, rounding=ROUND_HALF_UP
    )
    expected = (expected_asset - expected_liab).quantize(MONEY, rounding=ROUND_HALF_UP)
    assert compute_eve(reduced, BASE_CURVE, {}) == expected


def test_base_eve_matches_independent_derivation() -> None:
    positions = _positions()
    engine_eve = compute_eve(positions, BASE_CURVE, {})
    assert engine_eve == _independent_eve(positions, {})
    # Regression anchor for the canonical Sample Bank book.
    assert engine_eve == Decimal("-100562865.7068")


def test_duration_golden() -> None:
    duration = compute_duration(_positions(), BASE_CURVE)
    assert duration.asset_macaulay == Decimal("1.0503")
    assert duration.asset_modified == Decimal("0.8290")
    assert duration.liability_macaulay == Decimal("0.4121")
    assert duration.liability_modified == Decimal("0.3267")
    assert duration.duration_gap == Decimal("0.4807")
    # Duration gap = ModDur_A - (PV_L / PV_A) * ModDur_L, positive => asset-sensitive.
    expected_gap = (
        duration.asset_modified
        - (duration.pv_liabilities / duration.pv_assets) * duration.liability_modified
    ).quantize(DUR, rounding=ROUND_HALF_UP)
    assert duration.duration_gap == expected_gap


def test_run_scenarios_golden() -> None:
    result = run_irr_scenarios(_positions(), BASE_CURVE, SCENARIO_SHOCKS, TIER1, EVE_LIMIT)
    scenarios = {scenario.scenario_code: scenario for scenario in result.scenarios}
    assert set(scenarios) == {
        "parallel_up_200",
        "parallel_down_200",
        "short_up_250",
        "short_down_250",
        "steepener",
        "flattener",
    }
    # Rates up reduce the (asset-sensitive) EVE; rates down raise it.
    assert scenarios["parallel_up_200"].delta_eve == Decimal("-13835507.3950")
    assert scenarios["parallel_up_200"].delta_eve_pct_tier1 == Decimal("-4.941253")
    assert scenarios["parallel_down_200"].delta_eve == Decimal("14986975.3393")
    assert scenarios["parallel_down_200"].delta_eve_pct_tier1 == Decimal("5.352491")

    # Independent re-derivation of the parallel-up ΔEVE (+200bp = +2.0pp shift).
    up_shift = {mid: Decimal("2.0") for mid in BASE_CURVE}
    expected_up_eve = _independent_eve(_positions(), up_shift)
    assert scenarios["parallel_up_200"].eve == expected_up_eve

    assert result.worst_scenario_code == "parallel_down_200"
    assert result.worst_delta_eve == Decimal("14986975.3393")
    assert result.worst_delta_eve_pct_tier1 == Decimal("5.352491")
    assert result.breach is False
    assert [item.line_code for item in result.line_items][0] == "base"
    assert all(item.section == "irr_eve" for item in result.line_items)
    assert len(result.line_items) == 7


def test_eve_limit_breach_when_tier1_is_thin() -> None:
    # A thin Tier 1 base forces the worst |ΔEVE| above the 15% limit.
    thin_tier1 = Decimal("60") * M
    result = run_irr_scenarios(_positions(), BASE_CURVE, SCENARIO_SHOCKS, thin_tier1, EVE_LIMIT)
    # 14.986975M / 60M = 24.98% > 15%.
    assert result.worst_delta_eve_pct_tier1 == Decimal("24.978292")
    assert result.breach is True
    assert any(scenario.breach for scenario in result.scenarios)


def test_missing_curve_point_raises() -> None:
    trimmed = {mid: rate for mid, rate in BASE_CURVE.items() if mid != Decimal("7.0")}
    with pytest.raises(MissingParameterError) as excinfo:
        compute_duration(_positions(), trimmed)
    assert "base_curve" in excinfo.value.name


def test_missing_scenario_shock_raises() -> None:
    trimmed = {code: shocks for code, shocks in SCENARIO_SHOCKS.items() if code != "flattener"}
    with pytest.raises(MissingParameterError) as excinfo:
        run_irr_scenarios(_positions(), BASE_CURVE, trimmed, TIER1, EVE_LIMIT)
    assert excinfo.value.name == "stress_shock:flattener"


def test_unsupported_shock_key_raises() -> None:
    shocks = {
        **SCENARIO_SHOCKS,
        "parallel_up_200": {"parallel_bp": Decimal("200"), "junk": Decimal("1")},
    }
    with pytest.raises(UnsupportedShockError) as excinfo:
        run_irr_scenarios(_positions(), BASE_CURVE, shocks, TIER1, EVE_LIMIT)
    assert excinfo.value.shock_key == "junk"
