"""API schemas for Data Engine activations (canonical → module facts + runs)."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

type ActivationGroupStatus = Literal["derived", "skipped"]
type ActivationRunStatus = Literal["succeeded", "partial", "failed"]
type ActivationModule = Literal["liquidity", "capital", "irr", "fx", "ftp", "forecast"]


class ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class DataActivationCreate(ClosedModel):
    as_of_date: date
    reason: str = Field(min_length=1)
    run_calculations: bool = True


class ActivationGroupRead(ClosedModel):
    group: str
    status: ActivationGroupStatus
    rows: int
    warnings: list[str]
    note: str | None = Field(default=None, title="Activation Group Note")


class ActivationRunRead(ClosedModel):
    module: ActivationModule
    status: ActivationRunStatus
    scenarios_succeeded: int
    scenarios_failed: int
    headline: str | None = Field(default=None, title="Activation Run Headline")
    error: str | None = Field(default=None, title="Activation Run Error")


class DataActivationRead(ClosedModel):
    bank_id: UUID
    reporting_period_id: UUID
    period_label: str
    as_of_date: date
    period_created: bool
    facts_deleted: int
    facts_created: int
    groups: list[ActivationGroupRead]
    runs: list[ActivationRunRead]
    warnings: list[str]


class DataActivationSummaryRead(ClosedModel):
    activated_at: datetime
    as_of_date: date | None = Field(default=None, title="Activation As-Of Date")
    period_label: str | None = Field(default=None, title="Activation Period Label")
    facts_created: int | None = Field(default=None, title="Activation Facts Created")
    modules_succeeded: int | None = Field(default=None, title="Activation Modules Succeeded")
    modules_failed: int | None = Field(default=None, title="Activation Modules Failed")
    warnings: int | None = Field(default=None, title="Activation Warning Count")


class DataActivationListRead(ClosedModel):
    bank_id: UUID
    activations: list[DataActivationSummaryRead]
