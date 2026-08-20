"""Hand-verified tests for the contingent-leverage stress (Phase 4 item 4).

Goldens use the documented coefficients: PFE stress ×0.5, SFT haircut +10pp,
netting breakdown 20%, collateral-swap uplift 15%.
"""

from __future__ import annotations

from decimal import Decimal

from app.domain.stress.contingent_leverage import (
    ContingentLeverageInputs,
    DerivativePosition,
    SftPosition,
    compute_contingent_leverage,
)

_BASE_EXPOSURE = Decimal("1000000000")
_TIER1 = Decimal("100000000")


def test_all_four_channels_inflate_the_leverage_exposure() -> None:
    result = compute_contingent_leverage(
        ContingentLeverageInputs(
            base_leverage_exposure=_BASE_EXPOSURE,
            tier1=_TIER1,
            derivatives=(
                DerivativePosition("D1", Decimal("400000000"), Decimal("20000000")),
                DerivativePosition("D2", Decimal("200000000"), Decimal("10000000")),
            ),
            sfts=(SftPosition("S1", Decimal("40000000")),),
            netting_benefit=Decimal("25000000"),
            collateral_swaps_notional=Decimal("60000000"),
        )
    )
    # PFE 30M × 0.5 = 15M; SFT 40M × 10% = 4M; netting 25M × 20% = 5M;
    # collateral swap 60M × 15% = 9M → total uplift 33M.
    assert result.derivative_addon_uplift == Decimal("15000000.0000")
    assert result.sft_addon_uplift == Decimal("4000000.0000")
    assert result.netting_offset_reduction == Decimal("5000000.0000")
    assert result.collateral_swap_uplift == Decimal("9000000.0000")
    assert result.total_uplift == Decimal("33000000.0000")
    assert result.stressed_leverage_exposure == Decimal("1033000000.0000")
    # Leverage ratio erodes from 10% to 100M/1033M = 9.680542%.
    assert result.base_leverage_ratio_pct == Decimal("10.000000")
    assert result.stressed_leverage_ratio_pct == Decimal("9.680542")
    assert result.leverage_ratio_impact_pp == Decimal("-0.319458")
    assert result.has_contingent_positions is True


def test_sfts_add_exposure_over_the_no_sft_case() -> None:
    without = compute_contingent_leverage(
        ContingentLeverageInputs(
            base_leverage_exposure=_BASE_EXPOSURE,
            tier1=_TIER1,
            derivatives=(DerivativePosition("D", Decimal("100000000"), Decimal("10000000")),),
        )
    )
    with_sft = compute_contingent_leverage(
        ContingentLeverageInputs(
            base_leverage_exposure=_BASE_EXPOSURE,
            tier1=_TIER1,
            derivatives=(DerivativePosition("D", Decimal("100000000"), Decimal("10000000")),),
            sfts=(SftPosition("S", Decimal("50000000")),),
        )
    )
    # The SFT adds 50M × 10% = 5M of exposure the no-SFT case lacks.
    assert with_sft.sft_addon_uplift == Decimal("5000000.0000")
    assert (
        with_sft.stressed_leverage_exposure - without.stressed_leverage_exposure
        == Decimal("5000000.0000")
    )
    assert with_sft.stressed_leverage_ratio_pct < without.stressed_leverage_ratio_pct


def test_graceful_zero_without_contingent_positions() -> None:
    result = compute_contingent_leverage(
        ContingentLeverageInputs(base_leverage_exposure=_BASE_EXPOSURE, tier1=_TIER1)
    )
    assert result.total_uplift == Decimal("0.0000")
    assert result.stressed_leverage_exposure == result.base_leverage_exposure
    assert result.stressed_leverage_ratio_pct == result.base_leverage_ratio_pct
    assert result.leverage_ratio_impact_pp == Decimal("0")
    assert result.has_contingent_positions is False


def test_reproducible() -> None:
    inputs = ContingentLeverageInputs(
        base_leverage_exposure=_BASE_EXPOSURE,
        tier1=_TIER1,
        derivatives=(DerivativePosition("D", Decimal("100000000"), Decimal("10000000")),),
    )
    assert compute_contingent_leverage(inputs).serialize() == compute_contingent_leverage(
        inputs
    ).serialize()
