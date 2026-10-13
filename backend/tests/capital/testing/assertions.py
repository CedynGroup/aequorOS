"""Capital invariants and relations between engine runs; no ratio formula oracle."""

from app.domain.capital.engine import RATIO_PCT, CapitalRatiosResult, RwaResult
from tests.support.assertions import TolerancePolicy

TOLERANCE = TolerancePolicy(
    2 * RATIO_PCT,
    "percentage points",
    "domain/capital/engine.py: independent ROUND_HALF_UP ratio quantization",
)


def assert_capital_invariants(rwa: RwaResult, result: CapitalRatiosResult) -> None:
    assert rwa.total_rwa == rwa.credit_rwa + rwa.market_rwa + rwa.operational_rwa, (
        f"Capital: RWA classes do not reconcile: {rwa!r}"
    )
    assert result.total_capital == result.tier1_capital + result.tier2_capital, (
        f"Capital: funded tiers do not reconcile: {result!r}"
    )
    assert result.tier1_capital == result.cet1_capital + result.at1_capital, (
        f"Capital: Tier 1 does not reconcile: {result!r}"
    )
    for value in (
        result.car_pct,
        result.cet1_ratio_pct,
        result.tier1_ratio_pct,
        result.leverage_ratio_pct,
    ):
        assert value.is_finite() and value >= 0, f"Capital: invalid ratio {value}"


def assert_capital_ratios_unchanged(
    actual: CapitalRatiosResult, before: CapitalRatiosResult
) -> None:
    pairs = (
        ("CAR", actual.car_pct, before.car_pct),
        ("CET1", actual.cet1_ratio_pct, before.cet1_ratio_pct),
        ("Tier 1", actual.tier1_ratio_pct, before.tier1_ratio_pct),
        ("leverage", actual.leverage_ratio_pct, before.leverage_ratio_pct),
    )
    for name, value, previous in pairs:
        TOLERANCE.assert_close(value, previous, because=f"Capital: amount scaling changed {name}")


def assert_zero_exposure_unchanged(
    actual: CapitalRatiosResult, before: CapitalRatiosResult
) -> None:
    assert_capital_ratios_unchanged(actual, before)
    assert actual.total_capital == before.total_capital, (
        f"Capital: zero exposure changed capital {before.total_capital} -> {actual.total_capital}"
    )
