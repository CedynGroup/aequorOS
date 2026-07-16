from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.banks import BankRead, BankReportingPeriodRead
from app.schemas.live import LiveModuleView
from app.schemas.regulatory_liquidity import (
    CapitalScenarioCode,
    RegulatoryMetricUnit,
    RegulatoryValidationSeverity,
)

type CapitalRatioStatus = Literal["green", "amber", "red"]


class ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CapitalScenarioBatchCreate(ClosedModel):
    reporting_period_id: UUID


class CapitalLineRead(ClosedModel):
    line_code: str
    description: str
    exposure_amount: Decimal | None = Field(title="Capital Line Exposure")
    rate_pct: Decimal | None = Field(title="Capital Line Rate Pct")
    weighted_amount: Decimal


class CapitalValidationRead(ClosedModel):
    rule_code: str
    passed: bool
    severity: RegulatoryValidationSeverity
    message: str


class CapitalMetricsRead(ClosedModel):
    car_pct: Decimal
    car_status: CapitalRatioStatus
    tier1_ratio_pct: Decimal
    tier1_status: CapitalRatioStatus
    cet1_ratio_pct: Decimal
    cet1_status: CapitalRatioStatus
    leverage_ratio_pct: Decimal
    leverage_status: CapitalRatioStatus
    total_rwa_ghs: Decimal
    credit_rwa_ghs: Decimal
    market_rwa_ghs: Decimal
    operational_rwa_ghs: Decimal
    total_capital_ghs: Decimal


class RwaCompositionRead(ClosedModel):
    credit_rwa_ghs: Decimal
    market_rwa_ghs: Decimal
    operational_rwa_ghs: Decimal
    total_rwa_ghs: Decimal
    credit_lines: list[CapitalLineRead]


class CapitalStructureSummaryRead(ClosedModel):
    cet1_components: list[CapitalLineRead]
    cet1_deductions: list[CapitalLineRead]
    at1_components: list[CapitalLineRead]
    tier2_components: list[CapitalLineRead]
    cet1_capital_ghs: Decimal
    at1_capital_ghs: Decimal
    tier1_capital_ghs: Decimal
    tier2_capital_ghs: Decimal
    total_capital_ghs: Decimal


class CapitalStructureRead(CapitalStructureSummaryRead):
    bank_id: UUID
    reporting_period_id: UUID
    run_id: UUID


class RwaBreakdownRead(ClosedModel):
    bank_id: UUID
    reporting_period_id: UUID
    run_id: UUID
    credit_rwa_ghs: Decimal
    market_rwa_ghs: Decimal
    operational_rwa_ghs: Decimal
    total_rwa_ghs: Decimal
    credit_lines: list[CapitalLineRead]
    market_lines: list[CapitalLineRead]
    operational_lines: list[CapitalLineRead]


class CapitalTrendPointRead(ClosedModel):
    reporting_period_id: UUID
    label: str
    period_end: date
    car_pct: Decimal
    tier1_ratio_pct: Decimal
    cet1_ratio_pct: Decimal
    stored: bool


class CapitalBuffersRead(ClosedModel):
    car_min_pct: Decimal
    car_early_warning_pct: Decimal
    car_early_warning_label: str
    car_critical_pct: Decimal
    current_car_pct: Decimal
    headroom_pp: Decimal


class CapitalDashboardRead(ClosedModel):
    bank: BankRead
    period: BankReportingPeriodRead
    stored: bool
    latest_run_id: UUID | None = Field(title="Capital Dashboard Latest Run Id")
    metrics: CapitalMetricsRead
    rwa_composition: RwaCompositionRead
    capital_structure: CapitalStructureSummaryRead
    trend: list[CapitalTrendPointRead]
    buffers: CapitalBuffersRead
    validations: list[CapitalValidationRead]
    live: LiveModuleView | None = None


class Bsd2HeaderRead(ClosedModel):
    form_code: str
    form_title: str
    regulator: str
    bank_name: str
    license_type: str
    reporting_period_label: str
    period_end: date
    currency: str
    generated_at: datetime
    preview_note: str


class Bsd2RowRead(ClosedModel):
    row_code: str
    description: str
    amount: Decimal


class Bsd2WeightedRowRead(ClosedModel):
    row_code: str
    description: str
    balance: Decimal
    rate_pct: Decimal
    weighted_amount: Decimal


class Bsd2SummaryRowRead(ClosedModel):
    row_code: str
    description: str
    value: Decimal
    unit: RegulatoryMetricUnit


class Bsd2RatioRowRead(ClosedModel):
    row_code: str
    description: str
    value_pct: Decimal
    minimum_pct: Decimal
    passed: bool


class Bsd2PreviewRead(ClosedModel):
    header: Bsd2HeaderRead
    run_id: UUID
    scenario_code: CapitalScenarioCode
    cet1_rows: list[Bsd2RowRead]
    deduction_rows: list[Bsd2RowRead]
    cet1_total: Bsd2SummaryRowRead
    at1_rows: list[Bsd2RowRead]
    tier1_total: Bsd2SummaryRowRead
    tier2_rows: list[Bsd2RowRead]
    total_capital: Bsd2SummaryRowRead
    credit_rwa_rows: list[Bsd2WeightedRowRead]
    market_rwa_rows: list[Bsd2WeightedRowRead]
    operational_rwa_rows: list[Bsd2WeightedRowRead]
    total_rwa: Bsd2SummaryRowRead
    ratio_rows: list[Bsd2RatioRowRead]
    validations: list[CapitalValidationRead]
