"""Withdrawal: retiring a source system's canonical book for a business date.

This is the capability the platform prescribed and could not perform. The
duplicated-book diagnosis tells an operator to "withdraw the other system's data
for this date"; nothing could. ``superseded_by`` retires a row only by naming its
REPLACEMENT, and a duplicated book has no replacement — so there was no
representation for "this record is gone", and ``CanonicalPosition.superseded_by``
was never assigned anywhere in the codebase (0 of 571,984 rows on the primary
database at the time this was built).

Shape of the act
----------------
1. **Request** — an analyst names the scope ``(source_system, as_of_date, entity
   [, position_type])`` and a reason. Nothing is stamped. A request whose scope
   matches no current row is refused, so "withdrawn" never means "found nothing".
2. **Approve** — a DIFFERENT officer approves. Approval mints a real
   ``ingestion_batches`` row carrying a ``SUPERSESSION`` lineage node (the
   operation type has been declared since the Data Engine shipped and was never
   emitted) and stamps ``withdrawn_at`` / ``withdrawn_by_batch_id`` /
   ``withdrawal_reason`` on every current-generation row in scope.
3. **Reverse** — a later governed act with its own reason and its own batch and
   lineage node. The withdrawal record survives as ``status='reversed'``.

Invariants
----------
* **Never automatic.** No detector, job or heuristic calls into this module. The
  overlap detector is advisory; the system-of-record register names the
  violation; a human requests and a second human approves.
* **Never cross-source supersession.** Ingestion's per-source supersession
  (``ingestion.py``) is untouched — a bank legitimately splits its book across
  systems, and cross-source supersession would delete a real book. Withdrawal is
  an explicit governed act, not a widening of supersession.
* **Append-only.** No row is deleted and no business field is rewritten. The
  lifecycle marker is stamped exactly as ``superseded_by`` is stamped today, and
  the governing record, both lineage nodes and both audit events survive
  reversal.
* **Fail-closed downstream is preserved.** Withdrawing a book changes the
  balance sheet. If it no longer balances, the existing reconciliation control
  blocks the filing — this module adds no bypass.
"""

from __future__ import annotations

from datetime import date
from typing import Any, cast
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import CursorResult, and_, func, select, update
from sqlalchemy.orm import Session, aliased

from app.api.deps import TenantContext
from app.core.ids import new_uuid7
from app.db.base import utc_now
from app.models import (
    Bank,
    CanonicalCounterparty,
    CanonicalGlAccount,
    CanonicalPosition,
    CanonicalPositionSnapshot,
    CanonicalProduct,
    CanonicalWithdrawal,
    IngestionBatch,
    LineageRecord,
)
from app.models.canonical import CanonicalMetadataMixin, is_current_generation
from app.models.canonical_withdrawal import WITHDRAWABLE_ENTITIES
from app.services.audit import record_event
from app.services.withdrawal_impact import invalidate_register

REQUESTED_EVENT = "canonical_withdrawal.requested"
APPROVED_EVENT = "canonical_withdrawal.approved"
REVERSED_EVENT = "canonical_withdrawal.reversed"

#: Stamped on the withdrawal batch so a withdrawal is distinguishable from an
#: ingestion in ``ingestion_batches`` without reading its lineage.
WITHDRAWAL_ADAPTER_VERSION = "withdrawal_v1"

#: ``position`` maps to SNAPSHOTS, not identities: a withdrawal is dated, and the
#: snapshot is the dated record. See ``models/canonical_withdrawal.py``.
_ENTITY_MODELS: dict[str, type[CanonicalMetadataMixin]] = {
    "position": CanonicalPositionSnapshot,
    "gl_account": CanonicalGlAccount,
    "counterparty": CanonicalCounterparty,
    "product": CanonicalProduct,
}

#: Each entity's natural key among the current generation — the same columns its
#: partial unique index covers. Used to refuse a reversal that would resurrect a
#: duplicate after the withdrawn book was re-ingested.
_NATURAL_KEYS: dict[str, tuple[str, ...]] = {
    "position": ("organization_id", "position_id", "as_of_date"),
    "gl_account": ("organization_id", "bank_id", "account_code", "as_of_date"),
    "counterparty": (
        "organization_id",
        "bank_id",
        "source_system",
        "source_reference",
        "as_of_date",
    ),
    "product": ("organization_id", "bank_id", "product_code", "as_of_date"),
}

