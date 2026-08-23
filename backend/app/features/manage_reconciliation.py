"""The reconciliation escape valve, reachable through the product.

Audit 2026-08-22 D-20: ``reconciliation.grant_exception`` was built complete —
non-empty reason, positive ceiling, ordered window, four-eyes refusal of
self-approval, append-only audit event — and had no endpoint, no schema and no
caller outside a test factory. The balance-sheet identity control is
fail-closed, so a tenant whose canonical book carries a known, bounded defect
was barred from every filing act with no way to record the approved exception
except a manual database write. A control with no escape valve is not a
control; it is an outage.

These three routes ARE the valve. They record a governance decision, so both
mutations are approver-gated and the service writes the audit event; the read
exists because an examiner asking "on whose authority was this filed?" must be
able to see the live exception, its ceiling and its window without a query.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select

from app.api.deps import ApproverTenant, DbSession, Tenant, TenantContext
from app.models import Bank, ReconciliationException
from app.schemas.reconciliation import (
    ReconciliationExceptionCreate,
    ReconciliationExceptionListRead,
    ReconciliationExceptionRead,
    ReconciliationExceptionRevoke,
)
from app.services import reconciliation
from app.services.reconciliation import ReconciliationExceptionError

router = APIRouter(tags=["reconciliation"])


def _bank_or_404(db: DbSession, ctx: TenantContext, bank_id: str) -> Bank:
    bank = db.scalar(
        select(Bank).where(Bank.id == bank_id, Bank.organization_id == ctx.organization_id)
    )
    if bank is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bank not found.")
    return bank


def _conflict(exc: ReconciliationExceptionError) -> HTTPException:
    """The service's governance refusals are 409s, not 500s.

    Every message it raises is actionable by the caller (blank reason, absent
    ceiling, inverted window, self-approval), so it belongs in the response.
    """
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={"error_code": "reconciliation_exception_refused", "message": str(exc)},
    )


@router.get(
    "/banks/{bank_id}/reconciliation/exceptions",
    response_model=ReconciliationExceptionListRead,
    operation_id="listReconciliationExceptions",
)
def list_reconciliation_exceptions(
    bank_id: str,
    db: DbSession,
    ctx: Tenant,
    as_of: Annotated[date | None, Query()] = None,
    control: Annotated[str, Query()] = reconciliation.CONTROL_BALANCE_SHEET_IDENTITY,
) -> ReconciliationExceptionListRead:
    bank = _bank_or_404(db, ctx, bank_id)
    resolved_as_of = as_of or date.today()  # noqa: DTZ011 - date-only business resolution
    rows = list(
        db.scalars(
            select(ReconciliationException)
            .where(
                ReconciliationException.organization_id == ctx.organization_id,
                ReconciliationException.bank_id == bank.id,
                ReconciliationException.control == control,
            )
            .order_by(
                ReconciliationException.effective_from.desc(),
                ReconciliationException.id.desc(),
            )
        )
    )
    # The ACTIVE grant comes from the service, not from re-filtering the list:
    # "which exception would the filing gate apply?" has exactly one answer and
    # it must be the gate's own (widest ceiling first).
    active = reconciliation.active_exception(
        db, ctx.organization_id, bank.id, resolved_as_of, control=control
    )
    return ReconciliationExceptionListRead(
        bank_id=bank.id,
        control=control,
        as_of=resolved_as_of,
        active_exception_id=UUID(active.exception_id) if active is not None else None,
        exceptions=[ReconciliationExceptionRead.model_validate(row) for row in rows],
    )


@router.post(
    "/banks/{bank_id}/reconciliation/exceptions",
    response_model=ReconciliationExceptionRead,
    status_code=status.HTTP_201_CREATED,
    operation_id="grantReconciliationException",
)
def grant_reconciliation_exception(
    bank_id: str,
    payload: ReconciliationExceptionCreate,
    db: DbSession,
    ctx: ApproverTenant,
) -> ReconciliationExceptionRead:
    bank = _bank_or_404(db, ctx, bank_id)
    try:
        row = reconciliation.grant_exception(
            db,
            ctx,
            bank,
            reason=payload.reason,
            approved_by=payload.approved_by,
            approved_by_user_id=payload.approved_by_user_id,
            max_gap_fraction=payload.max_gap_fraction,
            effective_from=payload.effective_from,
            effective_to=payload.effective_to,
        )
    except ReconciliationExceptionError as exc:
        raise _conflict(exc) from exc
    db.commit()
    db.refresh(row)
    return ReconciliationExceptionRead.model_validate(row)


@router.post(
    "/banks/{bank_id}/reconciliation/exceptions/{exception_id}/revoke",
    response_model=ReconciliationExceptionRead,
    operation_id="revokeReconciliationException",
)
def revoke_reconciliation_exception(
    bank_id: str,
    exception_id: UUID,
    payload: ReconciliationExceptionRevoke,
    db: DbSession,
    ctx: ApproverTenant,
) -> ReconciliationExceptionRead:
    bank = _bank_or_404(db, ctx, bank_id)
    try:
        row = reconciliation.revoke_exception(
            db,
            ctx,
            bank,
            exception_id,
            revoked_by=payload.revoked_by,
            reason=payload.reason,
        )
    except ReconciliationExceptionError as exc:
        raise _conflict(exc) from exc
    db.commit()
    db.refresh(row)
    return ReconciliationExceptionRead.model_validate(row)
