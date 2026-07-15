"""Hand-verified golden tests for the pure FTP engine.

The curve, product, branch and NMD fixtures mirror the Sample Bank Ltd seed's
latest reporting period (2026-03, factor 1.0, canonical amounts). Every
expectation is derived independently by hand (net margins, balance-weighted
NIMs, branch contributions, the core/volatile split), never a straight echo of
the engine's own output.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.domain.ftp.engine import (
    CurvePoint,
    FtpBranch,
    FtpComputationError,
    FtpNmd,
    FtpProduct,
    branch_profitability,
    build_curve,
    classify_core_band,
    nmd_split,
    product_profitability,
    shift_curve,
)

M = Decimal("1000000")

# (tenor_label, tenor_years, base_yield_pct, liquidity_bps, funding_bps, ftp_pct).
_CURVE = (
    ("overnight", "0.003", "25.0", "0", "40", "25.40"),
    ("91d", "0.25", "25.4", "0", "45", "25.85"),
    ("182d", "0.5", "26.1", "5", "45", "26.60"),
    ("1y", "1.0", "27.0", "10", "50", "27.60"),
    ("2y", "2.0", "27.8", "20", "50", "28.50"),
    ("3y", "3.0", "28.4", "30", "55", "29.25"),
    ("5y", "5.0", "28.9", "40", "55", "29.85"),
    ("10y", "10.0", "29.5", "50", "60", "30.60"),
)

# (product, category, balance_m, tenor, customer, ftp, opex, ecl, cap).
_PRODUCTS = (
    ("corporate_5y", "asset", "560", "5.0", "32.0", "29.85", "0.5", "0.8", "0.15"),
    ("sme_2y", "asset", "280", "2.0", "33.0", "28.50", "0.7", "1.2", "0.11"),
    ("mortgage_10y", "asset", "200", "10.0", "30.0", "30.60", "0.4", "0.3", "0.05"),
    ("retail_1y", "asset", "250", "1.0", "34.0", "27.60", "0.9", "1.5", "0.08"),
    ("gov_securities_3y", "asset", "620", "3.0", "27.5", "29.25", "0.05", "0", "0"),
    ("current_accounts", "liability", "700", "0.003", "0.0", "25.40", "0.3", "0", "0"),
    ("savings", "liability", "300", "0.5", "8.5", "26.60", "0.4", "0", "0"),
    ("term_3m", "liability", "280", "0.25", "21.5", "25.85", "0.2", "0", "0"),
    ("term_1y", "liability", "220", "1.0", "23.4", "27.60", "0.2", "0", "0"),
    ("wholesale", "liability", "240", "0.25", "23.0", "25.85", "0.1", "0", "0"),
)

# (branch, deposits_m, loans_m).
_BRANCHES = (
    ("accra_main", "520", "380"),
    ("kumasi", "310", "260"),
    ("takoradi", "180", "210"),
    ("tema", "240", "150"),
    ("tamale", "130", "90"),
    ("cape_coast", "120", "90"),
)

# (segment, balance_m, core_pct, volatile_pct, effective_duration_years).
_NMDS = (
    ("current_accounts", "700", "65", "35", "2.5"),
    ("savings", "300", "70", "30", "3.0"),
)

MIN_MARGIN = Decimal("0")


def _curve() -> list[CurvePoint]:
    return [
        CurvePoint(
            tenor_label=label,
            tenor_years=Decimal(tenor),
            base_yield_pct=Decimal(base),
            liquidity_premium_bps=Decimal(liq),
            funding_spread_bps=Decimal(fund),
            ftp_rate_pct=Decimal(ftp),
        )
        for label, tenor, base, liq, fund, ftp in _CURVE
    ]


def _products() -> list[FtpProduct]:
    return [
        FtpProduct(
            product=product,
            category=category,  # type: ignore[arg-type]
            balance_ghs=Decimal(balance) * M,
            tenor_years=Decimal(tenor),
            customer_rate_pct=Decimal(customer),
            ftp_rate_pct=Decimal(ftp),
            operating_cost_pct=Decimal(opex),
            expected_credit_loss_pct=Decimal(ecl),
            capital_charge_pct=Decimal(cap),
        )
        for product, category, balance, tenor, customer, ftp, opex, ecl, cap in _PRODUCTS
    ]


def _branches() -> list[FtpBranch]:
    return [
        FtpBranch(branch=branch, deposits_ghs=Decimal(dep) * M, loans_ghs=Decimal(loan) * M)
        for branch, dep, loan in _BRANCHES
    ]


def _nmds() -> list[FtpNmd]:
    return [
        FtpNmd(
            segment=segment,
            balance_ghs=Decimal(balance) * M,
            core_pct=Decimal(core),
            volatile_pct=Decimal(volatile),
            effective_duration_years=Decimal(duration),
        )
        for segment, balance, core, volatile, duration in _NMDS
    ]


def test_curve_arithmetic_and_interpolation() -> None:
    curve = build_curve(_curve())
    assert curve.arithmetic_consistent is True
    assert curve.inconsistent_labels == ()
    assert [item.line_code for item in curve.line_items] == [
        "overnight",
        "91d",
        "182d",
        "1y",
        "2y",
        "3y",
        "5y",
        "10y",
    ]

    by_label = {point.tenor_label: point for point in curve.points}
    # ftp = base + (liquidity_bps + funding_bps) / 100.
    assert by_label["overnight"].ftp_rate_pct == Decimal("25.40")  # 25.0 + (0 + 40)/100
    assert by_label["3y"].ftp_rate_pct == Decimal("29.25")  # 28.4 + (30 + 55)/100
    assert by_label["10y"].ftp_rate_pct == Decimal("30.60")  # 29.5 + (50 + 60)/100

    # Exact tenors resolve to their point; a 2.5y tenor interpolates 2y..3y.
    assert curve.rate_at(Decimal("5.0")) == Decimal("29.85")
    assert curve.rate_at(Decimal("3.0")) == Decimal("29.25")
    # 28.50 + (2.5 - 2)/(3 - 2) * (29.25 - 28.50) = 28.50 + 0.375 = 28.875.
    assert curve.rate_at(Decimal("2.5")) == Decimal("28.875000")
    # Below the first / above the last point clamps to the endpoint rate.
    assert curve.rate_at(Decimal("0.001")) == Decimal("25.40")
    assert curve.rate_at(Decimal("20")) == Decimal("30.60")
    assert curve.overnight_rate_pct == Decimal("25.40")


def test_build_curve_flags_inconsistent_point() -> None:
    points = _curve()
    broken = CurvePoint(
        tenor_label="1y",
        tenor_years=Decimal("1.0"),
        base_yield_pct=Decimal("27.0"),
        liquidity_premium_bps=Decimal("10"),
        funding_spread_bps=Decimal("50"),
        ftp_rate_pct=Decimal("99.99"),  # should be 27.60
    )
    curve = build_curve([broken, *points[1:]])
    assert curve.arithmetic_consistent is False
    assert curve.inconsistent_labels == ("1y",)


def test_product_net_margins_and_portfolio_nim() -> None:
    curve = build_curve(_curve())
    result = product_profitability(_products(), curve, MIN_MARGIN)
    by_product = {product.product: product for product in result.products}

    # Asset: customer - ftp - opex - ecl - capital.
    assert by_product["corporate_5y"].net_margin_pct == Decimal("0.700000")  # 32-29.85-.5-.8-.15
    assert by_product["corporate_5y"].below_min_margin is False
    # Mortgages price below FTP: 30 - 30.60 - 0.4 - 0.3 - 0.05 = -1.35 (a finding).
    assert by_product["mortgage_10y"].net_margin_pct == Decimal("-1.350000")
    assert by_product["mortgage_10y"].below_min_margin is True
    # Government securities earn below FTP: 27.5 - 29.25 - 0.05 = -1.80 (funding drag).
    assert by_product["gov_securities_3y"].net_margin_pct == Decimal("-1.800000")
    # Liability: ftp - customer - opex. Current accounts earn the full FTP credit.
    assert by_product["current_accounts"].net_margin_pct == Decimal("25.100000")  # 25.40-0-0.30
    assert by_product["current_accounts"].contribution_ghs == Decimal("175700000.0000")

    # Two products (mortgage_10y, gov_securities_3y) price below the zero floor.
    assert result.products_below_min_margin == 2
    assert result.below_min_products == ("gov_securities_3y", "mortgage_10y")

    # Balance-weighted margins over the 3,650M book (assets 1,910M, liabilities 1,740M):
    #   assets  Σ(bal*margin) = 683.2M%   -> 683.2 / 1910   = 0.357696%
    #   liabs   Σ(bal*margin) = 25,582M%  -> 25,582 / 1740  = 14.702299%
    #   book    Σ(bal*margin) = 26,265.2M% -> 26,265.2 / 3650 = 7.195945%
    assert result.weighted_asset_yield_pct == Decimal("0.357696")
    assert result.weighted_funding_credit_pct == Decimal("14.702299")
    assert result.portfolio_nim_pct == Decimal("7.195945")
    assert result.total_balance_ghs == Decimal("3650000000.0000")
    assert result.total_contribution_ghs == Decimal("262652000.0000")


def test_product_profitability_reprices_under_rate_shock() -> None:
    curve = shift_curve(build_curve(_curve()), Decimal("2.00"))
    result = product_profitability(_products(), curve, MIN_MARGIN)
    by_product = {product.product: product for product in result.products}
    # Every FTP rate lifts 200 bp: corporate margin 32 - 31.85 - 0.5 - 0.8 - 0.15 = -1.30.
    assert by_product["corporate_5y"].net_margin_pct == Decimal("-1.300000")
    assert by_product["corporate_5y"].below_min_margin is True
    # Deposits gain: current accounts 27.40 - 0 - 0.30 = 27.10.
    assert by_product["current_accounts"].net_margin_pct == Decimal("27.100000")
    # The stress pushes corporate below the floor -> three products now flagged.
    assert result.products_below_min_margin == 3
    # Portfolio Σ(bal*margin) = 25,925.2M% -> 25,925.2 / 3650 = 7.102795%.
    assert result.portfolio_nim_pct == Decimal("7.102795")


def test_branch_ranking_and_total_contribution() -> None:
    # Portfolio asset yield / funding credit from the product golden above.
    result = branch_profitability(_branches(), Decimal("0.357696"), Decimal("14.702299"))
    assert [branch.branch for branch in result.branches] == [
        "accra_main",
        "kumasi",
        "tema",
        "takoradi",
        "tamale",
        "cape_coast",
    ]
    assert [branch.rank for branch in result.branches] == [1, 2, 3, 4, 5, 6]

    by_branch = {branch.branch: branch for branch in result.branches}
    # Accra: 520M * 0.14702299 + 380M * 0.00357696 = 76,451,954.8 + 1,359,244.8.
    assert by_branch["accra_main"].net_contribution_ghs == Decimal("77811199.6000")
    # Tema (deposit-heavy) outranks Takoradi (loan-heavy) on FTP funding credit.
    assert by_branch["tema"].net_contribution_ghs == Decimal("35822061.6000")
    assert by_branch["takoradi"].net_contribution_ghs == Decimal("27215299.8000")
    assert result.total_contribution_ghs == Decimal("224755297.8000")
    assert result.total_deposits_ghs == Decimal("1500000000.0000")
    assert result.total_loans_ghs == Decimal("1180000000.0000")


def test_nmd_core_volatile_split_and_policy() -> None:
    curve = build_curve(_curve())
    result = nmd_split(_nmds(), curve, Decimal("60"), Decimal("70"))
    by_segment = {segment.segment: segment for segment in result.segments}

    current = by_segment["current_accounts"]
    assert current.core_amount_ghs == Decimal("455000000.0000")  # 700M * 65%
    assert current.volatile_amount_ghs == Decimal("245000000.0000")
    assert current.core_ftp_pct == Decimal("28.875000")  # curve at 2.5y
    assert current.volatile_ftp_pct == Decimal("25.40")  # overnight
    # assigned = (65 * 28.875 + 35 * 25.40) / 100 = 27.65875.
    assert current.assigned_ftp_pct == Decimal("27.658750")

    savings = by_segment["savings"]
    assert savings.core_ftp_pct == Decimal("29.25")  # curve at 3.0y (exact)
    # assigned = (70 * 29.25 + 30 * 25.40) / 100 = 28.095.
    assert savings.assigned_ftp_pct == Decimal("28.095000")

    # Blended core = (455M + 210M) / 1000M = 66.5%; inside the 60-70% band.
    assert result.total_core_ghs == Decimal("665000000.0000")
    assert result.core_pct == Decimal("66.500000")
    assert result.volatile_pct == Decimal("33.500000")
    assert result.within_policy is True
    # Blended assigned FTP = (700M * 27.65875 + 300M * 28.095) / 1000M = 27.789625.
    assert result.blended_assigned_ftp_pct == Decimal("27.789625")


def test_classify_core_band() -> None:
    assert classify_core_band(Decimal("66.5"), Decimal("60"), Decimal("70")) == "green"
    assert classify_core_band(Decimal("71.5"), Decimal("60"), Decimal("70")) == "amber"
    assert classify_core_band(Decimal("55"), Decimal("60"), Decimal("70")) == "red"


def test_empty_inputs_raise() -> None:
    curve = build_curve(_curve())
    with pytest.raises(FtpComputationError):
        product_profitability([], curve, MIN_MARGIN)
    with pytest.raises(FtpComputationError):
        branch_profitability([], Decimal("1"), Decimal("1"))
    with pytest.raises(FtpComputationError):
        build_curve([])
