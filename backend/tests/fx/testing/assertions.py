"""FX policies and metamorphic assertions in the quantities' own units."""

from dataclasses import dataclass
from decimal import Decimal

from app.domain.fx.engine import MONEY, RATIO_PCT, NopResult
from tests.support.assertions import TolerancePolicy


@dataclass(frozen=True)
class FxTolerance:
    """One FX policy: separate quanta for ratio and currency comparisons."""

    ratios: TolerancePolicy = TolerancePolicy(
        2 * RATIO_PCT, "percentage points", "fx/engine.py: six-decimal ROUND_HALF_UP"
    )
    currency: TolerancePolicy = TolerancePolicy(
        MONEY, "currency units", "fx/engine.py: four-decimal ROUND_HALF_UP; synthetic spots >= 10"
    )


TOLERANCE = FxTolerance()


def assert_nop_invariants(result: NopResult) -> None:
    assert result.sum_long >= 0 and result.sum_short >= 0, f"FX: signed side totals: {result}"
    assert result.overall_nop in (result.sum_long, result.sum_short), (
        f"FX: overall NOP is not one of its direction totals: {result}"
    )
    assert result.overall_nop >= result.sum_long and result.overall_nop >= result.sum_short, (
        f"FX: overall NOP understates a direction: {result}"
    )
    assert (
        sum((i.weighted_amount for i in result.line_items), Decimal(0))
        == result.sum_long - result.sum_short
    ), f"FX: disclosure lines do not reconcile to long less short: {result}"
    assert result.tier1 > 0 and result.nop_pct_tier1.is_finite(), (
        f"FX: invalid capital denominator or ratio: {result}"
    )


def assert_fx_ratios_unchanged(after: NopResult, before: NopResult) -> None:
    TOLERANCE.ratios.assert_close(
        after.nop_pct_tier1, before.nop_pct_tier1, because="FX: scaling changed aggregate NOP ratio"
    )
    TOLERANCE.ratios.assert_close(
        after.single_ccy_max_pct,
        before.single_ccy_max_pct,
        because="FX: scaling changed single-currency NOP ratio",
    )


def assert_currency_round_trip(restored: Decimal, original: Decimal, *, currency: str) -> None:
    TOLERANCE.currency.assert_close(
        restored, original, because=f"FX {currency}: currency round trip"
    )
