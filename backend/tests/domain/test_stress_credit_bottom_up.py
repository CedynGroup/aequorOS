"""Hand-verified tests for the bottom-up credit stress (Phase 4 item 1).

Every expected value is derived independently with explicit Decimal literals
against the documented migration coefficients (sensitivity 0.5, cap 0.5,
downgrade step +50pp, rw cap 150%), so the goldens are never engine-self-
referential.
"""

from __future__ import annotations

from decimal import Decimal

from app.domain.stress.credit_bottom_up import (
    CRD_EXPOSURE_CLASSES,
    CreditExposure,
    MigrationParams,
    compute_bottom_up_credit,
    result_for_year,
)
from app.domain.stress.translation import MacroPathPoint


def _book() -> tuple[CreditExposure, ...]:
    return (
        # corporate: 100M @ 2%/45%, RW100, domestic.
        CreditExposure("E1", "corporates", Decimal("100000000"), Decimal("2"), Decimal("45"),
                       Decimal("100")),
        # retail/SME: 50M @ 5%/40%, RW75, domestic.
        CreditExposure("E2", "retail_sme", Decimal("50000000"), Decimal("5"), Decimal("40"),
                       Decimal("75")),
        # bank: 20M @ 1%/30%, RW20, FX-denominated.
        CreditExposure("E3", "banks", Decimal("20000000"), Decimal("1"), Decimal("30"),
                       Decimal("20"), is_foreign_currency=True),
    )


def test_migration_and_fx_revaluation_lift_credit_rwa() -> None:
    result = compute_bottom_up_credit(
        _book(),
        pd_multiplier=Decimal("1.5"),
        lgd_multiplier=Decimal("1.2"),
        fx_fraction=Decimal("0.25"),
    )
    # migration_fraction = min(0.5 × 0.5, 0.5) = 0.25; fx_uplift = 0.25.
    # base credit RWA = 100 + 37.5 + 4 = 141.5M.
    assert result.base_credit_rwa == Decimal("141500000.0000")
    # stressed = 112.5 + 43.75 + 8.125 = 164.375M.
    assert result.stressed_credit_rwa == Decimal("164375000.0000")
    # FX revaluation adds only on the USD exposure: 5M × 20% = 1M.
    assert result.fx_revaluation_rwa == Decimal("1000000.0000")
    # Migration adds 12.5 + 6.25 + 3.125 = 21.875M.
    assert result.migration_rwa == Decimal("21875000.0000")
    # Additive attribution ties exactly: base + fx + migration == stressed.
    assert (
        result.base_credit_rwa + result.fx_revaluation_rwa + result.migration_rwa
        == result.stressed_credit_rwa
    )
    assert result.credit_rwa_uplift_factor == Decimal("1.161661")


def test_exposure_class_decomposition_sums_to_total() -> None:
    result = compute_bottom_up_credit(
        _book(),
        pd_multiplier=Decimal("1.5"),
        lgd_multiplier=Decimal("1.2"),
        fx_fraction=Decimal("0.25"),
    )
    losses = result.incremental_loss_by_class()
    # corporate incremental EL: 100M×3%×54% − 100M×2%×45% = 1.62M − 0.90M = 0.72M.
    assert losses["corporates"] == Decimal("720000.0000")
    # retail/SME: 50M×7.5%×48% − 50M×5%×40% = 1.80M − 1.00M = 0.80M.
    assert losses["retail_sme"] == Decimal("800000.0000")
    # bank (FX-revalued EAD 25M): 25M×1.5%×36% − 20M×1%×30% = 0.135M − 0.06M = 0.075M.
    assert losses["banks"] == Decimal("75000.0000")
    # The by-class incrementals sum to the total incremental loss exactly.
    assert result.incremental_expected_loss == Decimal("1595000.0000")
    assert sum((v for v in losses.values()), Decimal("0")) == result.incremental_expected_loss
    # Every unmodelled class defaults to zero (full CRD contract preserved).
    assert set(losses) == set(CRD_EXPOSURE_CLASSES)
    assert losses["gog"] == Decimal("0")


