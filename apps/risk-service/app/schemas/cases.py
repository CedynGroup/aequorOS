from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.domain.risk_constants import CaseDecision, CaseStatus, RiskLevel
from app.schemas.common import JsonObject
from app.services import cases as cases_service


class CaseCreate(BaseModel):
    title: str
    case_type: str
    subject_type: str | None = None
    subject_name: str | None = None
    description: str | None = None
    status: CaseStatus = CaseStatus.DRAFT
    metadata: JsonObject = Field(default_factory=dict)

    def to_command(self) -> cases_service.CreateCaseCommand:
        return cases_service.CreateCaseCommand(
            title=self.title,
            case_type=self.case_type,
            subject_type=self.subject_type,
            subject_name=self.subject_name,
            description=self.description,
            status=self.status.value,
            metadata=self.metadata,
        )


class CaseUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    subject_type: str | None = None
    subject_name: str | None = None
    status: CaseStatus | None = None

    def to_command(self) -> cases_service.UpdateCaseCommand:
        update_data: dict[str, str | None] = {}
        if "title" in self.model_fields_set:
            update_data["title"] = self.title
        if "description" in self.model_fields_set:
            update_data["description"] = self.description
        if "subject_type" in self.model_fields_set:
            update_data["subject_type"] = self.subject_type
        if "subject_name" in self.model_fields_set:
            update_data["subject_name"] = self.subject_name
        if "status" in self.model_fields_set:
            update_data["status"] = self.status.value if self.status is not None else None
        return cases_service.UpdateCaseCommand(update_data=update_data)


class CaseAssign(BaseModel):
    assigned_to_user_id: UUID | None


class CaseDecisionCreate(BaseModel):
    decision: CaseDecision
    reason: str | None = None

    def to_command(self) -> cases_service.RecordDecisionCommand:
        return cases_service.RecordDecisionCommand(decision=self.decision.value, reason=self.reason)


class CaseRead(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: UUID
    organization_id: UUID
    title: str
    case_type: str
    subject_type: str | None
    subject_name: str | None
    description: str | None
    status: CaseStatus
    assigned_to_user_id: UUID | None
    assigned_at: datetime | None
    risk_score: int | None
    risk_level: RiskLevel | None
    scored_at: datetime | None
    scoring_version: str | None
    decision: CaseDecision | None
    decided_at: datetime | None
    metadata: JsonObject = Field(validation_alias="metadata_", serialization_alias="metadata")
    created_by: UUID | None
    created_at: datetime
    updated_at: datetime
    archived_at: datetime | None


class CaseQueueItemRead(BaseModel):
    id: UUID
    organization_id: UUID
    title: str
    case_type: str
    subject_type: str | None
    subject_name: str | None
    status: CaseStatus
    assigned_to_user_id: UUID | None
    assignee_display_name: str | None
    assignee_email: str | None
    risk_score: int | None
    risk_level: RiskLevel | None
    decision: CaseDecision | None
    findings_count: int
    open_findings_count: int
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_queue_item(cls, item: cases_service.CaseQueueItem) -> CaseQueueItemRead:
        case = item.case
        return cls(
            id=case.id,
            organization_id=case.organization_id,
            title=case.title,
            case_type=case.case_type,
            subject_type=case.subject_type,
            subject_name=case.subject_name,
            status=CaseStatus(case.status),
            assigned_to_user_id=case.assigned_to_user_id,
            assignee_display_name=item.assignee_display_name,
            assignee_email=item.assignee_email,
            risk_score=case.risk_score,
            risk_level=RiskLevel(case.risk_level) if case.risk_level is not None else None,
            decision=CaseDecision(case.decision) if case.decision is not None else None,
            findings_count=item.findings_count,
            open_findings_count=item.open_findings_count,
            created_at=case.created_at,
            updated_at=case.updated_at,
        )


class CaseListRead(BaseModel):
    items: list[CaseQueueItemRead]
    total: int
    limit: int
    offset: int
    page: int
    pages: int
    has_more: bool

    @classmethod
    def from_result(cls, result: cases_service.CaseListResult) -> CaseListRead:
        return cls(
            items=[CaseQueueItemRead.from_queue_item(item) for item in result.items],
            total=result.total,
            limit=result.limit,
            offset=result.offset,
            page=result.page,
            pages=result.pages,
            has_more=result.has_more,
        )


class CaseTaxonomyRead(BaseModel):
    statuses: list[str]
    decisions: list[str]
    risk_levels: list[str]
    sort_options: list[str]


class CaseDecisionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    organization_id: UUID
    case_id: UUID
    decision: CaseDecision
    previous_decision: CaseDecision | None
    reason: str | None
    decided_by: UUID | None
    created_at: datetime


class ScoreRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    organization_id: UUID
    case_id: UUID
    assessment_id: UUID | None
    run_id: UUID | None
    score: int
    risk_level: RiskLevel
    scoring_version: str
    input_hash: str
    input_snapshot: JsonObject
    rule_results: list[JsonObject]
    created_at: datetime


class CaseSummaryRead(BaseModel):
    total_cases: int
    archived_cases: int
    unassigned_cases: int
    completed_cases: int
    open_findings: int
    by_status: dict[str, int]
    by_assignee: dict[str, int]
    by_decision: dict[str, int]
    by_risk_level: dict[str, int]
