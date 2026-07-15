from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.regulatory_liquidity import (
    RegulatoryMetricResultRead,
    RegulatoryRunErrorRead,
    RegulatoryRunStatus,
    RegulatoryValidationRead,
)

type ForecastPresetCode = Literal["base", "adverse", "severely_adverse"]
type ForecastScenarioCode = Literal["base", "adverse", "severely_adverse", "custom"]
type WhatIfShockCode = Literal[
    "rate_shock_up_400", "cedi_depreciation_20", "default_spike", "mpr_cut_200"
]
type OptimizerConstraintCode = Literal["car", "lcr", "nsfr"]


class ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ForecastAssumptionsRead(ClosedModel):
    loan_growth_pct: Decimal
    deposit_growth_pct: Decimal
    nim_pct: Decimal
    cost_to_income_pct: Decimal
    credit_loss_rate_pct: Decimal
    fx_depreciation_pct: Decimal
    dividend_payout_pct: Decimal
    fee_income_pct_assets: Decimal
    tax_rate_pct: Decimal
    securities_shift_pp: Decimal


class ForecastAssumptionsUpdate(ClosedModel):
    """Full or partial assumption override; unset fields resolve from the preset."""

    loan_growth_pct: Decimal | None = Field(default=None, title="Loan Growth Override")
    deposit_growth_pct: Decimal | None = Field(default=None, title="Deposit Growth Override")
    nim_pct: Decimal | None = Field(default=None, title="NIM Override")
    cost_to_income_pct: Decimal | None = Field(default=None, title="Cost To Income Override")
    credit_loss_rate_pct: Decimal | None = Field(default=None, title="Credit Loss Rate Override")
    fx_depreciation_pct: Decimal | None = Field(default=None, title="FX Depreciation Override")
    dividend_payout_pct: Decimal | None = Field(default=None, title="Dividend Payout Override")
    fee_income_pct_assets: Decimal | None = Field(default=None, title="Fee Income Override")
    tax_rate_pct: Decimal | None = Field(default=None, title="Tax Rate Override")
    securities_shift_pp: Decimal | None = Field(default=None, title="Securities Shift Override")


class ForecastAssumptionDefaultsRead(ClosedModel):
    fee_income_pct_assets: Decimal
    tax_rate_pct: Decimal
    securities_shift_pp: Decimal


class ForecastScenarioRead(ClosedModel):
    code: ForecastPresetCode
    assumptions: dict[str, Decimal]


class ForecastScenarioListRead(ClosedModel):
    bank_id: UUID
    scenarios: list[ForecastScenarioRead]
    defaults: ForecastAssumptionDefaultsRead


class ForecastRunCreate(ClosedModel):
    reporting_period_id: UUID
    scenario_code: ForecastScenarioCode
    assumptions: ForecastAssumptionsUpdate | None = None

    @model_validator(mode="after")
    def require_assumptions_for_custom(self) -> ForecastRunCreate:
        if self.scenario_code == "custom" and self.assumptions is None:
            raise ValueError(
                "A custom forecast scenario requires an assumptions object; partial overrides "
                "resolve against the base preset."
            )
        return self


class ProjectionYearRead(ClosedModel):
    year: int
    period_label: str
    total_assets: Decimal
    loans: Decimal
    securities: Decimal
    cash: Decimal
    deposits: Decimal
    borrowings_plug: Decimal
    equity: Decimal
    nii: Decimal
    fees: Decimal
    total_income: Decimal
    opex: Decimal
    credit_losses: Decimal
    net_income: Decimal
    dividends: Decimal
    roe_pct: Decimal | None = Field(title="Projection Year ROE Pct")
    car_pct: Decimal
    tier1_ratio_pct: Decimal
    cet1_ratio_pct: Decimal
    lcr_pct: Decimal
    nsfr_pct: Decimal


class ProjectionSummaryRead(ClosedModel):
    avg_roe_pct: Decimal
    year5_car_pct: Decimal
    year5_lcr_pct: Decimal
    year5_nsfr_pct: Decimal
    cumulative_net_income: Decimal
    min_car_pct: Decimal
    min_lcr_pct: Decimal
    min_nsfr_pct: Decimal


