from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.domain.risk_constants import (
    FINDING_STATUSES,
    MANUAL_FINDING_SOURCE,
    RISK_TYPES,
    SEVERITIES,
    FindingStatus,
)
from app.models import Document, DocumentChunk, RiskFinding, RiskFindingEvidence
from app.schemas.common import JsonObject
from app.services.audit import record_event
from app.services.cases import ensure_case_is_not_archived, get_case_or_404


@dataclass(frozen=True)
class FindingFilters:
    case_id: UUID | None = None
    assessment_id: UUID | None = None
    status: str | None = None
    severity: str | None = None
    risk_type: str | None = None


@dataclass(frozen=True)
class CreateFindingCommand:
    risk_type: str
    title: str
    summary: str
    severity: str
    rationale: str | None
    likelihood: str | None
    impact: str | None
    confidence: Decimal | None
    details: JsonObject


@dataclass(frozen=True)
class UpdateFindingCommand:
    update_data: dict[str, str | None]
    fields_set: set[str]


@dataclass(frozen=True)
class EvidenceDocument:
    id: UUID
    filename: str
    status: str
    parse_status: str


@dataclass(frozen=True)
class EvidenceChunk:
    id: UUID
    chunk_index: int
    page_start: int | None
    page_end: int | None


@dataclass(frozen=True)
class EvidenceResult:
    id: UUID
    finding_id: UUID
    document_id: UUID | None
    document_chunk_id: UUID | None
    page_number: int | None
    quote: str | None
    locator: JsonObject
    relevance: Decimal | None
    created_at: datetime
    document: EvidenceDocument | None
    chunk: EvidenceChunk | None


def get_finding_or_404(db: Session, organization_id: UUID, finding_id: UUID) -> RiskFinding:
    finding = db.scalar(
        select(RiskFinding).where(
            RiskFinding.id == finding_id,
            RiskFinding.organization_id == organization_id,
        )
    )
    if finding is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Finding not found.")
    return finding


def list_findings(db: Session, ctx: TenantContext, filters: FindingFilters) -> list[RiskFinding]:
    stmt = select(RiskFinding).where(RiskFinding.organization_id == ctx.organization_id)
    if filters.case_id is not None:
        stmt = stmt.where(RiskFinding.case_id == filters.case_id)
    if filters.assessment_id is not None:
        stmt = stmt.where(RiskFinding.assessment_id == filters.assessment_id)
    if filters.status is not None:
        stmt = stmt.where(RiskFinding.status == filters.status)
    if filters.severity is not None:
        stmt = stmt.where(RiskFinding.severity == filters.severity)
    if filters.risk_type is not None:
        stmt = stmt.where(RiskFinding.risk_type == filters.risk_type)
    return list(db.scalars(stmt.order_by(RiskFinding.created_at.desc())))


def list_case_findings(db: Session, ctx: TenantContext, case_id: UUID) -> list[RiskFinding]:
    get_case_or_404(db, ctx.organization_id, case_id)
    return list_findings(db, ctx, FindingFilters(case_id=case_id))


def create_case_finding(
    db: Session, ctx: TenantContext, case_id: UUID, command: CreateFindingCommand
) -> RiskFinding:
    case = get_case_or_404(db, ctx.organization_id, case_id)
    ensure_case_is_not_archived(case)
    if command.risk_type not in RISK_TYPES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid risk type.")
    if command.severity not in SEVERITIES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid severity.")
    finding = RiskFinding(
        organization_id=ctx.organization_id,
        case_id=case.id,
        risk_type=command.risk_type,
        title=command.title,
        summary=command.summary,
        rationale=command.rationale,
        severity=command.severity,
        likelihood=command.likelihood,
        impact=command.impact,
        confidence=command.confidence,
        status=FindingStatus.OPEN.value,
        source=MANUAL_FINDING_SOURCE,
        details=command.details,
    )
    db.add(finding)
    db.flush()
    record_event(
        db,
        ctx,
        event_type="finding.created",
        entity_type="risk_finding",
        entity_id=finding.id,
        details={"case_id": str(case.id), "source": finding.source},
    )
    db.commit()
    db.refresh(finding)
    return finding


def update_finding(
    db: Session, ctx: TenantContext, finding_id: UUID, command: UpdateFindingCommand
) -> RiskFinding:
    disallowed = {"title", "summary", "severity", "rationale"} & command.fields_set
    if disallowed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Generated finding fields cannot be updated through this endpoint.",
        )
    finding = get_finding_or_404(db, ctx.organization_id, finding_id)
    case = get_case_or_404(db, ctx.organization_id, finding.case_id)
    ensure_case_is_not_archived(case)
    update_data = command.update_data
    status_value = update_data.get("status")
    if "status" in update_data and (
        not isinstance(status_value, str) or status_value not in FINDING_STATUSES
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid finding status."
        )
    if status_value == FindingStatus.ACCEPTED.value:
        status_value = FindingStatus.ACKNOWLEDGED.value
    disposition_reason = update_data.get("disposition_reason", finding.disposition_reason)
    if status_value == FindingStatus.DISMISSED.value and not disposition_reason:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Dismissed findings require a disposition reason.",
        )
    before_status = finding.status
    if "status" in update_data:
        assert isinstance(status_value, str)
        finding.status = status_value
    if "disposition_reason" in update_data:
        finding.disposition_reason = update_data["disposition_reason"]
    if "status" in update_data and finding.status != before_status:
        record_event(
            db,
            ctx,
            event_type="finding.status_changed",
            entity_type="risk_finding",
            entity_id=finding.id,
            details={"before": {"status": before_status}, "after": {"status": finding.status}},
        )
    db.commit()
    db.refresh(finding)
    return finding


def list_finding_evidence(
    db: Session, ctx: TenantContext, finding_id: UUID
) -> list[EvidenceResult]:
    get_finding_or_404(db, ctx.organization_id, finding_id)
    rows = db.execute(
        select(RiskFindingEvidence, Document, DocumentChunk)
        .outerjoin(
            Document,
            and_(
                Document.id == RiskFindingEvidence.document_id,
                Document.organization_id == ctx.organization_id,
            ),
        )
        .outerjoin(
            DocumentChunk,
            and_(
                DocumentChunk.id == RiskFindingEvidence.document_chunk_id,
                DocumentChunk.organization_id == ctx.organization_id,
            ),
        )
        .where(
            RiskFindingEvidence.organization_id == ctx.organization_id,
            RiskFindingEvidence.finding_id == finding_id,
        )
        .order_by(RiskFindingEvidence.created_at.asc())
    ).all()
    return [
        EvidenceResult(
            id=evidence.id,
            finding_id=evidence.finding_id,
            document_id=evidence.document_id,
            document_chunk_id=evidence.document_chunk_id,
            page_number=evidence.page_number,
            quote=evidence.quote,
            locator=evidence.locator,
            relevance=evidence.relevance,
            created_at=evidence.created_at,
            document=None
            if document is None
            else EvidenceDocument(
                id=document.id,
                filename=document.filename,
                status=document.status,
                parse_status=document.parse_status,
            ),
            chunk=None
            if chunk is None
            else EvidenceChunk(
                id=chunk.id,
                chunk_index=chunk.chunk_index,
                page_start=chunk.page_start,
                page_end=chunk.page_end,
            ),
        )
        for evidence, document, chunk in rows
    ]
