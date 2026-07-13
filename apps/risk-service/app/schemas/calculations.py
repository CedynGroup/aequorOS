from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

type CalculationStatus = Literal["queued", "running", "succeeded", "failed"]


class ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CalculationRunCreate(ClosedModel):
    scenario_id: UUID
    forecast_periods: int = Field(default=3, ge=1, le=12)
    as_of_date: date | None = None


class CalculationRerunCreate(ClosedModel):
    forecast_periods: int | None = Field(default=None, ge=1, le=12)
    as_of_date: date | None = None


class CalculationErrorRead(ClosedModel):
    code: str
    message: str
    details: dict[str, Any] | None = None


class ForecastPeriodRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    period_number: int
    period_end: date
    currency: str
    total_assets: Decimal
    total_liabilities: Decimal
    total_equity: Decimal
    cash: Decimal
    projected_inflows: Decimal
    projected_outflows: Decimal
    credit_draw: Decimal
    debt_repayment: Decimal
    components: dict[str, Any]


class CalculationRunRead(ClosedModel):
    id: UUID
    organization_id: UUID
    case_id: UUID
    scenario_id: UUID
    rerun_of_run_id: UUID | None
    status: CalculationStatus
    engine_version: str
    input_schema_version: str
    output_schema_version: str
    input_hash: str
    inputs: dict[str, Any]
    forecast_periods: int
    as_of_date: date
    started_at: datetime | None
    completed_at: datetime | None
    error: CalculationErrorRead | None
    outputs: list[ForecastPeriodRead]
    created_by: UUID
    created_at: datetime
    updated_at: datetime


class CalculationRunListRead(ClosedModel):
    case_id: UUID
    runs: list[CalculationRunRead]
    latest_successful_run_id: UUID | None
