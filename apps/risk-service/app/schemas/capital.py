from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.schemas.findings import EvidenceRead, FindingRead

type CapitalProjectionStatus = Literal["queued", "running", "succeeded", "failed"]
type CapitalPressureLevel = Literal["low", "medium", "high", "critical"]


class ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CapitalProjectionCreate(ClosedModel):
    calculation_run_id: UUID


class CapitalProjectionErrorRead(ClosedModel):
    code: str
    message: str
    details: dict[str, Any]


class CapitalIndicatorRead(ClosedModel):
    id: UUID
    forecast_period_id: UUID
    period_number: int
    equity: Decimal
    equity_to_assets_ratio: Decimal
    liabilities_to_assets_ratio: Decimal
    equity_change: Decimal
    pressure_level: CapitalPressureLevel
    evidence: dict[str, Any]


class CapitalFindingRead(ClosedModel):
    finding: FindingRead
    evidence: list[EvidenceRead]


class CapitalProjectionRead(ClosedModel):
    id: UUID
    organization_id: UUID
    case_id: UUID
    scenario_id: UUID
    calculation_run_id: UUID
    status: CapitalProjectionStatus
    engine_version: str
    input_hash: str
    started_at: datetime | None
    completed_at: datetime | None
    error: CapitalProjectionErrorRead | None
    indicators: list[CapitalIndicatorRead]
    findings: list[CapitalFindingRead]
    created_by: UUID
    created_at: datetime
    updated_at: datetime


class CapitalProjectionSummaryRead(ClosedModel):
    id: UUID
    scenario_id: UUID
    calculation_run_id: UUID
    status: CapitalProjectionStatus
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime


class CapitalProjectionListRead(ClosedModel):
    case_id: UUID
    projections: list[CapitalProjectionSummaryRead]
    total: int
    limit: int
    offset: int
    has_more: bool


class CapitalSummaryRead(ClosedModel):
    case_id: UUID
    scenario_id: UUID | None
    projection: CapitalProjectionRead | None


class CapitalComparisonPeriodRead(ClosedModel):
    period_number: int
    baseline_equity: Decimal
    downside_equity: Decimal
    equity_delta: Decimal
    baseline_equity_to_assets_ratio: Decimal
    downside_equity_to_assets_ratio: Decimal
    equity_to_assets_ratio_delta: Decimal


class CapitalComparisonRead(ClosedModel):
    case_id: UUID
    baseline: CapitalProjectionRead | None
    downside: CapitalProjectionRead | None
    periods: list[CapitalComparisonPeriodRead]