assert set(_ENTITY_MODELS) == set(WITHDRAWABLE_ENTITIES)
assert set(_NATURAL_KEYS) == set(WITHDRAWABLE_ENTITIES)


class WithdrawalError(HTTPException):
    """A refused withdrawal, surfaced to the API as a precise status."""


def _model_for(entity: str) -> type[CanonicalMetadataMixin]:
    model = _ENTITY_MODELS.get(entity)
    if model is None:
        raise WithdrawalError(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                f"{entity!r} is not a withdrawable canonical entity. "
                f"Withdrawable entities: {', '.join(WITHDRAWABLE_ENTITIES)}."
            ),
        )
    return model


def _scope_predicates(  # noqa: PLR0913 - the scope IS the argument list
    model: type[CanonicalMetadataMixin],
    *,
    organization_id: str,
    bank_id: str,
    source_system: str,
    as_of_date: date,
    position_type: str | None,
) -> list[Any]:
    """The current-generation rows a withdrawal scope selects."""
    predicates: list[Any] = [
        model.organization_id == organization_id,
        model.bank_id == bank_id,
        model.source_system == source_system,
        model.as_of_date == as_of_date,
        # Already-withdrawn rows are excluded, so a second withdrawal over the
        # same scope retires nothing and is refused rather than double-stamping.
        *is_current_generation(model),
    ]
    if position_type is not None:
        if model is not CanonicalPositionSnapshot:
            raise WithdrawalError(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="position_type narrows a 'position' withdrawal only.",
            )
        predicates.append(
            CanonicalPositionSnapshot.position_id.in_(
                select(CanonicalPosition.id).where(
                    CanonicalPosition.organization_id == organization_id,
                    CanonicalPosition.bank_id == bank_id,
                    CanonicalPosition.position_type == position_type,
                )
            )
        )
    return predicates


def count_in_scope(  # noqa: PLR0913 - the scope IS the argument list
    db: Session,
    *,
    organization_id: str,
    bank_id: str,
    entity: str,
    source_system: str,
    as_of_date: date,
    position_type: str | None = None,
) -> int:
    """Current-generation rows a withdrawal would retire. Read-only."""
    model = _model_for(entity)
    return int(
        db.scalar(
            select(func.count())
            .select_from(model)
            .where(
                *_scope_predicates(
                    model,
                    organization_id=organization_id,
                    bank_id=bank_id,
                    source_system=source_system,
                    as_of_date=as_of_date,
                    position_type=position_type,
                )
            )
        )
        or 0
    )


# ---------------------------------------------------------------------------
# Maker step
# ---------------------------------------------------------------------------


def request_withdrawal(  # noqa: PLR0913 - a governed act names every field
    db: Session,
    ctx: TenantContext,
    bank: Bank,
    *,
    entity: str,
    source_system: str,
    as_of_date: date,
    reason: str,
    requested_by: str,
    position_type: str | None = None,
    declaration_id: UUID | None = None,
) -> CanonicalWithdrawal:
    """Record a PENDING withdrawal. Stamps nothing; retires nothing."""
    if not reason or not reason.strip():
        raise WithdrawalError(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                "A withdrawal requires a non-empty reason: it removes data from every "
                "filed number derived for this date."
            ),
        )
    if not requested_by or not requested_by.strip():
        raise WithdrawalError(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="A withdrawal requires a named requester.",
        )
    _model_for(entity)
    in_scope = count_in_scope(
        db,
        organization_id=ctx.organization_id,
        bank_id=bank.id,
        entity=entity,
        source_system=source_system,
        as_of_date=as_of_date,
        position_type=position_type,
    )
    if in_scope == 0:
        # "Withdrawn" must never quietly mean "matched nothing".
        raise WithdrawalError(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                f"No current {entity} records from {source_system} exist for "
                f"{as_of_date.isoformat()}"
                + (f" of type {position_type}" if position_type else "")
                + ". There is nothing to withdraw."
            ),
        )
    now = utc_now()
    row = CanonicalWithdrawal(
        organization_id=ctx.organization_id,
        bank_id=bank.id,
        source_system=source_system,
        as_of_date=as_of_date,
        entity=entity,
        position_type=position_type,
        reason=reason.strip(),
        declaration_id=declaration_id,
        status="pending",
        requested_by=requested_by.strip(),
        requested_by_user_id=ctx.actor_user_id,
        requested_at=now,
    )
    db.add(row)
    db.flush()
    record_event(
        db,
        ctx,
        event_type=REQUESTED_EVENT,
        entity_type="canonical_withdrawal",
        entity_id=row.id,
        details={
            "bank_id": bank.id,
            "entity": entity,
            "source_system": source_system,
            "as_of_date": as_of_date.isoformat(),
            "position_type": position_type,
            "reason": row.reason,
            "requested_by": row.requested_by,
            "rows_in_scope": in_scope,
            "declaration_id": str(declaration_id) if declaration_id else None,
        },
    )
    db.flush()
    return row


