"""Reconciliation proposals, separate authenticated approvals and retained revocations.

DATA CREATE persists a pending request. DATA APPROVE belongs to a different
signed-in officer; a display label never establishes their authority. Metadata
reads require confidential VIEW and pending requests never affect filing gates.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select

from app.api.deps import DbSession, ScopedMutationTenant, Tenant
from app.core.authorization import Module, Permission, Sensitivity
from app.data_engine.schemas.reconciliation import (
    ReconciliationExceptionApprove,
    ReconciliationExceptionCreate,
    ReconciliationExceptionListRead,
    ReconciliationExceptionRead,
    ReconciliationExceptionRevoke,
)
from app.data_engine.service import reconciliation
from app.data_engine.service.access import require_data_access
from app.data_engine.service.reconciliation import ReconciliationExceptionError
from app.identity.public import resolve_bank
from app.models import ReconciliationException

router = APIRouter(tags=["reconciliation"])


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
    bank = require_data_access(db, ctx, bank_id, Permission.VIEW, Sensitivity.CONFIDENTIAL)
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
    operation_id="proposeReconciliationException",
)
def propose_reconciliation_exception(
    bank_id: str,
    payload: ReconciliationExceptionCreate,
    db: DbSession,
    ctx: ScopedMutationTenant,
) -> ReconciliationExceptionRead:
    bank = require_data_access(db, ctx, bank_id, Permission.CREATE, Sensitivity.CONFIDENTIAL)
    try:
        row = reconciliation.propose_exception(
            db,
            ctx,
            bank,
            reason=payload.reason,
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
    "/banks/{bank_id}/reconciliation/exceptions/{exception_id}/approve",
    response_model=ReconciliationExceptionRead,
    operation_id="approveReconciliationException",
)
def approve_reconciliation_exception(
    bank_id: str,
    exception_id: UUID,
    payload: ReconciliationExceptionApprove,
    db: DbSession,
    ctx: ScopedMutationTenant,
) -> ReconciliationExceptionRead:
    bank = resolve_bank(db, ctx, bank_id, module=Module.DATA)
    try:
        row = reconciliation.approve_exception(
            db, ctx, bank, exception_id, approved_by=payload.approved_by
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
    ctx: ScopedMutationTenant,
) -> ReconciliationExceptionRead:
    bank = resolve_bank(db, ctx, bank_id, module=Module.DATA)
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
