from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

type ScenarioType = Literal["baseline", "downside", "custom"]
type AssumptionCategory = Literal[
    "growth", "expenses", "cash_flow_timing", "credit_usage", "repayment_behavior", "other"
]
type AssumptionReviewStatus = Literal["draft", "reviewed"]
type AssumptionValue = str | int | float | bool | None
type ScenarioProvenance = dict[str, AssumptionValue]
type ChangeReason = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)
]


class ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ScenarioAssumptionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    organization_id: UUID
    case_id: UUID
    scenario_id: UUID
    category: AssumptionCategory
    key: str
    label: str
    value: AssumptionValue
    unit: str | None
    provenance: ScenarioProvenance
    review_status: AssumptionReviewStatus
    reviewed_by: UUID | None
    reviewed_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ScenarioRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    organization_id: UUID
    case_id: UUID
    name: str
    description: str | None
    scenario_type: ScenarioType
    copied_from_scenario_id: UUID | None
    created_by: UUID | None
    archived_at: datetime | None
    created_at: datetime
    updated_at: datetime
    assumptions: list[ScenarioAssumptionRead]


class ScenarioCreate(ClosedModel):
    name: str = Field(min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=2000)
    scenario_type: Literal["custom"] = "custom"
    reason: ChangeReason


class ScenarioUpdate(ClosedModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=2000)
    reason: ChangeReason

    @model_validator(mode="after")
    def require_update(self) -> ScenarioUpdate:
        if not (set(self.model_fields_set) - {"reason"}):
            raise ValueError("At least one scenario field is required.")
        return self


class ScenarioInitialize(ClosedModel):
    reason: ChangeReason


class ScenarioCopy(ClosedModel):
    name: str = Field(min_length=1, max_length=160)
    reason: ChangeReason


class ScenarioArchive(ClosedModel):
    reason: ChangeReason


class AssumptionCreate(ClosedModel):
    category: AssumptionCategory
    key: str = Field(min_length=1, max_length=120, pattern=r"^[a-z][a-z0-9_]*$")
    label: str = Field(min_length=1, max_length=160)
    value: AssumptionValue
    unit: str | None = Field(default=None, max_length=40)
    provenance: ScenarioProvenance = Field(default_factory=dict)
    reason: ChangeReason


class AssumptionUpdate(ClosedModel):
    category: AssumptionCategory = "other"
    label: str = Field(default="", min_length=1, max_length=160)
    value: AssumptionValue = None
    unit: str | None = Field(default=None, max_length=40)
    provenance: ScenarioProvenance = Field(default_factory=dict)
    reason: ChangeReason

    @model_validator(mode="after")
    def require_update(self) -> AssumptionUpdate:
        if not (set(self.model_fields_set) - {"reason"}):
            raise ValueError("At least one assumption field is required.")
        return self


class AssumptionReview(ClosedModel):
    reason: ChangeReason


class ScenarioValidationIssue(ClosedModel):
    code: str
    message: str
    category: AssumptionCategory | None = None
    assumption_id: UUID | None = None


class ScenarioValidationRead(ClosedModel):
    scenario_id: UUID
    complete: bool
    issue_count: int
    issues: list[ScenarioValidationIssue]


class ScenarioReadinessRead(ClosedModel):
    case_id: UUID
    ready: bool
    scenario_count: int
    complete_scenario_count: int
    incomplete_scenario_ids: list[UUID]


class ScenarioWorkspaceRead(ClosedModel):
    case_id: UUID
    scenarios: list[ScenarioRead]
    readiness: ScenarioReadinessRead


class ScenarioMutationResponse(ClosedModel):
    scenario: ScenarioRead
    validation: ScenarioValidationRead
    readiness: ScenarioReadinessRead
