"""Hand-verified tests for the concentration stress (Phase 4 item 2).

Goldens are computed independently against the documented coefficients
(single-name LGD 60% / full default, sector loss 10%, HHI charge coeff 0.5).
"""

from __future__ import annotations

from decimal import Decimal

from app.domain.stress.concentration import (
    DIM_FUNDING,
    DIM_SECTOR,
    DIM_SINGLE_NAME,
    ConcentrationExposure,
    ConcentrationInputs,
    FundingPosition,
    compute_concentration,
)

_BASE_CREDIT_RWA = Decimal("100000000")


def _book() -> tuple[ConcentrationExposure, ...]:
    return (
        # Connected group A: two exposures share group_key → one 80M name.
        ConcentrationExposure("A1", Decimal("60000000"), "group:A", sector="agri", geography="GH"),
        ConcentrationExposure("A2", Decimal("20000000"), "group:A", sector="agri", geography="GH"),
        ConcentrationExposure("B", Decimal("30000000"), "cp:B", sector="energy", geography="NG"),
        ConcentrationExposure("C", Decimal("10000000"), "cp:C", sector="trade", geography="GH"),
    )


def _inputs() -> ConcentrationInputs:
    return ConcentrationInputs(
        exposures=_book(),
        funding=(
            FundingPosition("dep:X", Decimal("40000000")),
            FundingPosition("dep:Y", Decimal("10000000")),
        ),
        base_credit_rwa=_BASE_CREDIT_RWA,
    )


def test_single_name_default_and_connected_group() -> None:
    result = compute_concentration(_inputs())
    # Connected group A (60M + 20M) is the single largest name at 80M.
    assert result.largest_group_key == "group:A"
    assert result.largest_group_exposure == Decimal("80000000.0000")
    single = result.dimension(DIM_SINGLE_NAME)
    assert single is not None
    assert single.bucket_count == 3  # A, B, C (A's two rows collapse into one group)
    assert single.top_share_pct == Decimal("66.6667")  # 80 / 120
    # name HHI = (80/120)² + (30/120)² + (10/120)² = 74/144.
    assert single.hhi == Decimal("0.513889")
    # single-name loss = 80M × 100% default × 60% LGD = 48M.
    assert single.incremental_loss == Decimal("48000000.0000")
    # Pillar-2 name charge = 100M × 0.5 × 0.513889 = 25,694,450.
    assert single.pillar2_charge == Decimal("25694450.0000")


def test_sectoral_concentration() -> None:
    result = compute_concentration(_inputs())
    sector = result.dimension(DIM_SECTOR)
    assert sector is not None
    assert sector.top_key == "agri"
    assert sector.top_amount == Decimal("80000000.0000")
    # sector loss = 80M × 10% = 8M.
    assert sector.incremental_loss == Decimal("8000000.0000")


def test_totals_and_pillar2_charge() -> None:
    result = compute_concentration(_inputs())
    # total incremental loss = single-name 48M + sector 8M = 56M.
    assert result.total_incremental_loss == Decimal("56000000.0000")
    # Pillar-2 charge = name 25,694,450 + sector 25,694,450.
    assert result.pillar2_concentration_charge == Decimal("51388900.0000")


def test_funding_concentration() -> None:
    result = compute_concentration(_inputs())
    funding = result.dimension(DIM_FUNDING)
    assert funding is not None
    assert result.largest_funding_source == "dep:X"
    # largest funder withdraws in full: 40M × 100%.
    assert result.largest_funding_outflow == Decimal("40000000.0000")
    assert funding.top_share_pct == Decimal("80.0000")  # 40 / 50


def test_graceful_zero_with_no_positions() -> None:
    result = compute_concentration(ConcentrationInputs())
    assert result.total_incremental_loss == Decimal("0")
    assert result.pillar2_concentration_charge == Decimal("0")
    assert result.largest_group_key is None
    assert result.largest_funding_source is None
    for dimension in result.dimensions:
        assert dimension.hhi == Decimal("0")
        assert dimension.incremental_loss == Decimal("0")


def test_missing_dimension_keys_are_skipped() -> None:
    # Exposures without a sector do not pollute the sector HHI.
    inputs = ConcentrationInputs(
        exposures=(
            ConcentrationExposure("X", Decimal("50000000"), "cp:X"),
            ConcentrationExposure("Y", Decimal("50000000"), "cp:Y"),
        ),
        base_credit_rwa=_BASE_CREDIT_RWA,
    )
    result = compute_concentration(inputs)
    sector = result.dimension(DIM_SECTOR)
    assert sector is not None
    assert sector.bucket_count == 0
    assert sector.incremental_loss == Decimal("0")
    # Two equal single names ⇒ HHI = 0.5² + 0.5² = 0.5.
    single = result.dimension(DIM_SINGLE_NAME)
    assert single is not None
    assert single.hhi == Decimal("0.5")
