"""Governed macro-scenario contracts (docs/stress.md §3.1, Phase 1)."""

from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal
from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.stress import MACRO_VARIABLES


class ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


type MacroModule = Literal["liquidity", "capital", "irr", "fx", "ftp"]
type ScenarioType = Literal[
    "base", "adverse", "historical", "hypothetical", "reverse", "supervisory"
]
type Severity = Literal["mild", "moderate", "severe"]
type ScenarioStatus = Literal["draft", "pending_approval", "approved", "archived"]

_CODE_PATTERN = re.compile(r"^[a-z0-9_]{1,60}$")
_MACRO_VARIABLE_SET = frozenset(MACRO_VARIABLES)


class MacroPathIn(ClosedModel):
    variable: str = Field(min_length=1, max_length=40)
    year_index: int = Field(ge=0, le=50)
    base_value: Decimal
    stress_value: Decimal

    @model_validator(mode="after")
    def known_variable(self) -> Self:
        if self.variable not in _MACRO_VARIABLE_SET:
            allowed = sorted(_MACRO_VARIABLE_SET)
            msg = f"unknown macro variable '{self.variable}'; expected one of {allowed}"
            raise ValueError(msg)
        return self


class MacroPathRead(ClosedModel):
    variable: str
    year_index: int
    base_value: Decimal
    stress_value: Decimal


class MacroScenarioCreate(ClosedModel):
    code: str = Field(min_length=1, max_length=60)
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    scenario_type: ScenarioType
    severity: Severity | None = None
    horizon_years: int = Field(default=3, ge=1, le=10)
    narrative: str | None = Field(default=None, max_length=8000)
    source: str | None = Field(default=None, max_length=2000)
    #: NULL = global-supervisory (org-wide). Set = bank-specific; validated to
    #: belong to the tenant.
    bank_id: str | None = Field(default=None, max_length=16)
    #: Forward hook for SDI scoping (docs/sdi.md §7); NULL = applies to all.
    institution_type_applicability: list[str] | None = None
    paths: list[MacroPathIn] = Field(min_length=1, max_length=1000)
    reason: str = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def _validate(self) -> Self:
        if not _CODE_PATTERN.match(self.code):
            msg = "code must be a lowercase slug: a-z, 0-9 and underscores only"
            raise ValueError(msg)
        for path in self.paths:
            if path.year_index > self.horizon_years:
                msg = (
                    f"path year_index {path.year_index} for '{path.variable}' exceeds "
                    f"horizon_years {self.horizon_years}"
                )
                raise ValueError(msg)
        seen: set[tuple[str, int]] = set()
        for path in self.paths:
            key = (path.variable, path.year_index)
            if key in seen:
                msg = f"duplicate path for ({path.variable}, year {path.year_index})"
                raise ValueError(msg)
            seen.add(key)
        return self


class MacroScenarioUpdate(ClosedModel):
    """Draft-only edit. Any provided ``paths`` REPLACES the full path set."""

    name: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    scenario_type: ScenarioType | None = None
    severity: Severity | None = None
    horizon_years: int | None = Field(default=None, ge=1, le=10)
    narrative: str | None = Field(default=None, max_length=8000)
    source: str | None = Field(default=None, max_length=2000)
    institution_type_applicability: list[str] | None = None
    paths: list[MacroPathIn] | None = Field(default=None, max_length=1000)
    reason: str = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def _validate(self) -> Self:
        if self.paths is not None:
            if not self.paths:
                msg = "paths, when provided, must be non-empty"
                raise ValueError(msg)
            seen: set[tuple[str, int]] = set()
            for path in self.paths:
                key = (path.variable, path.year_index)
                if key in seen:
                    msg = f"duplicate path for ({path.variable}, year {path.year_index})"
                    raise ValueError(msg)
                seen.add(key)
        return self


class MacroScenarioTransition(ClosedModel):
    """Reason-carrying body for submit / archive."""

    reason: str = Field(min_length=1, max_length=1000)


class MacroScenarioApproval(ClosedModel):
    reason: str = Field(min_length=1, max_length=1000)


class MacroScenarioRead(ClosedModel):
    id: UUID
    organization_id: str
    bank_id: str | None
    code: str
    name: str
    description: str | None
    scenario_type: ScenarioType
    severity: Severity | None
    horizon_years: int
    narrative: str | None
    source: str | None
    status: ScenarioStatus
    version: int
    created_by: UUID | None
    approved_by: UUID | None
    approval_timestamp: datetime | None
    institution_type_applicability: list[str] | None
    paths: list[MacroPathRead]
    created_at: datetime
    updated_at: datetime


class MacroScenarioSummaryRead(ClosedModel):
    id: UUID
    bank_id: str | None
    code: str
    name: str
    scenario_type: ScenarioType
    severity: Severity | None
    horizon_years: int
    status: ScenarioStatus
    version: int
    path_count: int
    created_by: UUID | None
    approved_by: UUID | None
    created_at: datetime
    updated_at: datetime


class MacroScenarioListRead(ClosedModel):
    scenarios: list[MacroScenarioSummaryRead]
    total: int


class ScenarioTranslationRead(ClosedModel):
    scenario_id: UUID
    code: str
    status: ScenarioStatus
    module: MacroModule
    #: Stringified Decimals, like the Scenario Workbench read contracts.
    shocks: dict[str, str]