class ForecastRunRead(ClosedModel):
    id: UUID
    organization_id: UUID
    bank_id: UUID
    reporting_period_id: UUID
    module: Literal["forecast"]
    scenario_code: ForecastScenarioCode
    status: RegulatoryRunStatus
    engine_version: str
    input_schema_version: str
    output_schema_version: str
    input_hash: str
    inputs: dict[str, Any]
    assumptions: ForecastAssumptionsRead | None = Field(title="Resolved Forecast Assumptions")
    path: list[ProjectionYearRead]
    summary: ProjectionSummaryRead | None = Field(title="Forecast Projection Summary")
    metric_results: list[RegulatoryMetricResultRead]
    validations: list[RegulatoryValidationRead]
    error: RegulatoryRunErrorRead | None = Field(title="Forecast Run Error")
    started_at: datetime | None = Field(title="Forecast Run Started At")
    completed_at: datetime | None = Field(title="Forecast Run Completed At")
    created_by: UUID
    created_at: datetime
    updated_at: datetime


class ForecastRunSummaryRead(ClosedModel):
    id: UUID
    scenario_code: ForecastScenarioCode
    status: RegulatoryRunStatus
    reporting_period_id: UUID
    period_label: str
    input_hash: str
    avg_roe_pct: Decimal | None = Field(title="Forecast Summary Avg ROE Pct")
    year5_car_pct: Decimal | None = Field(title="Forecast Summary Year 5 CAR Pct")
    year5_lcr_pct: Decimal | None = Field(title="Forecast Summary Year 5 LCR Pct")
    year5_nsfr_pct: Decimal | None = Field(title="Forecast Summary Year 5 NSFR Pct")
    error: RegulatoryRunErrorRead | None = Field(title="Forecast Run Summary Error")
    created_at: datetime


class ForecastRunListRead(ClosedModel):
    bank_id: UUID
    runs: list[ForecastRunSummaryRead]
    total: int
    limit: int
    offset: int
    has_more: bool


class OptimizerRunCreate(ClosedModel):
    reporting_period_id: UUID


class OptimizerDecisionRead(ClosedModel):
    loan_growth_pct: Decimal
    securities_shift_pp: Decimal
    deposit_premium_bps: int
    dividend_payout_pct: Decimal
    deposit_growth_delta_pct: Decimal
    nim_delta_pct: Decimal


class OptimizerConstraintStatusRead(ClosedModel):
    constraint: OptimizerConstraintCode
    minimum_pct: Decimal
    observed_min_pct: Decimal
    passed: bool


class OptimizerCandidateRead(ClosedModel):
    decision: OptimizerDecisionRead
    summary: ProjectionSummaryRead
    constraint_status: list[OptimizerConstraintStatusRead]
    feasible: bool


class OptimizerResultRead(ClosedModel):
    run_id: UUID
    bank_id: UUID
    reporting_period_id: UUID
    scenario_code: Literal["constrained_search"]
    status: RegulatoryRunStatus
    input_hash: str
    base_assumptions: ForecastAssumptionsRead | None = Field(title="Optimizer Base Assumptions")
    candidates_evaluated: int
    feasible_count: int
    top: list[OptimizerCandidateRead]
    binding_constraint_histogram: dict[str, int]
    error: RegulatoryRunErrorRead | None = Field(title="Optimizer Run Error")
    created_at: datetime


class WhatIfRunCreate(ClosedModel):
    reporting_period_id: UUID
    shock_code: WhatIfShockCode


class WhatIfYearDeltaRead(ClosedModel):
    year: int
    car_delta_pp: Decimal
    lcr_delta_pp: Decimal
    nsfr_delta_pp: Decimal
    net_income_delta: Decimal


class WhatIfMetricComparisonRead(ClosedModel):
    base: Decimal
    shocked: Decimal
    delta: Decimal


class WhatIfYear5ComparisonRead(ClosedModel):
    car_pct: WhatIfMetricComparisonRead
    lcr_pct: WhatIfMetricComparisonRead
    nsfr_pct: WhatIfMetricComparisonRead
    net_income: WhatIfMetricComparisonRead


class WhatIfResultRead(ClosedModel):
    run_id: UUID
    bank_id: UUID
    reporting_period_id: UUID
    shock_code: WhatIfShockCode
    status: RegulatoryRunStatus
    input_hash: str
    base_assumptions: ForecastAssumptionsRead | None = Field(title="What-If Base Assumptions")
    shocked_assumptions: ForecastAssumptionsRead | None = Field(title="What-If Shocked Assumptions")
    base_path: list[ProjectionYearRead]
    shocked_path: list[ProjectionYearRead]
    base_summary: ProjectionSummaryRead | None = Field(title="What-If Base Summary")
    shocked_summary: ProjectionSummaryRead | None = Field(title="What-If Shocked Summary")
    deltas: list[WhatIfYearDeltaRead]
    year5: WhatIfYear5ComparisonRead | None = Field(title="What-If Year 5 Comparison")
    error: RegulatoryRunErrorRead | None = Field(title="What-If Run Error")
    created_at: datetime
