from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.banks import BankRead, BankReportingPeriodRead
from app.schemas.live import LiveModuleView

type RegulatoryModule = Literal[
    "liquidity", "capital", "forecast", "optimizer", "whatif", "irr", "fx", "ftp"
]
type LiquidityScenarioCode = Literal["baseline", "idiosyncratic", "market_wide", "combined"]
type CapitalScenarioCode = Literal["baseline", "mild", "moderate", "severe"]
type IrrScenarioCode = Literal[
    "baseline",
    "parallel_up_200",
    "parallel_down_200",
    "short_up_250",
    "short_down_250",
    "steepener",
    "flattener",
]
type RegulatoryScenarioCode = Literal[
    "baseline",
    "idiosyncratic",
    "market_wide",
    "combined",
    "mild",
    "moderate",
    "severe",
    "base",
    "adverse",
    "severely_adverse",
    "custom",
    "constrained_search",
    "rate_shock_up_400",
    "cedi_depreciation_20",
    "default_spike",
    "mpr_cut_200",
    "parallel_up_200",
    "parallel_down_200",
    "short_up_250",
    "short_down_250",
    "steepener",
    "flattener",
    "mild_depreciation",
    "severe_depreciation",
    "cedi_crisis",
    "rates_up_200",
    "funding_stress",
]
type RegulatoryRunStatus = Literal["queued", "running", "succeeded", "failed"]
type RegulatoryMetricUnit = Literal["pct", "ghs", "years"]
type RegulatoryMetricStatus = Literal["green", "amber", "red", "na"]
type LiquidityRatioStatus = Literal["green", "amber", "red"]
type RegulatoryLineSection = Literal[
    "hqla",
    "outflow",
    "inflow",
    "asf",
    "rsf",
    "credit_rwa",
    "market_rwa",
    "operational_rwa",
    "capital_component",
    "ratio",
    "irr_gap",
    "irr_eve",
    "irr_ear",
    "fx_position",
    "fx_var",
    "fx_hedge",
    "ftp_curve",
    "ftp_product",
    "ftp_branch",
]
type RegulatoryValidationSeverity = Literal["error", "warning", "info"]


MODULE_SCENARIO_CODES: dict[str, tuple[str, ...]] = {
    "liquidity": ("baseline", "idiosyncratic", "market_wide", "combined"),
    "capital": ("baseline", "mild", "moderate", "severe"),
}


class ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RegulatoryRunCreate(ClosedModel):
    module: RegulatoryModule
    reporting_period_id: UUID
    scenario_code: RegulatoryScenarioCode

    @model_validator(mode="after")
    def require_scenario_matching_module(self) -> RegulatoryRunCreate:
        allowed = MODULE_SCENARIO_CODES.get(self.module)
        if allowed is None:
            raise ValueError(
                f"Runs for the '{self.module}' module are not created through this endpoint; "
                "use the dedicated /forecast/runs, /forecast/optimizer, or /forecast/whatif "
                "endpoints instead."
            )
        if self.scenario_code not in allowed:
            raise ValueError(
                f"Scenario '{self.scenario_code}' is not valid for the '{self.module}' module; "
                "expected one of: " + ", ".join(allowed) + "."
            )
        return self


class LiquidityScenarioBatchCreate(ClosedModel):
    reporting_period_id: UUID


class RegulatoryRunErrorRead(ClosedModel):
    code: str
    message: str
    details: dict[str, Any] | None = None


type RegulatoryRunError = RegulatoryRunErrorRead | None


class RegulatoryMetricResultRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    metric_code: str
    metric_value: Decimal
    unit: RegulatoryMetricUnit
    threshold_min: Decimal | None
    status: RegulatoryMetricStatus
    position: int


class RegulatoryLineItemRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    section: RegulatoryLineSection
    line_code: str
    description: str
    exposure_amount: Decimal | None
    rate_pct: Decimal | None
    weighted_amount: Decimal
    position: int


class RegulatoryValidationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    rule_code: str
    passed: bool
    severity: RegulatoryValidationSeverity
    message: str
    position: int