# ---------------------------------------------------------------------------
# Checker step — the only path that stamps a canonical row
# ---------------------------------------------------------------------------


def approve_withdrawal(
    db: Session,
    ctx: TenantContext,
    bank: Bank,
    withdrawal_id: UUID,
    *,
    approved_by: str,
) -> CanonicalWithdrawal:
    """Approve and apply a pending withdrawal. Approver must not be the requester."""
    row = get_withdrawal(db, ctx.organization_id, withdrawal_id)
    if row.status != "pending":
        raise WithdrawalError(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Withdrawal {withdrawal_id} is already {row.status}.",
        )
    if row.bank_id != bank.id:
        raise WithdrawalError(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Withdrawal {withdrawal_id} does not belong to bank {bank.id}.",
        )
    if not approved_by or not approved_by.strip():
        raise WithdrawalError(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="A withdrawal requires a named approver.",
        )
    if approved_by.strip().lower() == row.requested_by.strip().lower() or (
        ctx.actor_user_id is not None
        and row.requested_by_user_id is not None
        and ctx.actor_user_id == row.requested_by_user_id
    ):
        raise WithdrawalError(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                "A withdrawal cannot be approved by the officer who requested it — "
                "a second approver is required."
            ),
        )

    model = _model_for(row.entity)
    predicates = _scope_predicates(
        model,
        organization_id=row.organization_id,
        bank_id=row.bank_id,
        source_system=row.source_system,
        as_of_date=row.as_of_date,
        position_type=row.position_type,
    )
    # The batches that produced the book being retired — the precise provenance
    # a 139k-row `input_lineage_ids` list could never be.
    source_batches = [
        str(batch_id)
        for batch_id in db.scalars(
            select(model.ingestion_batch_id).where(*predicates).distinct()
        ).all()
    ]
    now = utc_now()
    batch = IngestionBatch(
        id=new_uuid7(),
        organization_id=row.organization_id,
        bank_id=row.bank_id,
        source_system=row.source_system,
        adapter_version=WITHDRAWAL_ADAPTER_VERSION,
        extraction_mode="full",
        status="accepted",
        as_of_date=row.as_of_date,
        content_hash=None,
        started_at=now,
        completed_at=now,
        created_by=ctx.actor_user_id,
        validation_report={
            "kind": "withdrawal",
            "withdrawal_id": str(row.id),
            "entity": row.entity,
            "position_type": row.position_type,
            "reason": row.reason,
            "requested_by": row.requested_by,
            "approved_by": approved_by.strip(),
        },
    )
    db.add(batch)
    db.flush()
    node = LineageRecord(
        organization_id=row.organization_id,
        ingestion_batch_id=batch.id,
        operation_type="SUPERSESSION",
        operation_ref=f"withdrawal/{row.id}",
        input_lineage_ids=[],
        details={
            "withdrawal_id": str(row.id),
            "entity": row.entity,
            "source_system": row.source_system,
            "as_of_date": row.as_of_date.isoformat(),
            "position_type": row.position_type,
            "reason": row.reason,
            "requested_by": row.requested_by,
            "approved_by": approved_by.strip(),
            "withdrawn_source_batch_ids": source_batches,
        },
    )
    db.add(node)
    db.flush()

    result = cast(
        "CursorResult[Any]",
        db.execute(
            update(model)
            .where(*predicates)
            .values(
                withdrawn_at=now,
                withdrawn_by_batch_id=batch.id,
                withdrawal_reason=row.reason,
            )
            .execution_options(synchronize_session=False)
        ),
    )
    withdrawn = int(result.rowcount or 0)
    if withdrawn == 0:
        # The scope emptied between request and approval (a re-ingestion
        # superseded it). Refusing beats recording an applied withdrawal that
        # retired nothing.
        raise WithdrawalError(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "The withdrawal scope no longer matches any current record — the book "
                "changed between request and approval. Re-request against current data."
            ),
        )

    row.status = "applied"
    row.approved_by = approved_by.strip()
    row.approved_by_user_id = ctx.actor_user_id
    row.approved_at = now
    row.withdrawal_batch_id = batch.id
    row.rows_withdrawn = withdrawn
    db.flush()
    # The register just changed, so any memo of it in this session is now a
    # pre-approval answer. `withdrawal_impact` derives every sealed run's
    # standing from that register; leaving a stale memo in place would let the
    # filing gate pass a run this act has just orphaned.
    invalidate_register(db)
    record_event(
        db,
        ctx,
        event_type=APPROVED_EVENT,
        entity_type="canonical_withdrawal",
        entity_id=row.id,
        details={
            "bank_id": row.bank_id,
            "entity": row.entity,
            "source_system": row.source_system,
            "as_of_date": row.as_of_date.isoformat(),
            "position_type": row.position_type,
            "reason": row.reason,
            "requested_by": row.requested_by,
            "approved_by": row.approved_by,
            "approved_at": now.isoformat(),
            "withdrawal_batch_id": str(batch.id),
            "lineage_id": str(node.id),
            "rows_withdrawn": withdrawn,
            "withdrawn_source_batch_ids": source_batches,
        },
    )
    db.flush()
    return row


