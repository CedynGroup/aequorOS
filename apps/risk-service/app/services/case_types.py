from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import ClassVar
from uuid import UUID

from app.domain.risk_constants import CaseDecision, CaseStatus, RiskLevel
from app.models import RiskCase
from app.schemas.common import JsonObject


@dataclass(frozen=True)
class CaseFilters:
    include_archived: bool = False
    status: str | None = None
    assigned_to_user_id: UUID | None = None
    decision: str | None = None
    risk_level: str | None = None
    q: str | None = None
    sort: str = "created_at_desc"
    limit: int = 50
    offset: int = 0


@dataclass(frozen=True)
class CreateCaseCommand:
    title: str
    case_type: str
    subject_type: str | None
    subject_name: str | None
    description: str | None
    status: str
    metadata: JsonObject


@dataclass(frozen=True)
class UpdateCaseCommand:
    update_data: dict[str, str | None]


class BulkCaseAction(StrEnum):
    ASSIGN = "assign"
    UNASSIGN = "unassign"
    ARCHIVE = "archive"
    UPDATE_STATUS = "update_status"


@dataclass(frozen=True)
class BulkCaseActionCommandBase:
    case_ids: list[UUID]


@dataclass(frozen=True)
class BulkAssignCaseActionCommand(BulkCaseActionCommandBase):
    action: ClassVar[BulkCaseAction] = BulkCaseAction.ASSIGN
    assigned_to_user_id: UUID


@dataclass(frozen=True)
class BulkUnassignCaseActionCommand(BulkCaseActionCommandBase):
    action: ClassVar[BulkCaseAction] = BulkCaseAction.UNASSIGN


@dataclass(frozen=True)
class BulkArchiveCaseActionCommand(BulkCaseActionCommandBase):
    action: ClassVar[BulkCaseAction] = BulkCaseAction.ARCHIVE


@dataclass(frozen=True)
class BulkUpdateStatusCaseActionCommand(BulkCaseActionCommandBase):
    action: ClassVar[BulkCaseAction] = BulkCaseAction.UPDATE_STATUS
    status: CaseStatus


type BulkCaseActionCommand = (
    BulkAssignCaseActionCommand
    | BulkUnassignCaseActionCommand
    | BulkArchiveCaseActionCommand
    | BulkUpdateStatusCaseActionCommand
)


@dataclass(frozen=True)
class RecordDecisionCommand:
    decision: str
    reason: str | None


@dataclass(frozen=True)
class CaseQueueItem:
    case: RiskCase
    findings_count: int
    open_findings_count: int
    assignee_display_name: str | None
    assignee_email: str | None
    score_run_reference: str | None


@dataclass(frozen=True)
class CaseListResult:
    items: list[CaseQueueItem]
    total: int
    limit: int
    offset: int
    page: int
    pages: int
    has_more: bool


@dataclass(frozen=True)
class CaseSummary:
    total_cases: int
    archived_cases: int
    unassigned_cases: int
    completed_cases: int
    open_findings: int
    by_status: dict[str, int]
    by_assignee: dict[str, int]
    by_decision: dict[str, int]
    by_risk_level: dict[str, int]


@dataclass(frozen=True)
class CaseSnapshot:
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
    metadata_: JsonObject
    created_by: UUID | None
    created_at: datetime
    updated_at: datetime
    archived_at: datetime | None


@dataclass(frozen=True)
class BulkCaseActionSuccess:
    case_id: UUID
    status: CaseStatus
    case: CaseSnapshot


class BulkCaseActionFailureCode(StrEnum):
    BAD_REQUEST = "bad_request"
    UNAUTHORIZED = "unauthorized"
    NOT_FOUND = "not_found"
    CONFLICT = "conflict"
    HTTP_ERROR = "http_error"


@dataclass(frozen=True)
class BulkCaseActionFailure:
    case_id: UUID
    status_code: int
    code: BulkCaseActionFailureCode
    message: str


@dataclass(frozen=True)
class BulkCaseActionResult:
    succeeded: list[BulkCaseActionSuccess]
    failed: list[BulkCaseActionFailure]