def test_base_scenario_is_a_zero_delta() -> None:
    result = compute_bottom_up_credit(
        _book(),
        pd_multiplier=Decimal("1"),
        lgd_multiplier=Decimal("1"),
        fx_fraction=Decimal("0"),
    )
    assert result.stressed_credit_rwa == result.base_credit_rwa
    assert result.credit_rwa_uplift_factor == Decimal("1")
    assert result.incremental_expected_loss == Decimal("0")
    assert result.fx_revaluation_rwa == Decimal("0")
    assert result.migration_rwa == Decimal("0")


def test_fx_appreciation_never_reduces_stressed_ead() -> None:
    # A benign (negative) FX fraction floors at zero uplift — no RWA relief.
    result = compute_bottom_up_credit(
        _book(),
        pd_multiplier=Decimal("1"),
        lgd_multiplier=Decimal("1"),
        fx_fraction=Decimal("-0.30"),
    )
    assert result.stressed_credit_rwa == result.base_credit_rwa


def test_migration_fraction_and_rw_cap_are_respected() -> None:
    # A severe PD doubling saturates the migration cap (0.5) and the RW cap (150).
    exposure = (
        CreditExposure("H", "corporates", Decimal("10000000"), Decimal("3"), Decimal("50"),
                       Decimal("120")),
    )
    result = compute_bottom_up_credit(
        exposure,
        pd_multiplier=Decimal("3"),  # migration_fraction = min(0.5×2, 0.5) = 0.5
        lgd_multiplier=Decimal("1"),
        fx_fraction=Decimal("0"),
    )
    # downgraded RW = min(120 + 50, 150) = 150; effective = 0.5×120 + 0.5×150 = 135.
    # stressed RWA = 10M × 135/100 = 13.5M; base = 10M × 120/100 = 12M.
    assert result.base_credit_rwa == Decimal("12000000.0000")
    assert result.stressed_credit_rwa == Decimal("13500000.0000")


def test_override_params_change_the_migration() -> None:
    gentle = MigrationParams(
        migration_sensitivity=Decimal("0.2"),
        max_migration_fraction=Decimal("0.5"),
        downgrade_rw_step_pct=Decimal("25"),
        rw_cap_pct=Decimal("150"),
    )
    exposure = (
        CreditExposure("O", "corporates", Decimal("10000000"), Decimal("2"), Decimal("45"),
                       Decimal("100")),
    )
    result = compute_bottom_up_credit(
        exposure,
        pd_multiplier=Decimal("1.5"),
        lgd_multiplier=Decimal("1"),
        fx_fraction=Decimal("0"),
        params=gentle,
    )
    # migration_fraction = min(0.2×0.5, 0.5) = 0.1; downgraded RW = 125.
    # effective = 0.9×100 + 0.1×125 = 102.5; stressed RWA = 10M × 102.5/100 = 10.25M.
    assert result.stressed_credit_rwa == Decimal("10250000.0000")


def test_perfect_foresight_uses_each_years_own_macro() -> None:
    """result_for_year conditions each year on its own macro (¶48)."""
    def gdp(year: int, stress: str) -> MacroPathPoint:
        return MacroPathPoint("gdp_growth", year, Decimal("0.05"), Decimal(stress))

    paths = (gdp(1, "0.00"), gdp(2, "0.02"))  # y1 delta −0.05 (pd 1.15), y2 −0.03 (pd 1.09)
    book = (
        CreditExposure("C", "corporates", Decimal("100000000"), Decimal("2"), Decimal("45"),
                       Decimal("100")),
    )
    year1 = result_for_year(book, paths, 1)
    year2 = result_for_year(book, paths, 2)
    assert year1.pd_multiplier == Decimal("1.15")
    assert year2.pd_multiplier == Decimal("1.09")
    # A deeper trough in year 1 ⇒ a larger credit-RWA uplift than year 2.
    assert year1.credit_rwa_uplift_factor > year2.credit_rwa_uplift_factor > Decimal("1")


def test_empty_book_is_neutral() -> None:
    result = compute_bottom_up_credit(
        (), pd_multiplier=Decimal("2"), lgd_multiplier=Decimal("2"), fx_fraction=Decimal("1")
    )
    assert result.base_credit_rwa == Decimal("0")
    assert result.credit_rwa_uplift_factor == Decimal("1")
    assert result.by_class == ()
