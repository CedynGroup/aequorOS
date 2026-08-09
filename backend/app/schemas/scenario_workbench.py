"""Scenario Workbench contracts: catalogue, CRUD, analysis, saved analyses."""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal
from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


type WorkbenchModule = Literal["liquidity", "capital", "irr", "fx", "ftp"]
type ScenarioKind = Literal["system", "custom"]

_CODE_PATTERN = re.compile(r"^[a-z0-9_]{1,60}$")


class StressScenarioCreate(ClosedModel):
    code: str = Field(min_length=1, max_length=60)
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    shocks: dict[str, Decimal] = Field(min_length=1)

    @model_validator(mode="after")
    def slug_code(self) -> Self:
        if not _CODE_PATTERN.match(self.code):
            msg = "code must be a lowercase slug: a-z, 0-9 and underscores only"
            raise ValueError(msg)
        return self


class StressScenarioUpdate(ClosedModel):
    name: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    shocks: dict[str, Decimal] | None = None


class StressScenarioArchive(ClosedModel):
    is_archived: bool


class StressScenarioRead(ClosedModel):
    id: UUID
    module: WorkbenchModule
    code: str
    name: str
    description: str | None = None
    shocks: dict[str, str]
    created_by: UUID | None = None
    is_archived: bool
    created_at: datetime
    updated_at: datetime


class ScenarioCatalogueEntryRead(ClosedModel):
    kind: ScenarioKind
    code: str
    scenario_id: UUID | None = None
    name: str
    description: str | None = None
    shocks: dict[str, str]
    #: System scenarios cannot be edited — their levels are the governed
    #: (Board/directive) parameter set.
    locked: bool
    #: False for a system scenario with no active parameter rows as of the
    #: date — surfaced, not hidden, so the gap is visible.
    configured: bool = True
    is_archived: bool = False


class ScenarioCatalogueRead(ClosedModel):
    bank_id: str
    module: WorkbenchModule
    as_of_date: date
    #: The module's shock vocabulary, so editors render the right fields.
    allowed_keys: list[str]
    allowed_prefixes: list[str]
    scenarios: list[ScenarioCatalogueEntryRead]


class ScenarioRefIn(ClosedModel):
    kind: ScenarioKind
    code: str | None = Field(default=None, max_length=60)
    scenario_id: UUID | None = None
    #: Ad-hoc tweak applied on top of the resolved shocks, without saving.
    overrides: dict[str, Decimal] | None = None

    @model_validator(mode="after")
    def ref_shape(self) -> Self:
        if self.kind == "system" and not self.code:
            msg = "a system scenario reference requires 'code'"
            raise ValueError(msg)
        if self.kind == "custom" and self.scenario_id is None:
            msg = "a custom scenario reference requires 'scenario_id'"
            raise ValueError(msg)
        return self


class AnalysisRunCreate(ClosedModel):
    reporting_period_id: UUID
    scenarios: list[ScenarioRefIn] = Field(min_length=1, max_length=10)


class AnalysisValidationRead(ClosedModel):
    rule_code: str
    passed: bool
    severity: str
    message: str


class ScenarioResultRead(ClosedModel):
    kind: ScenarioKind
    code: str
    scenario_id: UUID | None = None
    label: str
    shocks_applied: dict[str, str]
    status: Literal["succeeded", "failed"]
    error_code: str | None = None
    error_message: str | None = None
    metrics: dict[str, str] = Field(default_factory=dict)
    metric_statuses: dict[str, str] = Field(default_factory=dict)
    validations: list[AnalysisValidationRead] = Field(default_factory=list)


class AnalysisRunRead(ClosedModel):
    bank_id: str
    module: WorkbenchModule
    reporting_period_id: UUID
    period_label: str
    as_of_date: date
    engine_version: str
    computed_at: datetime
    results: list[ScenarioResultRead]


class SavedAnalysisCreate(ClosedModel):
    reporting_period_id: UUID
    name: str = Field(min_length=1, max_length=200)
    #: The server RECOMPUTES at save time and snapshots its own results —
    #: clients never supply numbers.
    scenarios: list[ScenarioRefIn] = Field(min_length=1, max_length=10)


class SavedAnalysisSummaryRead(ClosedModel):
    id: UUID
    module: WorkbenchModule
    reporting_period_id: UUID
    name: str
    engine_version: str
    scenario_count: int
    created_by: UUID | None = None
    created_at: datetime


class SavedAnalysisRead(ClosedModel):
    id: UUID
    module: WorkbenchModule
    reporting_period_id: UUID
    name: str
    engine_version: str
    scenarios: list[dict[str, object]]
    results: list[ScenarioResultRead]
    created_by: UUID | None = None
    created_at: datetime


class SavedAnalysisListRead(ClosedModel):
    analyses: list[SavedAnalysisSummaryRead]
    total: int
    limit: int
    offset: int
