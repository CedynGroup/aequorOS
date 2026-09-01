"""Legacy Data Engine activation schemas (canonical → facts).

Live calculation is triggered by ingestion. ``run_calculations`` remains a
backwards-compatible, explicit request for immutable official snapshots.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

type ActivationGroupStatus = Literal["derived", "skipped"]
type ActivationRunStatus = Literal["succeeded", "partial", "failed"]
type ActivationModule = Literal[
    "liquidity",
    "capital",
    "credit",
    "irr",
    "fx",
    "ftp",
    "forecast",
    "implied_rating",
]


class ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class DataActivationCreate(ClosedModel):
    as_of_date: date
    reason: str = Field(min_length=1)
    run_calculations: bool = Field(
        default=False,
        description=(
            "Compatibility-only: mint immutable official calculation runs. "
            "Never required for live Treasury/ALM refresh."
        ),
    )


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
    bank_id: str
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
    bank_id: str
    activations: list[DataActivationSummaryRead]