# ---------------------------------------------------------------------------
# Reversal — another governed act, never a rollback
# ---------------------------------------------------------------------------


def reverse_withdrawal(  # noqa: PLR0913 - governed reversal evidence is explicit
    db: Session,
    ctx: TenantContext,
    bank: Bank,
    withdrawal_id: UUID,
    *,
    reversed_by: str,
    reason: str,
) -> CanonicalWithdrawal:
    """Restore a withdrawn book, recording who reversed it and why."""
    row = get_withdrawal(db, ctx.organization_id, withdrawal_id)
    if row.bank_id != bank.id:
        raise WithdrawalError(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Withdrawal {withdrawal_id} does not belong to bank {bank.id}.",
        )
    if row.status != "applied":
        raise WithdrawalError(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Only an applied withdrawal can be reversed; this one is {row.status}.",
        )
    if not reason or not reason.strip():
        raise WithdrawalError(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Reversing a withdrawal requires a non-empty reason.",
        )
    if not reversed_by or not reversed_by.strip():
        raise WithdrawalError(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Reversing a withdrawal requires a named officer.",
        )
    assert row.withdrawal_batch_id is not None  # CHECK-constrained for 'applied'

    model = _model_for(row.entity)
    restored_scope = [
        model.organization_id == row.organization_id,
        model.withdrawn_by_batch_id == row.withdrawal_batch_id,
    ]
    # Refuse a reversal that would resurrect a duplicate: if the withdrawn book
    # was re-ingested, its replacement holds the natural key and restoring would
    # violate the current-generation unique index (or, on SQLite, silently
    # double-count).
    key = _NATURAL_KEYS[row.entity]
    withdrawn_rows = aliased(model)
    live_rows = aliased(model)
    conflicts = int(
        db.scalar(
            select(func.count())
            .select_from(withdrawn_rows)
            .join(
                live_rows,
                and_(
                    *[
                        getattr(live_rows, column) == getattr(withdrawn_rows, column)
                        for column in key
                    ],
                    *is_current_generation(live_rows),
                ),
            )
            .where(
                withdrawn_rows.organization_id == row.organization_id,
                withdrawn_rows.withdrawn_by_batch_id == row.withdrawal_batch_id,
            )
        )
        or 0
    )
    if conflicts:
        raise WithdrawalError(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"{conflicts} record(s) withdrawn by this act have since been replaced by "
                "a newer ingestion. Restoring them would put two live records on the same "
                "natural key. Withdraw the replacement first, or leave this withdrawal in "
                "place."
            ),
        )

    now = utc_now()
    batch = IngestionBatch(
        id=new_uuid7(),
        organization_id=row.organization_id,
        bank_id=row.bank_id,
        source_system=row.source_system,
        adapter_version=WITHDRAWAL_ADAPTER_VERSION,
        extraction_mode="full",
        status="accepted",
        as_of_date=row.as_of_date,
        content_hash=None,
        started_at=now,
        completed_at=now,
        created_by=ctx.actor_user_id,
        validation_report={
            "kind": "withdrawal_reversal",
            "withdrawal_id": str(row.id),
            "entity": row.entity,
            "reason": reason.strip(),
            "reversed_by": reversed_by.strip(),
        },
    )
    db.add(batch)
    db.flush()
    node = LineageRecord(
        organization_id=row.organization_id,
        ingestion_batch_id=batch.id,
        operation_type="SUPERSESSION",
        operation_ref=f"withdrawal-reversal/{row.id}",
        input_lineage_ids=[],
        details={
            "withdrawal_id": str(row.id),
            "withdrawal_batch_id": str(row.withdrawal_batch_id),
            "entity": row.entity,
            "source_system": row.source_system,
            "as_of_date": row.as_of_date.isoformat(),
            "reason": reason.strip(),
            "reversed_by": reversed_by.strip(),
        },
    )
    db.add(node)
    db.flush()

    # The three markers clear together: a row carrying a withdrawal reason while
    # counting toward a filed number would be worse than no marker at all. The
    # evidence lives in this record, in both batches, in both lineage nodes and
    # in both (append-only) audit events.
    result = cast(
        "CursorResult[Any]",
        db.execute(
            update(model)
            .where(*restored_scope)
            .values(withdrawn_at=None, withdrawn_by_batch_id=None, withdrawal_reason=None)
            .execution_options(synchronize_session=False)
        ),
    )
    restored = int(result.rowcount or 0)

    row.status = "reversed"
    row.reversed_at = now
    row.reversed_by = reversed_by.strip()
    row.reversed_by_user_id = ctx.actor_user_id
    row.reversal_reason = reason.strip()
    row.reversal_batch_id = batch.id
    row.rows_restored = restored
    db.flush()
    # Same reason, the other direction: a session holding the pre-reversal memo
    # would keep refusing runs this act has just restored.
    invalidate_register(db)
    record_event(
        db,
        ctx,
        event_type=REVERSED_EVENT,
        entity_type="canonical_withdrawal",
        entity_id=row.id,
        details={
            "bank_id": row.bank_id,
            "entity": row.entity,
            "source_system": row.source_system,
            "as_of_date": row.as_of_date.isoformat(),
            "reason": row.reversal_reason,
            "reversed_by": row.reversed_by,
            "reversed_at": now.isoformat(),
            "reversal_batch_id": str(batch.id),
            "lineage_id": str(node.id),
            "rows_restored": restored,
            "rows_withdrawn": row.rows_withdrawn,
        },
    )
    db.flush()
    return row


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


