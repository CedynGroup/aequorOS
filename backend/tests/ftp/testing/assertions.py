"""Reconcile FTP disclosures and compare runs without a second pricing formula."""

from dataclasses import dataclass
from decimal import Decimal

from app.ftp.domain.engine import MONEY, RATIO_PCT, NmdResult, ProductResult
from tests.support.assertions import TolerancePolicy


@dataclass(frozen=True)
class FtpTolerance:
    money: TolerancePolicy = TolerancePolicy(MONEY, "GHS", "ftp/engine.py: 4dp HALF_UP")
    ratios: TolerancePolicy = TolerancePolicy(
        RATIO_PCT, "percentage points", "ftp/engine.py: 6dp HALF_UP"
    )


TOLERANCE = FtpTolerance()


def assert_ftp_invariants(result: ProductResult, nmd: NmdResult) -> None:
    assert result.total_balance_ghs == sum((p.balance_ghs for p in result.products), Decimal(0)), (
        "FTP: product balances do not reconcile to book"
    )
    # Individual contributions round before summing; the aggregate rounds after summing.
    assert (
        abs(
            result.total_contribution_ghs
            - sum((p.contribution_ghs for p in result.products), Decimal(0))
        )
        <= len(result.products) * TOLERANCE.money.absolute
    ), "FTP: rounded contributions do not reconcile to book"
    assert result.products_below_min_margin == len(result.below_min_products), (
        "FTP: below-floor disclosure count differs"
    )
    assert nmd.total_core_ghs + nmd.total_volatile_ghs == nmd.total_balance_ghs, (
        "FTP: NMD core and volatile amounts do not exhaust deposits"
    )
    for segment in nmd.segments:
        assert segment.core_pct + segment.volatile_pct == 100, (
            f"FTP {segment.segment}: shares do not sum to 100%"
        )
        assert segment.core_amount_ghs + segment.volatile_amount_ghs == segment.balance_ghs, (
            f"FTP {segment.segment}: split loses deposit balance"
        )


def assert_ftp_ratios_unchanged(after: ProductResult, before: ProductResult) -> None:
    for name, actual, expected in (
        ("portfolio NIM", after.portfolio_nim_pct, before.portfolio_nim_pct),
        ("asset yield", after.weighted_asset_yield_pct, before.weighted_asset_yield_pct),
        ("funding credit", after.weighted_funding_credit_pct, before.weighted_funding_credit_pct),
    ):
        TOLERANCE.ratios.assert_close(actual, expected, because=f"FTP: scaling changed {name}")
