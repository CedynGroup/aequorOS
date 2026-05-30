from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.domain.risk_constants import FindingStatus
from app.schemas.common import JsonObject
from app.services import findings as findings_service


class FindingRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    organization_id: UUID
    case_id: UUID
    assessment_id: UUID | None
    run_id: UUID | None
    risk_type: str
    title: str
    summary: str
    rationale: str | None
    severity: str
    likelihood: str | None
    impact: str | None
    confidence: Decimal | None
    status: str
    disposition_reason: str | None
    source: str
    rule_id: str | None
    rule_version: str | None
    score_impact: int | None
    details: JsonObject
    created_at: datetime
    updated_at: datetime


class FindingUpdate(BaseModel):
    model_config = ConfigDict(extra="allow")

    status: FindingStatus | None = None
    disposition_reason: str | None = None

    def to_command(self) -> findings_service.UpdateFindingCommand:
        update_data: dict[str, str | None] = {}
        if "status" in self.model_fields_set:
            update_data["status"] = self.status.value if self.status is not None else None
        if "disposition_reason" in self.model_fields_set:
            update_data["disposition_reason"] = self.disposition_reason
        for field_name in ("title", "summary", "severity", "rationale"):
            if self.model_extra is not None and field_name in self.model_extra:
                value = self.model_extra[field_name]
                update_data[field_name] = value if isinstance(value, str) else None
        extra_fields = set(self.model_extra or {})
        return findings_service.UpdateFindingCommand(
            update_data=update_data,
            fields_set=set(self.model_fields_set) | extra_fields,
        )


class FindingCreate(BaseModel):
    risk_type: str
    title: str
    summary: str
    severity: str
    rationale: str | None = None
    likelihood: str | None = None
    impact: str | None = None
    confidence: Decimal | None = None
    details: JsonObject = Field(default_factory=dict)

    def to_command(self) -> findings_service.CreateFindingCommand:
        return findings_service.CreateFindingCommand(
            risk_type=self.risk_type,
            title=self.title,
            summary=self.summary,
            severity=self.severity,
            rationale=self.rationale,
            likelihood=self.likelihood,
            impact=self.impact,
            confidence=self.confidence,
            details=self.details,
        )


class EvidenceDocumentRead(BaseModel):
    id: UUID
    filename: str
    status: str
    parse_status: str


class EvidenceChunkRead(BaseModel):
    id: UUID
    chunk_index: int
    page_start: int | None
    page_end: int | None


class EvidenceRead(BaseModel):
    id: UUID
    finding_id: UUID
    document_id: UUID | None
    document_chunk_id: UUID | None
    page_number: int | None
    quote: str | None
    locator: JsonObject
    relevance: Decimal | None
    created_at: datetime
    document: EvidenceDocumentRead | None
    chunk: EvidenceChunkRead | None
