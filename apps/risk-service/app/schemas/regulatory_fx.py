from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.banks import BankRead, BankReportingPeriodRead
from app.schemas.regulatory_liquidity import RegulatoryValidationSeverity

type FxStatus = Literal["green", "amber", "red"]
type FxSide = Literal["long", "short"]
type FxScenarioCode = Literal["baseline", "mild_depreciation", "severe_depreciation", "cedi_crisis"]


class ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FxScenarioBatchCreate(ClosedModel):
    reporting_period_id: UUID


class FxCurrencyPositionRead(ClosedModel):
    currency: str
    side: FxSide
    net_ghs: Decimal
    net_ccy: Decimal
    spot_ghs: Decimal
    abs_pct_tier1: Decimal
    within_single_limit: bool


class FxStandaloneVarRead(ClosedModel):
    currency: str
    net_ghs: Decimal
    standalone_var_ghs: Decimal


class FxHedgeRead(ClosedModel):
    hedge_id: str
    instrument: str
    pair: str
    mtm_ghs: Decimal
    prospective_r2_pct: Decimal
    dollar_offset_pct: Decimal
    effective: bool


class FxScenarioNopRead(ClosedModel):
    scenario_code: FxScenarioCode
    shock_pct: Decimal
    nop_ghs: Decimal
    nop_pct_tier1: Decimal
    within_aggregate_limit: bool


class FxMetricsRead(ClosedModel):
    nop_ghs: Decimal
    nop_pct_tier1: Decimal
    nop_status: FxStatus
    sum_long_ghs: Decimal
    sum_short_ghs: Decimal
    single_ccy_max_pct: Decimal
    single_ccy_max_currency: str
    single_ccy_status: FxStatus
    nop_single_limit_pct: Decimal
    nop_aggregate_limit_pct: Decimal
    var_99_1d_ghs: Decimal
    stressed_var_ghs: Decimal
    diversification_benefit_ghs: Decimal
    standalone_var_total_ghs: Decimal
    var_confidence_pct: Decimal
    var_observations: int
    hedge_effective_count: int
    hedge_total_count: int
    hedge_aggregate_mtm_ghs: Decimal
    tier1_ghs: Decimal


class FxValidationRead(ClosedModel):
    rule_code: str
    passed: bool
    severity: RegulatoryValidationSeverity
    message: str


class FxTrendPointRead(ClosedModel):
    reporting_period_id: UUID
    label: str
    period_end: date
    nop_ghs: Decimal
    nop_pct_tier1: Decimal
    var_99_1d_ghs: Decimal
    stored: bool


class FxDashboardRead(ClosedModel):
    bank: BankRead
    period: BankReportingPeriodRead
    stored: bool
    latest_run_id: UUID | None = Field(title="Fx Dashboard Latest Run Id")
    metrics: FxMetricsRead
    positions: list[FxCurrencyPositionRead]
    standalone_vars: list[FxStandaloneVarRead]
    hedges: list[FxHedgeRead]
    scenarios: list[FxScenarioNopRead]
    trend: list[FxTrendPointRead]
    validations: list[FxValidationRead]
