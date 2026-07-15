"""Deterministic Sample Bank Ltd regulatory seed for the demo organization.

Seeds one bank, twelve monthly reporting periods (April 2025 through March
2026), tie-out validated financial facts per period, and the Bank of Ghana CRD
baseline parameter tables. The seed is idempotent: existing Sample Bank rows
are deleted by fixed UUID before re-insertion.
"""

from __future__ import annotations

import math
from calendar import monthrange
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy import inspect as sql_inspect
from sqlalchemy import text as sql_text
from sqlalchemy.orm import Session

from app.db.base import Base
from app.models import (
    Bank,
    BankFinancialFact,
    BankReportingPeriod,
    Organization,
    ParamCapitalThreshold,
    ParamLcrRunoffRate,
    ParamNsfrWeight,
    ParamRiskWeight,
    ParamStressShock,
    User,
)
from app.models.regulatory import RegulatoryParameterMixin

DEMO_ORG_ID = UUID("11111111-1111-4111-8111-111111111111")
DEMO_ORG_NAME = "AequorOS Demo Organization"
DEMO_USER_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
DEMO_USER_EMAIL = "demo.user.one@example.test"
DEMO_USER_NAME = "Demo User One"
ISOLATED_ORG_ID = UUID("22222222-2222-4222-8222-222222222222")
ISOLATED_ORG_NAME = "AequorOS Isolated Tenant"
SAMPLE_BANK_ID = UUID("77000000-0000-4000-8000-000000000001")

CURRENCY = "GHS"
JURISDICTION_CODE = "GH"
APPROVED_BY = "Bank of Ghana CRD baseline"
FX_APPROVED_BY = "BoG FX baseline"
FTP_APPROVED_BY = "BoG FTP baseline"
APPROVAL_TIMESTAMP = datetime(2025, 1, 1, tzinfo=UTC)
EFFECTIVE_FROM = date(2025, 1, 1)

PERIOD_COUNT = 12
FIRST_PERIOD_YEAR = 2025
FIRST_PERIOD_MONTH = 4

_ONE = Decimal(1)
_ZERO = Decimal(0)
_HUNDRED = Decimal("100")
MILLION = Decimal("1000000")
MONEY = Decimal("0.0001")

_SECURITIES_FACTOR_START = Decimal("0.90")
_LOANS_FACTOR_START = Decimal("0.94")
_DEPOSITS_FACTOR_START = Decimal("0.985")
_CAPITAL_FACTOR_START = Decimal("0.96")
_FX_FACTOR_START = Decimal("0.93")

# Canonical latest-period amounts, in GHS millions.
_FIXED_ASSETS_M: tuple[tuple[str, str], ...] = (
    ("cash_vault", "45"),
    ("bog_required_reserves", "175"),
    ("bog_excess_reserves", "70"),
)
_DEPOSITS_M: tuple[tuple[str, str], ...] = (
    ("retail_deposits_stable", "700"),
    ("retail_deposits_less_stable", "440"),
    ("wholesale_operational", "240"),
    ("wholesale_non_op_sme", "200"),
    ("wholesale_non_op_corporate", "320"),
)
_SECURED_FUNDING_M = "60"
_TERM_BORROWINGS_M = "100"
_OTHER_ASSETS_FLOOR_M = "40"
_LOAN_EXPOSURES_M: tuple[tuple[str, str, str], ...] = (
    ("corporate_unrated", "560", "RW100"),
    ("sme_retail", "280", "RW75"),
    ("retail_other", "250", "RW75"),
    ("residential_mortgage", "200", "RW35"),
    ("commercial_real_estate", "60", "RW100"),
    ("past_due_90", "50", "RW150"),
)
# (category, amount, sourced-from-cash marker); bog_bills and gog_bonds tie to
# the securities_bog_bills and securities_gog_bonds balance-sheet rows.
_SECURITIES_M: tuple[tuple[str, str, bool], ...] = (
    ("bog_bills", "260", False),
    ("gog_bonds", "360", False),
    ("cash_vault_hqla", "45", True),
    ("bog_excess_reserves_hqla", "70", True),
)
_OFF_BALANCE_M: tuple[tuple[str, str, str, str], ...] = (
    ("committed_retail", "80", "50", "RW75"),
    ("committed_corporate", "240", "50", "RW100"),
)
_LCR_INFLOWS_M: tuple[tuple[str, str, str], ...] = (
    ("retail_loan_repayments", "60", "50"),
    ("corporate_sme_repayments", "90", "50"),
    ("interbank_maturing", "45", "100"),
)
_MARKET_RISK_M: tuple[tuple[str, str], ...] = (
    ("net_long_fx", "45"),
    ("net_short_fx", "12"),
)
_OPERATIONAL_INCOME_M: tuple[tuple[str, str, int], ...] = (
    ("gross_income_2023", "340", 2023),
    ("gross_income_2024", "380", 2024),
    ("gross_income_2025", "400", 2025),
)
_CAPITAL_COMPONENTS_M: tuple[tuple[str, str, str, bool], ...] = (
    ("paid_up_capital", "150", "CET1", False),
    ("retained_earnings", "95", "CET1", False),
    ("statutory_reserves", "45", "CET1", False),
    ("other_reserves", "10", "CET1", False),
    ("intangibles", "25", "CET1", True),
    ("deferred_tax_assets", "15", "CET1", True),
    ("perpetual_instruments", "20", "AT1", False),
    ("subordinated_debt", "45", "T2", False),
    ("general_provisions", "15", "T2", False),
)

