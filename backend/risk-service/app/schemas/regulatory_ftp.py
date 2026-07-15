from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.banks import BankRead, BankReportingPeriodRead
from app.schemas.regulatory_liquidity import RegulatoryValidationSeverity

type FtpStatus = Literal["green", "amber", "red"]
type FtpCategory = Literal["asset", "liability"]
type FtpScenarioCode = Literal["baseline", "rates_up_200", "funding_stress"]


class ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FtpScenarioBatchCreate(ClosedModel):
    reporting_period_id: UUID


class FtpCurvePointRead(ClosedModel):
    tenor_label: str
    tenor_years: Decimal
    base_yield_pct: Decimal
    liquidity_premium_bps: Decimal
    funding_spread_bps: Decimal
    ftp_rate_pct: Decimal


class FtpProductRead(ClosedModel):
    product: str
    category: FtpCategory
    balance_ghs: Decimal
    tenor_years: Decimal
    customer_rate_pct: Decimal
    ftp_rate_pct: Decimal
    operating_cost_pct: Decimal
    expected_credit_loss_pct: Decimal
    capital_charge_pct: Decimal
    net_margin_pct: Decimal
    contribution_ghs: Decimal
    below_min_margin: bool


class FtpBranchRead(ClosedModel):
    branch: str
    deposits_ghs: Decimal
    loans_ghs: Decimal
    book_ghs: Decimal
    ftp_adjusted_nim_pct: Decimal
    net_contribution_ghs: Decimal
    rank: int


class FtpNmdSegmentRead(ClosedModel):
    segment: str
    balance_ghs: Decimal
    core_pct: Decimal
    volatile_pct: Decimal
    core_amount_ghs: Decimal
    volatile_amount_ghs: Decimal
    effective_duration_years: Decimal
    core_ftp_pct: Decimal
    volatile_ftp_pct: Decimal
    assigned_ftp_pct: Decimal
    within_policy: bool


class FtpMetricsRead(ClosedModel):
    portfolio_nim_pct: Decimal
    weighted_asset_yield_pct: Decimal
    weighted_funding_credit_pct: Decimal
    total_balance_ghs: Decimal
    total_contribution_ghs: Decimal
    products_below_min_margin: int
    total_products: int
    min_product_margin_pct: Decimal
    total_branch_contribution_ghs: Decimal
    nmd_core_pct: Decimal
    nmd_core_status: FtpStatus
    nmd_core_min_pct: Decimal
    nmd_core_max_pct: Decimal
    blended_assigned_ftp_pct: Decimal


class FtpValidationRead(ClosedModel):
    rule_code: str
    passed: bool
    severity: RegulatoryValidationSeverity
    message: str


class FtpTrendPointRead(ClosedModel):
    reporting_period_id: UUID
    label: str
    period_end: date
    portfolio_nim_pct: Decimal
    products_below_min_margin: int
    nmd_core_pct: Decimal
    stored: bool


class FtpDashboardRead(ClosedModel):
    bank: BankRead
    period: BankReportingPeriodRead
    stored: bool
    latest_run_id: UUID | None = Field(title="Ftp Dashboard Latest Run Id")
    metrics: FtpMetricsRead
    curve: list[FtpCurvePointRead]
    products: list[FtpProductRead]
    branches: list[FtpBranchRead]
    nmd_segments: list[FtpNmdSegmentRead]
    trend: list[FtpTrendPointRead]
    validations: list[FtpValidationRead]
