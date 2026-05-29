from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.features.audit import record_event
from app.features.cases_service import get_case_or_404
from app.features.constants import FINDING_STATUSES
from app.models import Document, DocumentChunk, RiskFinding, RiskFindingEvidence


@dataclass(frozen=True)
class FindingFilters:
    case_id: UUID | None = None
    assessment_id: UUID | None = None
    status: str | None = None
    severity: str | None = None
    risk_type: str | None = None


@dataclass(frozen=True)
class EvidenceResult:
    id: UUID
    finding_id: UUID
    document_id: UUID | None
    document_chunk_id: UUID | None
    page_number: int | None
    quote: str | None
    locator: dict[str, object]
    relevance: Decimal | None
    created_at: datetime
    document: dict[str, object] | None
    chunk: dict[str, object] | None


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


def update_finding(db: Session, ctx: TenantContext, finding_id: UUID, payload) -> RiskFinding:
    disallowed = {"title", "summary", "severity", "rationale"} & payload.model_fields_set
    if disallowed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Generated finding fields cannot be updated through this endpoint.",
        )
    finding = get_finding_or_404(db, ctx.organization_id, finding_id)
    update_data = payload.model_dump(exclude_unset=True)
    if "status" in update_data and update_data["status"] not in FINDING_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid finding status."
        )
    before_status = finding.status
    if "status" in update_data:
        finding.status = update_data["status"]
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
            else {
                "id": document.id,
                "filename": document.filename,
                "status": document.status,
                "parse_status": document.parse_status,
            },
            chunk=None
            if chunk is None
            else {
                "id": chunk.id,
                "chunk_index": chunk.chunk_index,
                "page_start": chunk.page_start,
                "page_end": chunk.page_end,
            },
        )
        for evidence, document, chunk in rows
    ]
