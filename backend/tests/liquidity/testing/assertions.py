"""Liquidity reconciliation and metamorphic assertions over public results."""

from decimal import Decimal

from app.domain.liquidity.engine import RATIO_PCT, CurrencyGapResult, LcrResult, NsfrResult
from tests.support.assertions import TolerancePolicy

TOLERANCE = TolerancePolicy(
    2 * RATIO_PCT,
    "percentage points",
    "domain/liquidity/engine.py: independent ROUND_HALF_UP ratio quantization",
)


def assert_ladder_totals(result: CurrencyGapResult) -> None:
    for gap in result.gaps:
        for side, buckets, total in (
            ("assets", gap.assets, gap.assets_total),
            ("liabilities", gap.liabilities, gap.liabilities_total),
        ):
            assert len(buckets) == 5, f"Liquidity {gap.currency}: {side} requires five horizons"
            assert all(v.is_finite() and v >= 0 for v in buckets), (
                f"Liquidity {gap.currency}: invalid {side} buckets {buckets}"
            )
            assert sum(buckets, Decimal(0)) == total, (
                f"Liquidity {gap.currency}: {side} buckets do not reconcile to total"
            )
        assert gap.cumulative[-1] == gap.net_total, (
            f"Liquidity {gap.currency}: cumulative gap does not reconcile to total"
        )


def assert_liquidity_invariants(lcr: LcrResult, nsfr: NsfrResult) -> None:
    assert lcr.net_outflows_total > 0 and nsfr.rsf_total > 0, (
        "Liquidity: computable monitoring ratios require positive funding denominators"
    )
    assert lcr.capped_inflows_total <= lcr.inflow_cap_amount, (
        f"Liquidity: capped inflows exceed cap {lcr!r}"
    )
    for value in (lcr.lcr_pct, nsfr.nsfr_pct):
        assert value.is_finite() and value >= 0, f"Liquidity: invalid ratio {value}"


def assert_liquidity_ratios_unchanged(
    actual: tuple[LcrResult, NsfrResult], before: tuple[LcrResult, NsfrResult]
) -> None:
    TOLERANCE.assert_close(
        actual[0].lcr_pct, before[0].lcr_pct, because="Liquidity: amount scaling changed LCR"
    )
    TOLERANCE.assert_close(
        actual[1].nsfr_pct, before[1].nsfr_pct, because="Liquidity: amount scaling changed NSFR"
    )
