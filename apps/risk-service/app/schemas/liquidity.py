from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.common import JsonObject

type LiquiditySummaryStatus = Literal["not_calculated", "ready"]
type LiquidityFindingStatus = Literal[
    "open",
    "accepted",
    "acknowledged",
    "dismissed",
    "needs_review",
    "resolved",
    "superseded",
]
type LiquidityReviewAction = Literal["acknowledge", "dismiss"]


class ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LiquidityMetricRead(ClosedModel):
    key: str
    label: str
    value: Decimal | None = Field(title="Liquidity Metric Value")
    unit: str
    availability: Literal["available", "unavailable"] = "available"
    diagnostic: str | None = Field(default=None, title="Liquidity Metric Diagnostic")
    period_number: int | None = None
    period_end: date | None = None
    description: str

    @model_validator(mode="after")
    def validate_availability(self) -> LiquidityMetricRead:
        if self.availability == "available" and self.value is None:
            raise ValueError("Available liquidity metrics require a value.")
        if self.availability == "unavailable" and (self.value is not None or not self.diagnostic):
            raise ValueError("Unavailable liquidity metrics require a diagnostic and no value.")
        return self


class LiquidityEvidenceRead(ClosedModel):
    id: UUID
    source_type: Literal["forecast_output", "canonical_input", "scenario_assumption"]
    label: str
    source_url: str
    locator: JsonObject
    quote: str | None


class LiquidityFindingRead(ClosedModel):
    id: UUID
    calculation_run_id: UUID
    rule_id: str
    rule_version: str
    title: str
    summary: str
    rationale: str
    severity: Literal["low", "medium", "high", "critical"]
    status: LiquidityFindingStatus
    disposition_reason: str | None
    evidence: list[LiquidityEvidenceRead]
    created_at: datetime
    updated_at: datetime


class LiquiditySummaryRead(ClosedModel):
    case_id: UUID
    scenario_id: UUID | None
    calculation_run_id: UUID | None
    calculation_input_hash: str | None
    analysis_version: str | None
    sources_coverage_threshold: Decimal | None = Field(title="Liquidity Sources Coverage Threshold")
    sources_coverage_threshold_rule_version: str | None = Field(
        title="Liquidity Sources Coverage Threshold Rule Version"
    )
    status: LiquiditySummaryStatus
    currency: str | None
    as_of_date: date | None = Field(title="Liquidity Summary As Of Date")
    metrics: list[LiquidityMetricRead]
    findings: list[LiquidityFindingRead]
    generated_at: datetime | None

    @model_validator(mode="after")
    def validate_sources_coverage_threshold(self) -> LiquiditySummaryRead:
        if (self.sources_coverage_threshold is None) != (
            self.sources_coverage_threshold_rule_version is None
        ):
            raise ValueError(
                "Liquidity sources coverage threshold and rule version must be provided together."
            )
        return self


class LiquidityFindingReview(ClosedModel):
    action: LiquidityReviewAction
    reason: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def require_dismissal_reason(self) -> LiquidityFindingReview:
        if self.action == "dismiss" and not (self.reason and self.reason.strip()):
            raise ValueError("Dismissed findings require a reason.")
        return self