class RegulatoryRunRead(ClosedModel):
    id: UUID
    organization_id: UUID
    bank_id: UUID
    reporting_period_id: UUID
    module: RegulatoryModule
    scenario_code: RegulatoryScenarioCode
    status: RegulatoryRunStatus
    engine_version: str
    input_schema_version: str
    output_schema_version: str
    input_hash: str
    inputs: dict[str, Any]
    metrics: dict[str, Any]
    started_at: datetime | None = Field(title="Regulatory Run Started At")
    completed_at: datetime | None = Field(title="Regulatory Run Completed At")
    error: RegulatoryRunError
    metric_results: list[RegulatoryMetricResultRead]
    line_items: list[RegulatoryLineItemRead]
    validations: list[RegulatoryValidationRead]
    created_by: UUID
    created_at: datetime
    updated_at: datetime


class RegulatoryRunSummaryRead(ClosedModel):
    id: UUID
    module: RegulatoryModule
    scenario_code: RegulatoryScenarioCode
    status: RegulatoryRunStatus
    reporting_period_id: UUID
    period_label: str
    engine_version: str
    input_hash: str
    metrics: dict[str, Any]
    error: RegulatoryRunError
    created_at: datetime


class RegulatoryRunListRead(ClosedModel):
    bank_id: UUID
    runs: list[RegulatoryRunSummaryRead]
    total: int
    limit: int
    offset: int
    has_more: bool


class RegulatoryRunBatchRead(ClosedModel):
    bank_id: UUID
    reporting_period_id: UUID
    runs: list[RegulatoryRunRead]


class LiquidityMetricsRead(ClosedModel):
    lcr_pct: Decimal
    lcr_status: LiquidityRatioStatus
    nsfr_pct: Decimal
    nsfr_status: LiquidityRatioStatus
    hqla_total_ghs: Decimal
    net_outflows_30d_ghs: Decimal
    asf_total_ghs: Decimal
    rsf_total_ghs: Decimal


class LiquidityDashboardLineRead(ClosedModel):
    line_code: str
    description: str
    exposure_amount: Decimal | None = Field(title="Liquidity Dashboard Line Exposure")
    rate_pct: Decimal | None = Field(title="Liquidity Dashboard Line Rate Pct")
    weighted_amount: Decimal


class LiquidityValidationRead(ClosedModel):
    rule_code: str
    passed: bool
    severity: RegulatoryValidationSeverity
    message: str


class LiquidityTrendPointRead(ClosedModel):
    reporting_period_id: UUID
    label: str
    period_end: date
    lcr_pct: Decimal
    nsfr_pct: Decimal
    stored: bool


class LiquidityDashboardRead(ClosedModel):
    bank: BankRead
    period: BankReportingPeriodRead
    stored: bool
    latest_run_id: UUID | None
    metrics: LiquidityMetricsRead
    hqla_composition: list[LiquidityDashboardLineRead]
    outflows: list[LiquidityDashboardLineRead]
    inflows: list[LiquidityDashboardLineRead]
    trend: list[LiquidityTrendPointRead]
    validations: list[LiquidityValidationRead]
    live: LiveModuleView | None = None


class Bsd3HeaderRead(ClosedModel):
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


class Bsd3RowRead(ClosedModel):
    row_code: str
    description: str
    amount: Decimal


class Bsd3WeightedRowRead(ClosedModel):
    row_code: str
    description: str
    balance: Decimal
    rate_pct: Decimal
    weighted_amount: Decimal


class Bsd3SummaryRowRead(ClosedModel):
    row_code: str
    description: str
    value: Decimal
    unit: RegulatoryMetricUnit


class Bsd3NsfrSectionRead(ClosedModel):
    asf_rows: list[Bsd3WeightedRowRead]
    asf_total: Bsd3SummaryRowRead
    rsf_rows: list[Bsd3WeightedRowRead]
    rsf_total: Bsd3SummaryRowRead
    nsfr_ratio: Bsd3SummaryRowRead


class Bsd3PreviewRead(ClosedModel):
    header: Bsd3HeaderRead
    run_id: UUID
    scenario_code: LiquidityScenarioCode
    hqla_rows: list[Bsd3RowRead]
    outflow_rows: list[Bsd3WeightedRowRead]
    inflow_rows: list[Bsd3WeightedRowRead]
    summary_rows: list[Bsd3SummaryRowRead]
    nsfr: Bsd3NsfrSectionRead
    validations: list[LiquidityValidationRead]