# IRR rate-sensitive repricing positions (latest-period canonical, GHS millions).
# (category, bucket, millions, rate_pct, fixed_or_float, midpoint_years, source).
#
# Source reconciliation to the balance sheet (canonical 2026-03):
#   - securities-sourced positions (short/medium government bills and bonds) sum
#     to 620M, tying to the balance-sheet securities line (bog_bills 260 +
#     gog_bonds 360) and scaling with the securities factor;
#   - one interbank placement of 70M is sourced from the fixed BoG excess
#     reserves (70M) and does not scale;
#   - the remaining 1,350M is loan-sourced (corporate, SME, mortgage and
#     long-dated held-to-maturity government paper carried in the banking book)
#     and stays within the 1,400M gross loan book, scaling with the loans factor.
# Asset RS total = 620 + 70 + 1,350 = 2,040M.
_IRR_ASSET_POSITIONS: tuple[tuple[str, str, str, str, str, str, str], ...] = (
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
# Interest-bearing funding that reprices; the retail current-account core is
# behaviorally non-rate-sensitive and is excluded. Liability RS total = 1,705M.
_IRR_LIABILITY_POSITIONS: tuple[tuple[str, str, str, str, str, str, str], ...] = (
    ("call_deposits", "overnight", "240", "7.0", "float", "0.003", "deposits"),
    ("wholesale_sme", "8-30d", "200", "21.5", "fixed", "0.06", "deposits"),
    ("term_deposits_3m", "1-3m", "280", "21.5", "fixed", "0.17", "deposits"),
    ("wholesale_corp", "1-3m", "320", "23.0", "fixed", "0.17", "deposits"),
    ("savings_repricing", "3-6m", "300", "8.5", "float", "0.38", "deposits"),
    ("term_deposits_1y", "6-12m", "220", "23.4", "fixed", "0.75", "deposits"),
    ("term_borrowings", "1-3y", "100", "25.5", "fixed", "1.9", "deposits"),
    ("subordinated_debt", "5y+", "45", "26.0", "fixed", "7.0", "capital"),
)
# A single pay-fixed receiver interest-rate swap hedge. The engine decomposes it
# into a floating receive leg (asset, next 91-day reset) and a fixed pay leg
# (liability, 3-year tenor); both price into gap/EVE/duration, and their net
# accrual — notional × (91d floating index − pay fixed)/100 — is the swap carry
# that joins base NII.
_IRR_SWAP: dict[str, str] = {
    "category": "pay_fixed_irs",
    "notional_m": "120",
    "pay_rate_pct": "25.3",
    "receive_index": "91d_tbill",
    "tenor_years": "3",
    "direction": "pay_fixed",
    "receive_bucket": "1-3m",
    "receive_midpoint_years": "0.17",
    "pay_bucket": "1-3y",
    "pay_midpoint_years": "1.9",
}
# Base zero-coupon discount curve for PV/EVE, keyed by bucket midpoint (percent).
_IRR_BASE_CURVE: dict[str, str] = {
    "0.003y": "25.5",
    "0.014y": "25.4",
    "0.06y": "25.6",
    "0.17y": "25.8",
    "0.38y": "26.2",
    "0.75y": "27.0",
    "1.9y": "27.8",
    "4.0y": "28.9",
    "7.0y": "29.5",
}
# Six Basel IRRBB scenarios plus the base discount curve. Short-rate scenarios
# decay with tenor via decay_years; steepener/flattener use the standard
# e^(-t/4) short weight applied in the engine.
_IRR_STRESS: dict[str, dict[str, str]] = {
    "base_curve": _IRR_BASE_CURVE,
    "parallel_up_200": {"parallel_bp": "200"},
    "parallel_down_200": {"parallel_bp": "-200"},
    "short_up_250": {"short_bp": "250", "decay_years": "3"},
    "short_down_250": {"short_bp": "-250", "decay_years": "3"},
    "steepener": {"short_bp": "-65", "long_bp": "90"},
    "flattener": {"short_bp": "80", "long_bp": "-60"},
}
_IRR_SOURCE_FACTOR: dict[str, str] = {
    "securities": "securities",
    "loans": "loans",
    "interbank": "fixed",
    "deposits": "deposits",
    "capital": "capital",
}
# IRR positions are an independent decomposition of the rate-sensitive book;
# their reconciliation to balance-sheet securities and loans is checked within
# this tolerance to absorb 4-dp rounding of the individually scaled overlay rows.
_IRR_TIE_TOLERANCE = Decimal("1")

# FX net open positions per currency (latest-period canonical, GHS millions).
# (currency, net_ghs_m signed, spot_ghs, assets_ccy_m, liabilities_ccy_m,
#  net_derivatives_ccy_m, return_seed). By construction, for every row
#  assets_ccy - liabilities_ccy + net_derivatives_ccy = net_ccy and
#  net_ccy * spot_ghs = net_ghs at factor 1.0. Longs (USD, GBP, NGN, ZAR) sum to
#  +45M GHS-equivalent and shorts (EUR, XOF) to -12M, tying exactly to the
#  market_risk net_long_fx (45M) / net_short_fx (12M) facts at 2026-03.
_FX_POSITIONS: tuple[tuple[str, str, str, str, str, str, int], ...] = (
    ("USD", "30", "12.5", "5.0", "3.1", "0.5", 0),
    ("EUR", "-7", "14.0", "1.2", "2.0", "0.3", 1),
    ("GBP", "9", "15.0", "1.4", "1.0", "0.2", 2),
    ("NGN", "3", "0.008", "500", "150", "25", 3),
    ("ZAR", "3", "0.6", "8.0", "4.0", "1.0", 4),
    ("XOF", "-5", "0.02", "300", "600", "50", 5),
)
_FX_LONG_TOTAL_M = "45"
_FX_SHORT_TOTAL_M = "12"
# Three hedges: two effective (R^2 >= 0.80 and dollar-offset within 0.80-1.25)
# and one ineffective (R^2 0.72) so the IFRS 9 effectiveness screen shows both
# states. (hedge_id, instrument, pair, notional_ccy_m, rate, maturity_days,
#  mtm_ghs_m signed, prospective_r2, dollar_offset_ratio).
_FX_HEDGES: tuple[tuple[str, str, str, str, str, str, str, str, str], ...] = (
    ("FXH-USD-01", "forward", "USD/GHS", "20", "12.7", "90", "4.5", "0.94", "1.02"),
    ("FXH-EUR-02", "cross_currency_swap", "EUR/GHS", "6", "14.2", "365", "-2.1", "0.88", "0.91"),
    ("FXH-GBP-03", "option", "GBP/GHS", "4", "16.0", "180", "1.3", "0.72", "0.95"),
)
# Daily FX-return history parameters (250-day rolling window). Returns are
# generated by a deterministic, RNG-free closed form (drift + seasonal +
# per-currency idiosyncratic oscillation), with a high-volatility cedi-crisis
# sub-window (days 60-110) carrying a fat negative tail. See _fx_return_series.
_FX_RETURN_WINDOW = 250
_FX_CRISIS_START = 60
_FX_CRISIS_END = 110
# FX position sums are checked against their scaled canonical totals within this
# tolerance to absorb 4-dp rounding of the individually scaled per-currency rows.
_FX_TIE_TOLERANCE = Decimal("1")
_FX_CAPITAL_THRESHOLDS: dict[str, str] = {
    "fx_nop_single_limit_pct": "10",
    "fx_nop_aggregate_limit_pct": "20",
    "fx_var_confidence_pct": "99",
    "hedge_r2_min_pct": "80",
    "hedge_offset_low_pct": "80",
    "hedge_offset_high_pct": "125",
}
_FX_STRESS: dict[str, dict[str, str]] = {
    "mild_depreciation": {"ghs_usd_shock_pct": "10"},
    "severe_depreciation": {"ghs_usd_shock_pct": "20"},
    "cedi_crisis": {
        "ghs_usd_shock_pct": "30",
        "correlation_uplift": "0.2",
        "crisis_window_start": str(_FX_CRISIS_START),
        "crisis_window_end": str(_FX_CRISIS_END),
    },
}

# FTP matched-maturity transfer curve (rates, period-invariant — no scaling).
# (tenor_label, tenor_years, base_yield_pct, liquidity_premium_bps,
#  funding_spread_bps, expected_ftp_rate_pct). By construction each point obeys
#  ftp = base + (liquidity_bps + funding_bps) / 100; the seed asserts it.
_FTP_CURVE_POINTS: tuple[tuple[str, str, str, str, str, str], ...] = (
    ("overnight", "0.003", "25.0", "0", "40", "25.40"),
    ("91d", "0.25", "25.4", "0", "45", "25.85"),
    ("182d", "0.5", "26.1", "5", "45", "26.60"),
    ("1y", "1.0", "27.0", "10", "50", "27.60"),
    ("2y", "2.0", "27.8", "20", "50", "28.50"),
    ("3y", "3.0", "28.4", "30", "55", "29.25"),
    ("5y", "5.0", "28.9", "40", "55", "29.85"),
    ("10y", "10.0", "29.5", "50", "60", "30.60"),
)
# FTP product book (latest-period canonical, GHS millions). ``ftp_rate_pct``
# matches the curve point at ``tenor_years``. Source drives the per-period
# scaling factor and the balance-sheet reconciliation:
#   - loan products (corporate/sme/mortgage/retail) sum to 1,290M ≤ 1,400M gross
#     loans; the remaining 110M is the past-due and commercial book carried
#     centrally, so the FTP loan book stays inside the balance sheet.
#   - gov_securities_3y (620M) ties to the balance-sheet securities line
#     (bog_bills 260 + gog_bonds 360).
#   - deposit products sum to 1,740M ≤ 1,900M of interest-bearing deposits; the
#     remaining 160M is non-repricing head-office float.
# (product, category, balance_m, tenor_years, customer_rate_pct, ftp_rate_pct,
#  operating_cost_pct, expected_credit_loss_pct, capital_charge_pct, source).
_FTP_PRODUCTS: tuple[tuple[str, str, str, str, str, str, str, str, str, str], ...] = (
    ("corporate_5y", "asset", "560", "5.0", "32.0", "29.85", "0.5", "0.8", "0.15", "loans"),
    ("sme_2y", "asset", "280", "2.0", "33.0", "28.50", "0.7", "1.2", "0.11", "loans"),
    ("mortgage_10y", "asset", "200", "10.0", "30.0", "30.60", "0.4", "0.3", "0.05", "loans"),
    ("retail_1y", "asset", "250", "1.0", "34.0", "27.60", "0.9", "1.5", "0.08", "loans"),
    ("gov_securities_3y", "asset", "620", "3.0", "27.5", "29.25", "0.05", "0", "0", "securities"),
    ("current_accounts", "liability", "700", "0.003", "0.0", "25.40", "0.3", "0", "0", "deposits"),
    ("savings", "liability", "300", "0.5", "8.5", "26.60", "0.4", "0", "0", "deposits"),
    ("term_3m", "liability", "280", "0.25", "21.5", "25.85", "0.2", "0", "0", "deposits"),
    ("term_1y", "liability", "220", "1.0", "23.4", "27.60", "0.2", "0", "0", "deposits"),
    ("wholesale", "liability", "240", "0.25", "23.0", "25.85", "0.1", "0", "0", "deposits"),
)
# FTP branch network (latest-period canonical, GHS millions). Deposits scale with
# the deposits factor, loans with the loans factor. Σ branch deposits (1,500M) and
# Σ branch loans (1,180M) are the branch-booked subset of the 1,900M deposit and
# 1,400M loan books; head-office/treasury positions are booked centrally.
# (branch, deposits_m, loans_m).
_FTP_BRANCHES: tuple[tuple[str, str, str], ...] = (
    ("accra_main", "520", "380"),
    ("kumasi", "310", "260"),
    ("takoradi", "180", "210"),
    ("tema", "240", "150"),
    ("tamale", "130", "90"),
    ("cape_coast", "120", "90"),
)
# FTP non-maturity-deposit segments (latest-period canonical, GHS millions).
# Core receives a long-tenor FTP rate at the effective duration, volatile the
# overnight rate. Balances scale with the deposits factor. Σ NMD balances (1,000M)
# maps to the current-account and savings deposit products.
# (segment, balance_m, core_pct, volatile_pct, effective_duration_years).
_FTP_NMDS: tuple[tuple[str, str, str, str, str], ...] = (
    ("current_accounts", "700", "65", "35", "2.5"),
    ("savings", "300", "70", "30", "3.0"),
)
_FTP_SOURCE_FACTOR: dict[str, str] = {
    "loans": "loans",
    "securities": "securities",
    "deposits": "deposits",
}
# FTP balances are checked against their scaled canonical totals within this
# tolerance to absorb 4-dp rounding of the individually scaled rows.
_FTP_TIE_TOLERANCE = Decimal("1")
_FTP_CAPITAL_THRESHOLDS: dict[str, str] = {
    "ftp_target_roe_pct": "15",
    "ftp_min_product_margin_pct": "0",
    "ftp_liquidity_premium_max_bps": "50",
    "ftp_funding_spread_max_bps": "200",
    "nmd_core_min_pct": "60",
    "nmd_core_max_pct": "70",
}
_FTP_STRESS: dict[str, dict[str, str]] = {
    "rates_up_200": {"curve_shift_bp": "200"},
    "funding_stress": {"funding_spread_add_bps": "100"},
}

_LCR_OUTFLOW_RATES: dict[str, str] = {
    "retail_deposits_stable": "5",
    "retail_deposits_less_stable": "10",
    "wholesale_operational": "25",
    "wholesale_non_op_sme": "40",
    "wholesale_non_op_corporate": "100",
    "secured_funding_l1": "0",
    "term_borrowings_gt_1y": "0",
    "committed_retail": "10",
    "committed_corporate": "30",
}
_LCR_INFLOW_RATES: dict[str, str] = {
    "retail_loan_repayments": "50",
    "corporate_sme_repayments": "50",
    "interbank_maturing": "100",
}
_NSFR_ASF_WEIGHTS: dict[str, str] = {
    "capital_total": "100",
    "retail_deposits_stable": "95",
    "retail_deposits_less_stable": "90",
    "wholesale_operational": "50",
    "wholesale_non_op_sme": "90",
    "wholesale_non_op_corporate": "50",
    "secured_funding_l1": "0",
    "term_borrowings_gt_1y": "100",
}
_NSFR_RSF_WEIGHTS: dict[str, str] = {
    "cash_vault": "0",
    "bog_required_reserves": "0",
    "bog_excess_reserves": "0",
    "securities_bog_bills": "5",
    "securities_gog_bonds": "5",
    "corporate_unrated": "85",
    "sme_retail": "85",
    "retail_other": "85",
    "residential_mortgage": "65",
    "commercial_real_estate": "85",
    "past_due_90": "100",
    "other_assets": "100",
    "off_balance_commitments": "5",
}
_RISK_WEIGHTS: dict[str, str] = {
    "RW0": "0",
    "RW20": "20",
    "RW35": "35",
    "RW50": "50",
    "RW75": "75",
    "RW100": "100",
    "RW150": "150",
}
_CAPITAL_THRESHOLDS: dict[str, str] = {
    "car_min": "10",
    "car_early_warning": "10.5",
    "car_critical": "9",
    "cet1_min": "6.5",
    "tier1_min": "8",
    "leverage_min": "3",
    "lcr_min": "100",
    "lcr_amber_floor": "90",
    "nsfr_min": "100",
    "lcr_inflow_cap_pct": "75",
    "bia_alpha_pct": "15",
    "fx_charge_pct": "8",
    "rwa_multiplier": "1250",
    "tier2_gp_cap_pct_credit_rwa": "1.25",
    "eve_tier1_limit_pct": "15",
    "irr_nii_limit_pct": "10",
}
_LIQUIDITY_IDIOSYNCRATIC: dict[str, str] = {
    "runoff:retail_deposits_stable": "15",
    "runoff:retail_deposits_less_stable": "20",
    "runoff:wholesale_operational": "40",
    "runoff:wholesale_non_op_sme": "60",
    "runoff:wholesale_non_op_corporate": "100",
    "runoff:committed_retail": "20",
    "runoff:committed_corporate": "50",
    "inflow_multiplier": "0.75",
    "hqla_securities_haircut_pct": "0",
    "asf:retail_deposits_stable": "90",
    "asf:retail_deposits_less_stable": "80",
    "asf:wholesale_operational": "40",
    "asf:wholesale_non_op_sme": "80",
    "asf:wholesale_non_op_corporate": "40",
}
_LIQUIDITY_MARKET_WIDE: dict[str, str] = {
    "runoff:retail_deposits_stable": "7.5",
    "runoff:retail_deposits_less_stable": "15",
    "runoff:wholesale_operational": "30",
    "runoff:wholesale_non_op_sme": "50",
    "runoff:wholesale_non_op_corporate": "100",
    "runoff:committed_retail": "10",
    "runoff:committed_corporate": "40",
    "inflow_multiplier": "0.90",
    "hqla_securities_haircut_pct": "8",
    "rsf:securities_weight_override": "10",
}
_LIQUIDITY_COMBINED: dict[str, str] = {
    **{
        key: value
        for key, value in _LIQUIDITY_IDIOSYNCRATIC.items()
        if key.startswith(("runoff:", "asf:"))
    },
    "inflow_multiplier": "0.67",
    "hqla_securities_haircut_pct": "8",
    "rsf:securities_weight_override": "10",
}
_STRESS_SHOCKS: dict[str, dict[str, dict[str, str]]] = {
    "liquidity": {
        "idiosyncratic": _LIQUIDITY_IDIOSYNCRATIC,
        "market_wide": _LIQUIDITY_MARKET_WIDE,
        "combined": _LIQUIDITY_COMBINED,
    },
    "capital": {
        "mild": {
            "quarterly_rwa_growth_pct": "1.5",
            "quarterly_income_m": "16",
            "quarterly_credit_loss_m": "1.4",
            "fx_rwa_multiplier": "1.0",
        },
        "moderate": {
            "quarterly_rwa_growth_pct": "2.5",
            "quarterly_income_m": "12",
            "quarterly_credit_loss_m": "6.3",
            "fx_rwa_multiplier": "1.25",
        },
        "severe": {
            "quarterly_rwa_growth_pct": "4.0",
            "quarterly_income_m": "2",
            "quarterly_credit_loss_m": "30.8",
            "fx_rwa_multiplier": "1.6",
        },
    },
    "forecast": {
        "base": {
            "loan_growth_pct": "18",
            "deposit_growth_pct": "16",
            "nim_pct": "4.8",
            "cost_to_income_pct": "48",
            "credit_loss_rate_pct": "1.0",
            "fx_depreciation_pct": "0",
            "dividend_payout_pct": "30",
        },
        "adverse": {
            "loan_growth_pct": "8",
            "deposit_growth_pct": "6",
            "nim_pct": "4.2",
            "cost_to_income_pct": "54",
            "credit_loss_rate_pct": "1.5",
            "fx_depreciation_pct": "15",
            "dividend_payout_pct": "0",
        },
        "severely_adverse": {
            "loan_growth_pct": "-2",
            "deposit_growth_pct": "-8",
            "nim_pct": "3.6",
            "cost_to_income_pct": "60",
            "credit_loss_rate_pct": "2.0",
            "fx_depreciation_pct": "40",
            "dividend_payout_pct": "0",
        },
    },
    "irr": _IRR_STRESS,
}
_PARAMETER_MODELS: tuple[type[RegulatoryParameterMixin], ...] = (
    ParamLcrRunoffRate,
    ParamNsfrWeight,
    ParamRiskWeight,
    ParamStressShock,
    ParamCapitalThreshold,
)


class SampleBankSeedError(RuntimeError):
    """Raised when the generated seed data fails a deterministic tie-out check."""


@dataclass(frozen=True)
class SeedSummary:
    bank_id: UUID
    periods: int
    fact_count: int
    param_count: int


@dataclass(frozen=True)
class _PeriodFactors:
    securities: Decimal
    loans: Decimal
    deposits: Decimal
    capital: Decimal
    fx: Decimal


def seed_sample_bank(session: Session) -> SeedSummary:
    """Idempotently seed Sample Bank Ltd for the demo organization."""
    _set_tenant_context(session, DEMO_ORG_ID)
    _ensure_organization(session, DEMO_ORG_ID, DEMO_ORG_NAME)
    _ensure_demo_user(session)
    _delete_existing_seed(session)

    session.add(
        Bank(
            id=SAMPLE_BANK_ID,
            organization_id=DEMO_ORG_ID,
            name="Sample Bank Ltd",
            short_name="Sample Bank",
            currency=CURRENCY,
            jurisdiction_code=JURISDICTION_CODE,
            license_type="universal",
        )
    )
    periods = _build_reporting_periods()
    session.add_all(periods)
    session.flush()

    fact_count = 0
    for index, period in enumerate(periods):
        facts = _build_period_facts(period, index)
        _validate_period_facts(period, facts, index)
        session.add_all(facts)
        fact_count += len(facts)

    param_count = _seed_parameters(session)
    session.flush()

    _set_tenant_context(session, ISOLATED_ORG_ID)
    _ensure_organization(session, ISOLATED_ORG_ID, ISOLATED_ORG_NAME)
    _set_tenant_context(session, DEMO_ORG_ID)

    return SeedSummary(
        bank_id=SAMPLE_BANK_ID,
        periods=len(periods),
        fact_count=fact_count,
        param_count=param_count,
    )


def _set_tenant_context(session: Session, organization_id: UUID) -> None:
    if session.get_bind().dialect.name != "postgresql":
        return
    session.execute(
        sql_text("SELECT set_config('app.organization_id', :organization_id, true)"),
        {"organization_id": str(organization_id)},
    )


def _ensure_organization(session: Session, organization_id: UUID, name: str) -> None:
    exists = session.scalar(select(Organization.id).where(Organization.id == organization_id))
    if exists is None:
        session.add(Organization(id=organization_id, name=name))
        session.flush()


def _ensure_demo_user(session: Session) -> None:
    exists = session.scalar(
        select(User.id).where(User.id == DEMO_USER_ID, User.organization_id == DEMO_ORG_ID)
    )
    if exists is None:
        session.add(
            User(
                id=DEMO_USER_ID,
                organization_id=DEMO_ORG_ID,
                email=DEMO_USER_EMAIL,
                display_name=DEMO_USER_NAME,
                is_active=True,
            )
        )
        session.flush()


# Runtime-populated tables that reference the Sample Bank via bank_id, ordered
# leaf -> root so foreign keys are satisfied when re-seeding a live database.
# The seed owns a full reset of the demo bank, so any derived runs / ingested
# data for it are cleared before re-insertion. Tables are swept defensively:
# a table absent from the current schema (e.g. a partial SQLite test DB) is
# skipped. regulatory_runs cascades to its metric/line-item/validation children.
_DEPENDENT_TABLES: tuple[str, ...] = (
    "regulatory_runs",
    "canonical_position_snapshots",
    "canonical_positions",
    "canonical_products",
    "canonical_counterparties",
    "canonical_gl_accounts",
    "translation_failures",
    "lineage_records",
    "mapping_configs",
    "ingestion_batches",
)


def _delete_bank_dependents(session: Session) -> None:
    inspector = sql_inspect(session.get_bind())
    existing = set(inspector.get_table_names())
    params = {"bank_id": str(SAMPLE_BANK_ID), "organization_id": str(DEMO_ORG_ID)}
    for table in _DEPENDENT_TABLES:
        if table not in existing:
            continue
        columns = {column["name"] for column in inspector.get_columns(table)}
        if "bank_id" in columns:
            where = "WHERE bank_id = :bank_id AND organization_id = :organization_id"
        elif "ingestion_batch_id" in columns:
            # Rows keyed to a batch rather than the bank (e.g. lineage_records);
            # cleared via their parent batch, which is deleted after this table.
            where = (
                "WHERE ingestion_batch_id IN "
                "(SELECT id FROM ingestion_batches WHERE bank_id = :bank_id)"
            )
        else:
            continue
        session.execute(
            sql_text(f"DELETE FROM {table} {where}"),  # noqa: S608 - fixed allowlist
            params,
        )


def _delete_existing_seed(session: Session) -> None:
    _delete_bank_dependents(session)
    session.execute(
        delete(BankFinancialFact).where(
            BankFinancialFact.bank_id == SAMPLE_BANK_ID,
            BankFinancialFact.organization_id == DEMO_ORG_ID,
        )
    )
    session.execute(
        delete(BankReportingPeriod).where(
            BankReportingPeriod.bank_id == SAMPLE_BANK_ID,
            BankReportingPeriod.organization_id == DEMO_ORG_ID,
        )
    )
    session.execute(
        delete(Bank).where(Bank.id == SAMPLE_BANK_ID, Bank.organization_id == DEMO_ORG_ID)
    )
    for model in _PARAMETER_MODELS:
        session.execute(
            delete(model).where(
                model.organization_id == DEMO_ORG_ID,
                model.jurisdiction_code == JURISDICTION_CODE,
                model.approved_by.in_((APPROVED_BY, FX_APPROVED_BY, FTP_APPROVED_BY)),
            )
        )


def _build_reporting_periods() -> list[BankReportingPeriod]:
    periods: list[BankReportingPeriod] = []
    for index in range(PERIOD_COUNT):
        year, month = _period_month(index)
        periods.append(
            BankReportingPeriod(
                organization_id=DEMO_ORG_ID,
                bank_id=SAMPLE_BANK_ID,
                period_start=date(year, month, 1),
                period_end=date(year, month, monthrange(year, month)[1]),
                label=f"{year:04d}-{month:02d}",
                status="open" if index == PERIOD_COUNT - 1 else "closed",
            )
        )
    return periods


def _period_month(index: int) -> tuple[int, int]:
    month_ordinal = FIRST_PERIOD_MONTH - 1 + index
    return FIRST_PERIOD_YEAR + month_ordinal // 12, month_ordinal % 12 + 1


def _factors(index: int) -> _PeriodFactors:
    return _PeriodFactors(
        securities=_factor(_SECURITIES_FACTOR_START, index),
        loans=_factor(_LOANS_FACTOR_START, index),
        deposits=_factor(_DEPOSITS_FACTOR_START, index),
        capital=_factor(_CAPITAL_FACTOR_START, index),
        fx=_factor(_FX_FACTOR_START, index),
    )


def _factor(start: Decimal, index: int) -> Decimal:
    return start + (_ONE - start) * Decimal(index) / Decimal(PERIOD_COUNT - 1)


def _amount(millions: str, factor: Decimal = _ONE) -> Decimal:
    return (Decimal(millions) * MILLION * factor).quantize(MONEY)


def _total(amounts: Iterable[Decimal]) -> Decimal:
    return sum(amounts, _ZERO)


def _fact(
    period: BankReportingPeriod,
    fact_group: str,
    category: str,
    amount: Decimal,
    **extra: Any,
) -> BankFinancialFact:
    return BankFinancialFact(
        organization_id=DEMO_ORG_ID,
        bank_id=SAMPLE_BANK_ID,
        reporting_period_id=period.id,
        fact_group=fact_group,
        category=category,
        amount=amount,
        currency=CURRENCY,
        **extra,
    )


def _build_period_facts(period: BankReportingPeriod, index: int) -> list[BankFinancialFact]:
    factors = _factors(index)
    loan_rows = [
        (category, _amount(millions, factors.loans), code)
        for category, millions, code in _LOAN_EXPOSURES_M
    ]
    securities_rows = [
        (category, _amount(millions, factors.securities), from_cash)
        for category, millions, from_cash in _SECURITIES_M
    ]
    capital_rows = [
        (category, _amount(millions, factors.capital), tier, deduction)
        for category, millions, tier, deduction in _CAPITAL_COMPONENTS_M
    ]
    capital_total = _total(
        amount if not deduction else -amount for _, amount, _, deduction in capital_rows
    )

    facts = _balance_sheet_facts(
        period,
        factors,
        loans_gross=_total(amount for _, amount, _ in loan_rows),
        securities_amounts=(securities_rows[0][1], securities_rows[1][1]),
        capital_total=capital_total,
    )
    facts.extend(
        _fact(period, "loan_exposure", category, amount, risk_weight_code=code)
        for category, amount, code in loan_rows
    )
    facts.extend(
        _fact(
            period,
            "securities",
            category,
            amount,
            hqla_level="L1",
            risk_weight_code="RW0",
            attributes={"source": "cash"} if from_cash else {},
        )
        for category, amount, from_cash in securities_rows
    )
    facts.extend(
        _fact(
            period,
            "off_balance",
            category,
            _amount(millions, factors.loans),
            ccf_pct=Decimal(ccf),
            risk_weight_code=code,
        )
        for category, millions, ccf, code in _OFF_BALANCE_M
    )
    facts.extend(
        _fact(
            period, "lcr_inflow", category, _amount(millions, factors.loans), rate_pct=Decimal(rate)
        )
        for category, millions, rate in _LCR_INFLOWS_M
    )
    facts.extend(
        _fact(period, "market_risk", category, _amount(millions))
        for category, millions in _MARKET_RISK_M
    )
    facts.extend(
        _fact(period, "operational_income", category, _amount(millions), income_year=year)
        for category, millions, year in _OPERATIONAL_INCOME_M
    )
    facts.extend(
        _fact(
            period,
            "capital_component",
            category,
            amount,
            capital_tier=tier,
            is_deduction=deduction,
        )
        for category, amount, tier, deduction in capital_rows
    )
    facts.extend(_irr_facts(period, factors))
    facts.extend(_fx_facts(period, factors))
    facts.extend(_ftp_facts(period, factors))
    return facts


def _irr_factor(source: str, factors: _PeriodFactors) -> Decimal:
    key = _IRR_SOURCE_FACTOR[source]
    return {
        "securities": factors.securities,
        "loans": factors.loans,
        "deposits": factors.deposits,
        "capital": factors.capital,
        "fixed": _ONE,
    }[key]


def _irr_position_fact(
    period: BankReportingPeriod,
    side: str,
    row: tuple[str, str, str, str, str, str, str],
    factors: _PeriodFactors,
) -> BankFinancialFact:
    category, bucket, millions, rate, fixed_or_float, midpoint, source = row
    return _fact(
        period,
        "irr_position",
        category,
        _amount(millions, _irr_factor(source, factors)),
        attributes={
            "side": side,
            "bucket": bucket,
            "fixed_or_float": fixed_or_float,
            "rate_pct": rate,
            "midpoint_years": midpoint,
            "source": source,
        },
    )


def _irr_swap_fact(period: BankReportingPeriod, factors: _PeriodFactors) -> BankFinancialFact:
    notional = _amount(_IRR_SWAP["notional_m"], factors.loans)
    return _fact(
        period,
        "irr_swap",
        _IRR_SWAP["category"],
        notional,
        attributes={
            "notional": str(notional),
            "pay_rate_pct": _IRR_SWAP["pay_rate_pct"],
            "receive_index": _IRR_SWAP["receive_index"],
            "tenor_years": _IRR_SWAP["tenor_years"],
            "direction": _IRR_SWAP["direction"],
            "receive_bucket": _IRR_SWAP["receive_bucket"],
            "receive_midpoint_years": _IRR_SWAP["receive_midpoint_years"],
            "pay_bucket": _IRR_SWAP["pay_bucket"],
            "pay_midpoint_years": _IRR_SWAP["pay_midpoint_years"],
        },
    )


def _irr_facts(period: BankReportingPeriod, factors: _PeriodFactors) -> list[BankFinancialFact]:
    facts = [_irr_position_fact(period, "asset", row, factors) for row in _IRR_ASSET_POSITIONS]
    facts.extend(
        _irr_position_fact(period, "liability", row, factors) for row in _IRR_LIABILITY_POSITIONS
    )
    facts.append(_irr_swap_fact(period, factors))
    return facts


def _fx_return_series(seed_index: int) -> list[float]:
    """Deterministic 250-day FX return series (no RNG state).

    Each day's return is a closed form of the day index and a per-currency seed:
    a mild -0.02%/day cedi-depreciation drift, a seasonal cycle and a currency
    idiosyncratic oscillation. Days 60-110 form the 2022-2023 cedi-crisis
    sub-window, replacing the calm regime with high-volatility swings plus a
    periodic sharp negative tail so the stressed-VaR window bites.
    """
    phase = 0.6 + 0.17 * seed_index
    amplitude = 0.0035 + 0.0004 * seed_index
    series: list[float] = []
    for day in range(_FX_RETURN_WINDOW):
        step = day + 1
        drift = -0.0002
        idiosyncratic = amplitude * math.sin(phase * step + seed_index)
        if _FX_CRISIS_START <= day <= _FX_CRISIS_END:
            crisis = 0.022 * math.sin(1.27 * step + 0.5 * seed_index)
            if (day - _FX_CRISIS_START) % 9 == 0:
                crisis -= 0.017
            value = drift + crisis + 0.4 * idiosyncratic
        else:
            seasonal = 0.0011 * math.sin(2.0 * math.pi * step / 63.0)
            value = drift + seasonal + idiosyncratic
        series.append(round(value, 6))
    return series


def _fx_position_fact(
    period: BankReportingPeriod,
    row: tuple[str, str, str, str, str, str, int],
    factors: _PeriodFactors,
) -> BankFinancialFact:
    currency, net_ghs_m, spot, assets_m, liabilities_m, derivatives_m, _seed = row
    net_ghs = _amount(net_ghs_m, factors.fx)
    assets_ccy = _amount(assets_m, factors.fx)
    liabilities_ccy = _amount(liabilities_m, factors.fx)
    derivatives_ccy = _amount(derivatives_m, factors.fx)
    net_ccy = assets_ccy - liabilities_ccy + derivatives_ccy
    return _fact(
        period,
        "fx_position",
        currency,
        net_ghs,
        attributes={
            "currency": currency,
            "side": "long" if net_ghs >= _ZERO else "short",
            "spot_ghs": spot,
            "net_ccy": str(net_ccy),
            "assets_ccy": str(assets_ccy),
            "liabilities_ccy": str(liabilities_ccy),
            "net_derivatives_ccy": str(derivatives_ccy),
            "net_ghs": str(net_ghs),
        },
    )


def _fx_return_fact(
    period: BankReportingPeriod, row: tuple[str, str, str, str, str, str, int]
) -> BankFinancialFact:
    currency = row[0]
    seed_index = row[6]
    return _fact(
        period,
        "fx_return_history",
        currency,
        Decimal(_FX_RETURN_WINDOW),
        attributes={"currency": currency, "returns": _fx_return_series(seed_index)},
    )


def _fx_hedge_fact(
    period: BankReportingPeriod,
    row: tuple[str, str, str, str, str, str, str, str, str],
    factors: _PeriodFactors,
) -> BankFinancialFact:
    hedge_id, instrument, pair, notional_m, rate, maturity_days, mtm_m, r2, offset = row
    mtm = _amount(mtm_m, factors.fx)
    notional = _amount(notional_m, factors.fx)
    return _fact(
        period,
        "fx_hedge",
        hedge_id,
        mtm,
        attributes={
            "hedge_id": hedge_id,
            "instrument": instrument,
            "pair": pair,
            "notional_ccy": str(notional),
            "rate": rate,
            "maturity_days": maturity_days,
            "mtm_ghs": str(mtm),
            "prospective_r2": r2,
            "dollar_offset_ratio": offset,
        },
    )


def _fx_facts(period: BankReportingPeriod, factors: _PeriodFactors) -> list[BankFinancialFact]:
    facts = [_fx_position_fact(period, row, factors) for row in _FX_POSITIONS]
    facts.extend(_fx_return_fact(period, row) for row in _FX_POSITIONS)
    facts.extend(_fx_hedge_fact(period, row, factors) for row in _FX_HEDGES)
    return facts


def _ftp_source_factor(source: str, factors: _PeriodFactors) -> Decimal:
    return {
        "loans": factors.loans,
        "securities": factors.securities,
        "deposits": factors.deposits,
    }[_FTP_SOURCE_FACTOR[source]]


def _ftp_curve_fact(
    period: BankReportingPeriod, row: tuple[str, str, str, str, str, str]
) -> BankFinancialFact:
    label, tenor, base, liquidity_bps, funding_bps, expected_ftp = row
    ftp = Decimal(base) + (Decimal(liquidity_bps) + Decimal(funding_bps)) / _HUNDRED
    if ftp != Decimal(expected_ftp):
        raise SampleBankSeedError(
            f"FTP curve point {label}: derived FTP {ftp}% != expected {expected_ftp}%."
        )
    return _fact(
        period,
        "ftp_curve_point",
        label,
        ftp,
        attributes={
            "tenor_label": label,
            "tenor_years": tenor,
            "base_yield_pct": base,
            "liquidity_premium_bps": liquidity_bps,
            "funding_spread_bps": funding_bps,
            "ftp_rate_pct": str(ftp),
        },
    )


def _ftp_product_fact(
    period: BankReportingPeriod,
    row: tuple[str, str, str, str, str, str, str, str, str, str],
    factors: _PeriodFactors,
) -> BankFinancialFact:
    product, category, balance_m, tenor, customer, ftp, opex, ecl, cap, source = row
    balance = _amount(balance_m, _ftp_source_factor(source, factors))
    # Assets earn customer - ftp - operating cost - expected credit loss - capital
    # charge; deposits earn the FTP credit ftp - customer - operating cost.
    if category == "asset":
        net_margin = Decimal(customer) - Decimal(ftp) - Decimal(opex) - Decimal(ecl) - Decimal(cap)
    else:
        net_margin = Decimal(ftp) - Decimal(customer) - Decimal(opex)
    return _fact(
        period,
        "ftp_product",
        product,
        balance,
        attributes={
            "product": product,
            "category": category,
            "balance_ghs": str(balance),
            "tenor_years": tenor,
            "customer_rate_pct": customer,
            "ftp_rate_pct": ftp,
            "operating_cost_pct": opex,
            "expected_credit_loss_pct": ecl,
            "capital_charge_pct": cap,
            "net_margin_pct": str(net_margin),
            "source": source,
        },
    )


def _ftp_branch_fact(
    period: BankReportingPeriod, row: tuple[str, str, str], factors: _PeriodFactors
) -> BankFinancialFact:
    branch, deposits_m, loans_m = row
    deposits = _amount(deposits_m, factors.deposits)
    loans = _amount(loans_m, factors.loans)
    return _fact(
        period,
        "ftp_branch",
        branch,
        deposits,
        attributes={
            "branch": branch,
            "deposits_ghs": str(deposits),
            "loans_ghs": str(loans),
        },
    )


def _ftp_nmd_fact(
    period: BankReportingPeriod, row: tuple[str, str, str, str, str], factors: _PeriodFactors
) -> BankFinancialFact:
    segment, balance_m, core_pct, volatile_pct, effective_duration = row
    balance = _amount(balance_m, factors.deposits)
    return _fact(
        period,
        "ftp_nmd",
        segment,
        balance,
        attributes={
            "segment": segment,
            "balance_ghs": str(balance),
            "core_pct": core_pct,
            "volatile_pct": volatile_pct,
            "effective_duration_years": effective_duration,
        },
    )


def _ftp_facts(period: BankReportingPeriod, factors: _PeriodFactors) -> list[BankFinancialFact]:
    facts = [_ftp_curve_fact(period, row) for row in _FTP_CURVE_POINTS]
    facts.extend(_ftp_product_fact(period, row, factors) for row in _FTP_PRODUCTS)
    facts.extend(_ftp_branch_fact(period, row, factors) for row in _FTP_BRANCHES)
    facts.extend(_ftp_nmd_fact(period, row, factors) for row in _FTP_NMDS)
    return facts


def _balance_sheet_facts(
    period: BankReportingPeriod,
    factors: _PeriodFactors,
    loans_gross: Decimal,
    securities_amounts: tuple[Decimal, Decimal],
    capital_total: Decimal,
) -> list[BankFinancialFact]:
    bills, bonds = securities_amounts
    fixed_rows = [(category, _amount(millions)) for category, millions in _FIXED_ASSETS_M]
    deposit_rows = [
        (category, _amount(millions, factors.deposits)) for category, millions in _DEPOSITS_M
    ]
    secured_funding = _amount(_SECURED_FUNDING_M)
    term_borrowings = _amount(_TERM_BORROWINGS_M)
    other_assets_floor = _amount(_OTHER_ASSETS_FLOOR_M)

    assets_before_plug = _total(amount for _, amount in fixed_rows) + bills + bonds + loans_gross
    liabilities_and_equity = (
        _total(amount for _, amount in deposit_rows)
        + secured_funding
        + term_borrowings
        + capital_total
    )
    other_assets = liabilities_and_equity - assets_before_plug
    if other_assets < other_assets_floor:
        term_borrowings += other_assets_floor - other_assets
        other_assets = other_assets_floor

    facts = [
        _fact(period, "balance_sheet", category, amount, attributes=_side("asset"))
        for category, amount in fixed_rows
    ]
    facts.append(
        _fact(period, "balance_sheet", "securities_bog_bills", bills, attributes=_side("asset"))
    )
    facts.append(
        _fact(period, "balance_sheet", "securities_gog_bonds", bonds, attributes=_side("asset"))
    )
    facts.append(
        _fact(period, "balance_sheet", "loans_gross", loans_gross, attributes=_side("asset"))
    )
    facts.append(
        _fact(period, "balance_sheet", "other_assets", other_assets, attributes=_side("asset"))
    )
    facts.extend(
        _fact(period, "balance_sheet", category, amount, attributes=_side("liability"))
        for category, amount in deposit_rows
    )
    facts.append(
        _fact(
            period,
            "balance_sheet",
            "secured_funding_l1",
            secured_funding,
            attributes=_side("liability"),
        )
    )
    facts.append(
        _fact(
            period,
            "balance_sheet",
            "term_borrowings_gt_1y",
            term_borrowings,
            attributes=_side("liability"),
        )
    )
    facts.append(
        _fact(
            period,
            "balance_sheet",
            "capital_total",
            capital_total,
            attributes=_side("equity"),
        )
    )
    return facts


def _side(side: str) -> dict[str, Any]:
    return {"side": side}


def _validate_period_facts(
    period: BankReportingPeriod, facts: list[BankFinancialFact], index: int
) -> None:
    balance = [fact for fact in facts if fact.fact_group == "balance_sheet"]
    assets_total = _total(fact.amount for fact in balance if fact.attributes.get("side") == "asset")
    funding_total = _total(
        fact.amount for fact in balance if fact.attributes.get("side") in ("liability", "equity")
    )
    if assets_total != funding_total:
        raise SampleBankSeedError(
            f"Period {period.label}: assets {assets_total} != liabilities+equity {funding_total}."
        )

    loans_gross = next(fact.amount for fact in balance if fact.category == "loans_gross")
    exposure_total = _total(fact.amount for fact in facts if fact.fact_group == "loan_exposure")
    if exposure_total != loans_gross:
        raise SampleBankSeedError(
            f"Period {period.label}: loan exposures {exposure_total} != loans_gross {loans_gross}."
        )

    securities_balance = _total(
        fact.amount
        for fact in balance
        if fact.category in ("securities_bog_bills", "securities_gog_bonds")
    )
    securities_group = _total(
        fact.amount
        for fact in facts
        if fact.fact_group == "securities" and fact.attributes.get("source") != "cash"
    )
    if securities_group != securities_balance:
        raise SampleBankSeedError(
            f"Period {period.label}: securities facts {securities_group} != "
            f"balance-sheet securities {securities_balance}."
        )

    _validate_irr_positions(period, facts, balance, loans_gross, securities_balance)
    _validate_fx_positions(period, facts, index)
    _validate_ftp_facts(period, facts, balance, loans_gross, securities_balance)


def _validate_fx_positions(
    period: BankReportingPeriod, facts: list[BankFinancialFact], index: int
) -> None:
    positions = [fact for fact in facts if fact.fact_group == "fx_position"]
    long_sum = _total(fact.amount for fact in positions if fact.amount >= _ZERO)
    short_sum = _total(-fact.amount for fact in positions if fact.amount < _ZERO)

    factor = _factor(_FX_FACTOR_START, index)
    expected_long = _amount(_FX_LONG_TOTAL_M, factor)
    expected_short = _amount(_FX_SHORT_TOTAL_M, factor)
    if (
        abs(long_sum - expected_long) > _FX_TIE_TOLERANCE
        or abs(short_sum - expected_short) > _FX_TIE_TOLERANCE
    ):
        raise SampleBankSeedError(
            f"Period {period.label}: FX net positions (long {long_sum}, short {short_sum}) "
            f"do not match the scaled canonical totals (long {expected_long}, short "
            f"{expected_short})."
        )

    # At the latest period (factor 1.0) the per-currency FX book must tie exactly
    # to the aggregate market_risk net_long_fx / net_short_fx facts.
    if index == PERIOD_COUNT - 1:
        net_long = next(
            fact.amount
            for fact in facts
            if fact.fact_group == "market_risk" and fact.category == "net_long_fx"
        )
        net_short = next(
            fact.amount
            for fact in facts
            if fact.fact_group == "market_risk" and fact.category == "net_short_fx"
        )
        if long_sum != net_long or short_sum != net_short:
            raise SampleBankSeedError(
                f"Period {period.label}: FX net positions (long {long_sum}, short {short_sum}) "
                f"do not tie to market_risk (net_long_fx {net_long}, net_short_fx {net_short})."
            )


def _validate_irr_positions(
    period: BankReportingPeriod,
    facts: list[BankFinancialFact],
    balance: list[BankFinancialFact],
    loans_gross: Decimal,
    securities_balance: Decimal,
) -> None:
    positions = [fact for fact in facts if fact.fact_group == "irr_position"]
    securities_sourced = _total(
        fact.amount for fact in positions if fact.attributes.get("source") == "securities"
    )
    if abs(securities_sourced - securities_balance) > _IRR_TIE_TOLERANCE:
        raise SampleBankSeedError(
            f"Period {period.label}: securities-sourced IRR positions {securities_sourced} != "
            f"balance-sheet securities {securities_balance}."
        )

    loans_sourced = _total(
        fact.amount
        for fact in positions
        if fact.attributes.get("source") == "loans" and fact.attributes.get("side") == "asset"
    )
    if loans_sourced > loans_gross + _IRR_TIE_TOLERANCE:
        raise SampleBankSeedError(
            f"Period {period.label}: loan-sourced IRR positions {loans_sourced} exceed "
            f"gross loans {loans_gross}."
        )

    interbank_sourced = _total(
        fact.amount for fact in positions if fact.attributes.get("source") == "interbank"
    )
    excess_reserves = _total(
        fact.amount for fact in balance if fact.category == "bog_excess_reserves"
    )
    if interbank_sourced > excess_reserves + _IRR_TIE_TOLERANCE:
        raise SampleBankSeedError(
            f"Period {period.label}: interbank IRR placements {interbank_sourced} exceed "
            f"BoG excess reserves {excess_reserves}."
        )


def _validate_ftp_facts(
    period: BankReportingPeriod,
    facts: list[BankFinancialFact],
    balance: list[BankFinancialFact],
    loans_gross: Decimal,
    securities_balance: Decimal,
) -> None:
    curve = [fact for fact in facts if fact.fact_group == "ftp_curve_point"]
    curve_ftp = {
        fact.attributes["tenor_years"]: Decimal(str(fact.attributes["ftp_rate_pct"]))
        for fact in curve
    }
    products = [fact for fact in facts if fact.fact_group == "ftp_product"]

    # Each product's FTP transfer rate must equal the curve rate at its tenor.
    for product in products:
        tenor = product.attributes["tenor_years"]
        product_ftp = Decimal(str(product.attributes["ftp_rate_pct"]))
        curve_rate = curve_ftp.get(tenor)
        if curve_rate is None or curve_rate != product_ftp:
            raise SampleBankSeedError(
                f"Period {period.label}: FTP product {product.category} FTP rate {product_ftp}% "
                f"does not match the curve rate {curve_rate}% at tenor {tenor}y."
            )

    loan_products = _total(
        product.amount for product in products if product.attributes.get("source") == "loans"
    )
    if loan_products > loans_gross + _FTP_TIE_TOLERANCE:
        raise SampleBankSeedError(
            f"Period {period.label}: FTP loan products {loan_products} exceed gross loans "
            f"{loans_gross}."
        )

    gov_securities = _total(
        product.amount for product in products if product.attributes.get("source") == "securities"
    )
    if abs(gov_securities - securities_balance) > _FTP_TIE_TOLERANCE:
        raise SampleBankSeedError(
            f"Period {period.label}: FTP government-securities product {gov_securities} does not "
            f"tie to balance-sheet securities {securities_balance}."
        )

    deposit_categories = {category for category, _ in _DEPOSITS_M}
    total_deposits = _total(fact.amount for fact in balance if fact.category in deposit_categories)
    deposit_products = _total(
        product.amount for product in products if product.attributes.get("category") == "liability"
    )
    if deposit_products > total_deposits + _FTP_TIE_TOLERANCE:
        raise SampleBankSeedError(
            f"Period {period.label}: FTP deposit products {deposit_products} exceed total "
            f"deposits {total_deposits}."
        )

    branches = [fact for fact in facts if fact.fact_group == "ftp_branch"]
    branch_deposits = _total(fact.amount for fact in branches)
    branch_loans = _total(Decimal(str(fact.attributes["loans_ghs"])) for fact in branches)
    if branch_deposits > total_deposits + _FTP_TIE_TOLERANCE:
        raise SampleBankSeedError(
            f"Period {period.label}: FTP branch deposits {branch_deposits} exceed total "
            f"deposits {total_deposits}."
        )
    if branch_loans > loans_gross + _FTP_TIE_TOLERANCE:
        raise SampleBankSeedError(
            f"Period {period.label}: FTP branch loans {branch_loans} exceed gross loans "
            f"{loans_gross}."
        )

    nmd_total = _total(fact.amount for fact in facts if fact.fact_group == "ftp_nmd")
    if nmd_total > total_deposits + _FTP_TIE_TOLERANCE:
        raise SampleBankSeedError(
            f"Period {period.label}: FTP non-maturity deposits {nmd_total} exceed total "
            f"deposits {total_deposits}."
        )


def _parameter_scope() -> dict[str, Any]:
    return {
        "organization_id": DEMO_ORG_ID,
        "jurisdiction_code": JURISDICTION_CODE,
        "effective_from": EFFECTIVE_FROM,
        "effective_to": None,
        "approved_by": APPROVED_BY,
        "approval_timestamp": APPROVAL_TIMESTAMP,
    }


def _seed_parameters(session: Session) -> int:
    scope = _parameter_scope()
    rows: list[Base] = []
    rows.extend(
        ParamLcrRunoffRate(
            flow_direction="outflow", category=category, rate_pct=Decimal(rate), **scope
        )
        for category, rate in _LCR_OUTFLOW_RATES.items()
    )
    rows.extend(
        ParamLcrRunoffRate(
            flow_direction="inflow", category=category, rate_pct=Decimal(rate), **scope
        )
        for category, rate in _LCR_INFLOW_RATES.items()
    )
    rows.extend(
        ParamNsfrWeight(side="asf", category=category, weight_pct=Decimal(weight), **scope)
        for category, weight in _NSFR_ASF_WEIGHTS.items()
    )
    rows.extend(
        ParamNsfrWeight(side="rsf", category=category, weight_pct=Decimal(weight), **scope)
        for category, weight in _NSFR_RSF_WEIGHTS.items()
    )
    rows.extend(
        ParamRiskWeight(risk_weight_code=code, weight_pct=Decimal(weight), **scope)
        for code, weight in _RISK_WEIGHTS.items()
    )
    rows.extend(
        ParamCapitalThreshold(threshold_code=code, value_pct=Decimal(value), **scope)
        for code, value in _CAPITAL_THRESHOLDS.items()
    )
    for module, scenarios in _STRESS_SHOCKS.items():
        for scenario_code, shocks in scenarios.items():
            rows.extend(
                ParamStressShock(
                    module=module,
                    scenario_code=scenario_code,
                    shock_key=shock_key,
                    shock_value=Decimal(value),
                    **scope,
                )
                for shock_key, value in shocks.items()
            )

    # FX-specific parameters carry the dedicated BoG FX baseline approver.
    fx_scope = {**scope, "approved_by": FX_APPROVED_BY}
    rows.extend(
        ParamCapitalThreshold(threshold_code=code, value_pct=Decimal(value), **fx_scope)
        for code, value in _FX_CAPITAL_THRESHOLDS.items()
    )
    for scenario_code, shocks in _FX_STRESS.items():
        rows.extend(
            ParamStressShock(
                module="fx",
                scenario_code=scenario_code,
                shock_key=shock_key,
                shock_value=Decimal(value),
                **fx_scope,
            )
            for shock_key, value in shocks.items()
        )

    # FTP-specific parameters carry the dedicated BoG FTP baseline approver.
    ftp_scope = {**scope, "approved_by": FTP_APPROVED_BY}
    rows.extend(
        ParamCapitalThreshold(threshold_code=code, value_pct=Decimal(value), **ftp_scope)
        for code, value in _FTP_CAPITAL_THRESHOLDS.items()
    )
    for scenario_code, shocks in _FTP_STRESS.items():
        rows.extend(
            ParamStressShock(
                module="ftp",
                scenario_code=scenario_code,
                shock_key=shock_key,
                shock_value=Decimal(value),
                **ftp_scope,
            )
            for shock_key, value in shocks.items()
        )

    session.add_all(rows)
    return len(rows)
