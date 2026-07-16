from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

type LiveStatus = Literal["green", "amber", "red", "na"]
type LiveModule = Literal["liquidity", "capital", "irr", "fx", "ftp", "forecast"]
type AlertSeverity = Literal["low", "medium", "high", "critical"]


class ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LiveModuleView(ClosedModel):
    module: LiveModule
    status: LiveStatus
    metrics: dict[str, Any]
    computed_at: datetime
    computed_from_input_hash: str | None


class LiveSummaryRead(ClosedModel):
    bank_id: UUID
    reporting_period_id: UUID | None
    period_label: str | None
    modules: list[LiveModuleView]
    is_stale: bool
    computed_at: datetime | None = Field(title="Live Summary Computed At")


class FreshnessModuleRead(ClosedModel):
    module: LiveModule
    live_hash: str | None
    official_run_hash: str | None
    is_stale: bool
    computed_at: datetime | None = Field(title="Live Metric Computed At")
    official_run_at: datetime | None = Field(title="Official Run At")


class BankFreshnessRead(ClosedModel):
    bank_id: UUID
    reporting_period_id: UUID | None
    period_label: str | None
    modules: list[FreshnessModuleRead]
    is_stale: bool


class AlertItemRead(ClosedModel):
    finding_id: UUID
    module: LiveModule
    severity: AlertSeverity
    rule_id: str
    message: str
    metric: str | None
    created_at: datetime


class BankAlertsRead(ClosedModel):
    bank_id: UUID
    total: int
    by_severity: dict[str, int]
    by_module: dict[str, int]
    items: list[AlertItemRead]


class RefreshRequest(ClosedModel):
    as_of_date: date
    reason: str = Field(min_length=1)


class OfficialRunRequest(ClosedModel):
    as_of_date: date
    reason: str = Field(min_length=1)


class JobEnqueuedRead(ClosedModel):
    job_id: UUID
    job_type: str
    status: str
