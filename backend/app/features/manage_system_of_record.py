"""System-of-record register + canonical withdrawal endpoints.

The register (GET/POST/approve/revoke) records which source system owns which
position type, and the assessment resolves it against the duplicated-book
diagnosis. The withdrawal endpoints are the remedy the platform has been
prescribing without being able to perform.

Role split mirrors the rest of the submission pipeline: an analyst PROPOSES a
declaration and REQUESTS a withdrawal; an approver approves, revokes or reverses.
No endpoint applies anything on its own, and none is reachable by a job.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query

from app.api.deps import ApproverTenant, DbSession, MutationTenant, Tenant
from app.domain.ingestion.constants import PositionType, SourceSystem
from app.models import CanonicalWithdrawal, SystemOfRecordDeclaration
from app.schemas.system_of_record import (
    CanonicalWithdrawalApproveRequest,
    CanonicalWithdrawalCreate,
    CanonicalWithdrawalListRead,
    CanonicalWithdrawalRead,
    CanonicalWithdrawalReverseRequest,
    CanonicalWithdrawalScopeRead,
    SourceBookRead,
    SystemOfRecordApproveRequest,
    SystemOfRecordAssessmentRead,
    SystemOfRecordDeclarationCreate,
    SystemOfRecordDeclarationRead,
    SystemOfRecordRegisterRead,
    SystemOfRecordRevokeRequest,
    TypeFindingRead,
)
from app.services import canonical_withdrawal, fact_derivation, system_of_record
from app.services.banks import _get_bank_or_404

router = APIRouter(tags=["system-of-record"])


def _declaration(row: SystemOfRecordDeclaration) -> SystemOfRecordDeclarationRead:
    return SystemOfRecordDeclarationRead.model_validate(row, from_attributes=True)


def _withdrawal(row: CanonicalWithdrawal) -> CanonicalWithdrawalRead:
    return CanonicalWithdrawalRead.model_validate(row, from_attributes=True)


# ---------------------------------------------------------------------------
# Register
# ---------------------------------------------------------------------------


@router.get(
    "/banks/{bank_id}/system-of-record",
    response_model=SystemOfRecordRegisterRead,
    operation_id="listSystemOfRecordDeclarations",
)
def list_system_of_record_declarations(
    bank_id: str,
    db: DbSession,
    ctx: Tenant,
    position_type: Annotated[PositionType | None, Query()] = None,
) -> SystemOfRecordRegisterRead:
    bank = _get_bank_or_404(db, ctx, bank_id)
    rows = system_of_record.list_declarations(
        db, ctx.organization_id, bank.id, position_type=position_type
    )
    return SystemOfRecordRegisterRead(
        bank_id=bank.id, declarations=[_declaration(row) for row in rows]
    )


@router.post(
    "/banks/{bank_id}/system-of-record",
    response_model=SystemOfRecordDeclarationRead,
    status_code=201,
    operation_id="proposeSystemOfRecordDeclaration",
)
def propose_system_of_record_declaration(
    bank_id: str,
    payload: SystemOfRecordDeclarationCreate,
    db: DbSession,
    ctx: MutationTenant,
) -> SystemOfRecordDeclarationRead:
    bank = _get_bank_or_404(db, ctx, bank_id)
    row = system_of_record.propose(
        db,
        ctx,
        bank,
        position_type=payload.position_type,
        source_system=payload.source_system,
        effective_from=payload.effective_from,
        source_citation=payload.source_citation,
        rationale=payload.rationale,
        proposed_by=payload.proposed_by,
        confirmation_status=payload.confirmation_status,
    )
    db.commit()
    db.refresh(row)
    return _declaration(row)


@router.post(
    "/banks/{bank_id}/system-of-record/{declaration_id}/approve",
    response_model=SystemOfRecordDeclarationRead,
    operation_id="approveSystemOfRecordDeclaration",
)
def approve_system_of_record_declaration(
    bank_id: str,
    declaration_id: UUID,
    payload: SystemOfRecordApproveRequest,
    db: DbSession,
    ctx: ApproverTenant,
) -> SystemOfRecordDeclarationRead:
    _get_bank_or_404(db, ctx, bank_id)
    row = system_of_record.approve(db, ctx, declaration_id, approved_by=payload.approved_by)
    db.commit()
    db.refresh(row)
    return _declaration(row)


@router.post(
    "/banks/{bank_id}/system-of-record/{declaration_id}/revoke",
    response_model=SystemOfRecordDeclarationRead,
    operation_id="revokeSystemOfRecordDeclaration",
)
def revoke_system_of_record_declaration(
    bank_id: str,
    declaration_id: UUID,
    payload: SystemOfRecordRevokeRequest,
    db: DbSession,
    ctx: ApproverTenant,
) -> SystemOfRecordDeclarationRead:
    _get_bank_or_404(db, ctx, bank_id)
    row = system_of_record.revoke(
        db, ctx, declaration_id, revoked_by=payload.revoked_by, reason=payload.reason
    )
    db.commit()
    db.refresh(row)
    return _declaration(row)


@router.get(
    "/banks/{bank_id}/system-of-record-assessment",
    response_model=SystemOfRecordAssessmentRead,
    operation_id="getSystemOfRecordAssessment",
)
def get_system_of_record_assessment(
    bank_id: str,
    db: DbSession,
    ctx: Tenant,
    as_of: Annotated[date, Query()],
) -> SystemOfRecordAssessmentRead:
    """Resolve the register against the duplicated-book diagnosis for a date.

    Read-only and side-effect free. A single-source bank comes back ``clean``
    with no findings — it is never asked for a declaration.
    """
    bank = _get_bank_or_404(db, ctx, bank_id)
    overlap = fact_derivation.diagnose_source_overlap(db, ctx, bank.id, as_of)
    assessment = system_of_record.assess(db, ctx, bank, as_of, overlap)
    return SystemOfRecordAssessmentRead(
        bank_id=assessment.bank_id,
        as_of_date=assessment.as_of_date,
        clean=assessment.clean,
        contested_types=assessment.contested_types,
        findings=[
            TypeFindingRead(
                position_type=finding.position_type,
                finding=finding.finding,  # type: ignore[arg-type]
                declared_source_system=finding.declared_source_system,
                declaration_id=finding.declaration_id,
                declaration_confirmation_status=finding.declaration_confirmation_status,
                offending_rows=finding.offending_rows,
                offending_total=finding.offending_total,
                books=[
                    SourceBookRead(
                        source_system=book.source_system, rows=book.rows, total=book.total
                    )
                    for book in finding.books
                ],
            )
            for finding in assessment.findings
        ],
        message=assessment.message(),
    )


# ---------------------------------------------------------------------------
# Withdrawal
# ---------------------------------------------------------------------------


@router.get(
    "/banks/{bank_id}/canonical-withdrawals",
    response_model=CanonicalWithdrawalListRead,
    operation_id="listCanonicalWithdrawals",
)
def list_canonical_withdrawals(
    bank_id: str,
    db: DbSession,
    ctx: Tenant,
    as_of: Annotated[date | None, Query()] = None,
    withdrawal_status: Annotated[str | None, Query()] = None,
) -> CanonicalWithdrawalListRead:
    bank = _get_bank_or_404(db, ctx, bank_id)
    rows = canonical_withdrawal.list_withdrawals(
        db, ctx.organization_id, bank.id, as_of_date=as_of, withdrawal_status=withdrawal_status
    )
    return CanonicalWithdrawalListRead(
        bank_id=bank.id, withdrawals=[_withdrawal(row) for row in rows]
    )


@router.get(
    "/banks/{bank_id}/canonical-withdrawal-scope",
    response_model=CanonicalWithdrawalScopeRead,
    operation_id="getCanonicalWithdrawalScope",
)
def get_canonical_withdrawal_scope(  # noqa: PLR0913 - the scope IS the query
    bank_id: str,
    db: DbSession,
    ctx: Tenant,
    entity: Annotated[str, Query()],
    source_system: Annotated[SourceSystem, Query()],
    as_of: Annotated[date, Query()],
    position_type: Annotated[PositionType | None, Query()] = None,
) -> CanonicalWithdrawalScopeRead:
    """How many current records a withdrawal would retire — before requesting it."""
    bank = _get_bank_or_404(db, ctx, bank_id)
    rows_in_scope = canonical_withdrawal.count_in_scope(
        db,
        organization_id=ctx.organization_id,
        bank_id=bank.id,
        entity=entity,
        source_system=source_system,
        as_of_date=as_of,
        position_type=position_type,
    )
    return CanonicalWithdrawalScopeRead(
        bank_id=bank.id,
        entity=entity,
        source_system=source_system,
        as_of_date=as_of,
        position_type=position_type,
        rows_in_scope=rows_in_scope,
    )


@router.post(
    "/banks/{bank_id}/canonical-withdrawals",
    response_model=CanonicalWithdrawalRead,
    status_code=201,
    operation_id="requestCanonicalWithdrawal",
)
def request_canonical_withdrawal(
    bank_id: str,
    payload: CanonicalWithdrawalCreate,
    db: DbSession,
    ctx: MutationTenant,
) -> CanonicalWithdrawalRead:
    """Record a PENDING withdrawal request. Retires nothing."""
    bank = _get_bank_or_404(db, ctx, bank_id)
    row = canonical_withdrawal.request_withdrawal(
        db,
        ctx,
        bank,
        entity=payload.entity,
        source_system=payload.source_system,
        as_of_date=payload.as_of_date,
        reason=payload.reason,
        requested_by=payload.requested_by,
        position_type=payload.position_type,
        declaration_id=payload.declaration_id,
    )
    db.commit()
    db.refresh(row)
    return _withdrawal(row)


@router.post(
    "/banks/{bank_id}/canonical-withdrawals/{withdrawal_id}/approve",
    response_model=CanonicalWithdrawalRead,
    operation_id="approveCanonicalWithdrawal",
)
def approve_canonical_withdrawal(
    bank_id: str,
    withdrawal_id: UUID,
    payload: CanonicalWithdrawalApproveRequest,
    db: DbSession,
    ctx: ApproverTenant,
) -> CanonicalWithdrawalRead:
    """Apply a pending withdrawal. The approver must not be the requester."""
    bank = _get_bank_or_404(db, ctx, bank_id)
    row = canonical_withdrawal.approve_withdrawal(
        db, ctx, bank, withdrawal_id, approved_by=payload.approved_by
    )
    db.commit()
    db.refresh(row)
    return _withdrawal(row)


@router.post(
    "/banks/{bank_id}/canonical-withdrawals/{withdrawal_id}/reverse",
    response_model=CanonicalWithdrawalRead,
    operation_id="reverseCanonicalWithdrawal",
)
def reverse_canonical_withdrawal(
    bank_id: str,
    withdrawal_id: UUID,
    payload: CanonicalWithdrawalReverseRequest,
    db: DbSession,
    ctx: ApproverTenant,
) -> CanonicalWithdrawalRead:
    """Restore a withdrawn book, with its own reason and its own audit trail."""
    bank = _get_bank_or_404(db, ctx, bank_id)
    row = canonical_withdrawal.reverse_withdrawal(
        db, ctx, bank, withdrawal_id, reversed_by=payload.reversed_by, reason=payload.reason
    )
    db.commit()
    db.refresh(row)
    return _withdrawal(row)
