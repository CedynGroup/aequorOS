"""Typed contracts for the behavioral-model endpoints (camelCase JSON)."""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel

type BehavioralModelSlug = Literal["nmd-duration", "prepayment", "deposit-stability"]
type BehavioralMethod = Literal["ml", "baseline"]
type BehavioralLiquidityDimension = Literal[
    "product", "customer_segment", "concentration_group", "branch"
]
type BehavioralLiquidityDataStatus = Literal["ready", "partial", "not_computable"]


class CamelClosedModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        alias_generator=to_camel,
        populate_by_name=True,
        protected_namespaces=(),
    )


class IncentivePoint(CamelClosedModel):
    incentive_bps: int
    cpr: float


class BehavioralProductEstimate(CamelClosedModel):
    product_code: str
    assumption_type: str
    value: float
    unit: str
    confidence: float
    method: BehavioralMethod
    core_pct: float | None = None
    incentive_curve: list[IncentivePoint] | None = None


class BehavioralAccuracyRead(CamelClosedModel):
    cv_rmse: float | None
    cv_mae: float | None
    sample_count: int
    month_coverage: int
    method: BehavioralMethod


class BehavioralModelRead(CamelClosedModel):
    model_id: str
    model_version: str
    method: BehavioralMethod
    as_of_date: date | None
    accuracy: BehavioralAccuracyRead
    products: list[BehavioralProductEstimate]


class BehavioralApplyProduct(CamelClosedModel):
    product_code: str
    value: float
    unit: str | None = None
    confidence: float | None = None


class BehavioralApplyRequest(CamelClosedModel):
    products: list[BehavioralApplyProduct]


class BehavioralApplyRead(CamelClosedModel):
    ingestion_batch_id: str
    as_of_date: date
    applied_rows: int
    total_rows: int


class BehavioralLiquiditySegmentRead(CamelClosedModel):
    dimension: BehavioralLiquidityDimension
    segment: str
    current_balance_ghs: float
    observed_monthly_runoff_pct: float | None
    latest_withdrawal_pct: float | None
    position_attrition_pct: float | None
    seasonal_deviation_pct: float | None
    deposit_beta: float | None
    repricing_lag_months: int | None
    observations: int
    data_status: BehavioralLiquidityDataStatus
    reasons: list[str]


class BehavioralLiquidityScenarioRead(CamelClosedModel):
    name: str
    activation_horizon: str
    linked_action: str
    deposit_runoff_uplift_pct: float
    funding_cost_uplift_bps: float
    notes: str | None = None


class BehavioralLiquidityRead(CamelClosedModel):
    as_of_date: str
    segments: list[BehavioralLiquiditySegmentRead]
    scenarios: list[BehavioralLiquidityScenarioRead]
