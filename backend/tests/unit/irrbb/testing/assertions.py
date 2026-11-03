"""IRRBB conservation and comparison evidence, never discounting formulas."""

from dataclasses import dataclass
from decimal import Decimal

from app.icaap.domain.pillar2.types import MethodResult
from app.irrbb.domain.standardised import MONEY, RATIO_PCT, SfResult
from tests.support.assertions import TolerancePolicy


@dataclass(frozen=True)
class IrrbbTolerance:
    money: TolerancePolicy = TolerancePolicy(
        MONEY, "currency units", "irr/standardised.py: 6dp HALF_UP"
    )
    ratios: TolerancePolicy = TolerancePolicy(
        RATIO_PCT, "percentage points", "irr/standardised.py: 6dp HALF_UP"
    )
    interim: TolerancePolicy = TolerancePolicy(
        Decimal("0.0001"), "reporting currency units", "icaap/domain/units.py: 4dp HALF_UP bridge"
    )
    conservation_relative: Decimal = Decimal("0.0000000000000001")


TOLERANCE = IrrbbTolerance()


def assert_irrbb_invariants(result: SfResult) -> None:
    measure = result.measures.all_scenarios.measure
    assert measure >= 0, "IRRBB: economic-value loss measure is negative"
    for scenario in result.scenarios:
        assert 0 <= scenario.loss <= measure, (
            f"IRRBB {scenario.code}: loss outside nonnegative measure"
        )
        for row in scenario.by_currency:
            assert scenario.loss >= max(Decimal(0), row.delta_eve_reporting), (
                f"IRRBB {scenario.code}/{row.currency}: currency loss is understated"
            )


def assert_irrbb_scaled(after: SfResult, before: SfResult, scale: int) -> None:
    # Both compared results round independently to a quantum; one base quantum
    # amplified by the scale bounds that rounding, without relative risk tolerance.
    policy = TolerancePolicy(
        TOLERANCE.money.absolute * scale, TOLERANCE.money.unit, TOLERANCE.money.source
    )
    for old, new in zip(before.scenarios, after.scenarios, strict=True):
        policy.assert_close(
            new.loss, old.loss * scale, because=f"IRRBB {old.code}: scaling changed loss"
        )
        for previous, current in zip(old.by_currency, new.by_currency, strict=True):
            policy.assert_close(
                current.delta_eve_native,
                previous.delta_eve_native * scale,
                because=f"IRRBB {old.code}/{previous.currency}: scaling changed native delta",
            )


def assert_irrbb_ratio_scaled(
    after: SfResult, before: SfResult, scale: int, *, tier1: Decimal
) -> None:
    before_rounding = (
        TOLERANCE.money.absolute / 2 * sum(currency.material for currency in before.currencies)
    )
    after_rounding = (
        TOLERANCE.money.absolute / 2 * sum(currency.material for currency in after.currencies)
    )
    policy = TolerancePolicy(
        (before_rounding / tier1 + after_rounding / (tier1 * scale)) * 100
        + TOLERANCE.ratios.absolute,
        TOLERANCE.ratios.unit,
        TOLERANCE.ratios.source,
    )
    policy.assert_close(
        after.pct_tier1,
        before.pct_tier1,
        because="IRRBB: scaling book and capital changed outlier percentage",
    )


def assert_principal_conserved(actual: Decimal, opening: Decimal, *, operation: str) -> None:
    # Decimal exponentiation in behavioural survival is inexact; preserve the
    # original test_sf_properties budget, much tighter than the reporting quantum.
    assert abs(actual - opening) <= (abs(opening) + 1) * TOLERANCE.conservation_relative, (
        f"IRRBB {operation}: principal {opening} became {actual}"
    )


def assert_interim_not_increased(after: MethodResult, before: MethodResult) -> None:
    assert after.baseline_amount is not None and before.baseline_amount is not None, (
        "IRRBB interim: complete shocks lost their amount"
    )
    assert 0 <= after.baseline_amount <= before.baseline_amount, (
        "IRRBB interim: improving EVE increased loss charge"
    )