def get_withdrawal(db: Session, organization_id: str, withdrawal_id: UUID) -> CanonicalWithdrawal:
    row = db.scalar(
        select(CanonicalWithdrawal).where(
            CanonicalWithdrawal.id == withdrawal_id,
            CanonicalWithdrawal.organization_id == organization_id,
        )
    )
    if row is None:
        raise WithdrawalError(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Withdrawal {withdrawal_id} not found.",
        )
    return row


def list_withdrawals(
    db: Session,
    organization_id: str,
    bank_id: str,
    *,
    as_of_date: date | None = None,
    withdrawal_status: str | None = None,
) -> list[CanonicalWithdrawal]:
    """Every withdrawal for the bank, newest first."""
    conditions = [
        CanonicalWithdrawal.organization_id == organization_id,
        CanonicalWithdrawal.bank_id == bank_id,
    ]
    if as_of_date is not None:
        conditions.append(CanonicalWithdrawal.as_of_date == as_of_date)
    if withdrawal_status is not None:
        conditions.append(CanonicalWithdrawal.status == withdrawal_status)
    return list(
        db.scalars(
            select(CanonicalWithdrawal)
            .where(*conditions)
            .order_by(CanonicalWithdrawal.requested_at.desc(), CanonicalWithdrawal.id.desc())
        )
    )
