"""Operator-triggered BI mart backfill (the write side of BI on the staff plane).

A backfill is the only way a bank's mart HISTORY gets built: the product hooks
enqueue one ``bi_mart_refresh`` per changed ``(bank, as_of)`` going forward,
and nothing walks backwards on its own. It is operator-triggered, never
automatic, because it is the one BI job whose cost is set by the size of a
tenant's book times the depth requested, and that is a decision for a person
looking at the ``bi`` worker's load — the same reason ``redrive_dedup`` is
manual.

Runs on the operator's cross-tenant BYPASSRLS session and is therefore
explicitly ``organization_id``-scoped, exactly as ``inspector_fix`` is.
Mutates and flushes but never commits: the feature router owns the single
commit so the enqueue and its ``operator_audit_log`` row land (or roll back)
atomically, after the session gate has admitted the caller.
"""

from __future__ import annotations

from datetime import date

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.base import utc_now
from app.models import Bank, CanonicalPositionSnapshot
from app.models.canonical import is_current_generation
from app.schemas.operator import BiBackfillRead, BiBackfillRequest
from app.services import job_queue
from app.services.bi.versions import BUILDER_VERSION
from app.services.public_ids import normalize_public_id

#: Named literally, as ``inspector_fix`` names ``etl_dedup``: importing
#: ``app.jobs.bi_mart_backfill`` would bind the operator app to the worker's
#: handler tree. ``job_queue.enqueue`` validates it against the allow-list, and
#: the key below is the handler's own (``bi_mart_backfill.coalesce_key_for``)
#: so an operator-started chain and its self-enqueued hops share one key.
JOB_TYPE = "bi_mart_backfill"
ENTITY_TYPE = "bank"

#: Marks jobs enqueued from the operator console so the worker's queue history
#: distinguishes a staff-triggered walk from a hook-triggered refresh.
_INITIATED_BY = "operator_bi_backfill"


def coalesce_key_for(bank_id: str) -> str:
    return f"bi-backfill:{bank_id}"


def _resolve_bank(db: Session, organization_id: str, bank_id: str) -> Bank:
    bank = db.scalar(
        select(Bank).where(
            Bank.id == normalize_public_id(bank_id),
            Bank.organization_id == organization_id,
        )
    )
    if bank is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Bank not found for this organization.",
        )
    return bank


def _latest_snapshot_date(db: Session, organization_id: str, bank: Bank) -> date | None:
    """The newest current-generation position snapshot date — the natural top
    of a backfill, because every earlier date is history and every later one
    has no canonical book to build from."""
    return db.scalar(
        select(func.max(CanonicalPositionSnapshot.as_of_date)).where(
            CanonicalPositionSnapshot.organization_id == organization_id,
            CanonicalPositionSnapshot.bank_id == bank.id,
            *is_current_generation(CanonicalPositionSnapshot),
        )
    )


def start_backfill(db: Session, organization_id: str, payload: BiBackfillRequest) -> BiBackfillRead:
    """Enqueue ONE ``bi_mart_backfill`` chain for a bank of ``organization_id``.

    Refuses (409) when mart builds are switched off — the handler would only
    mark every hop ``skipped``, and an operator who asked for a walk should be
    told it cannot run rather than handed a job that reports success and
    builds nothing; when the bank has no canonical snapshot to start from and
    no ``from_date`` was given; when the requested window runs the wrong way
    (a backfill walks newest-first, ``until_date`` ≤ ``from_date``); and when
    a chain for this bank is already queued or running, because a second one
    would merge into the queued hop's payload and silently rewrite its cursor.
    """
    if not get_settings().bi.mart_enqueue_enabled:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "BI mart builds are switched off on this deployment "
                "(BI_MART_ENQUEUE_ENABLED); a backfill would be skipped by the worker."
            ),
        )
    bank = _resolve_bank(db, organization_id, payload.bank_id)
    cursor = payload.from_date
    if cursor is None:
        cursor = _latest_snapshot_date(db, organization_id, bank)
        if cursor is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "This bank has no canonical position snapshot to start from; "
                    "give from_date explicitly or ingest a book first."
                ),
            )
    if payload.until_date > cursor:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"until_date {payload.until_date.isoformat()} is after the starting date "
                f"{cursor.isoformat()}; a backfill walks newest-first, so until_date "
                "must be on or before from_date."
            ),
        )
    previous = job_queue.latest_for_entity(
        db,
        organization_id=organization_id,
        job_type=JOB_TYPE,
        entity_id=bank.id,
    )
    if previous is not None and previous.status in {"queued", "running"}:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"A mart backfill for this bank is already {previous.status} "
                f"(cursor {previous.payload.get('cursor_date')}); wait for the chain to "
                "finish before starting another."
            ),
        )
    job = job_queue.enqueue(
        db,
        organization_id,
        JOB_TYPE,
        bank_id=bank.id,
        payload={
            "organization_id": organization_id,
            "bank_id": bank.id,
            "cursor_date": cursor.isoformat(),
            "until_date": payload.until_date.isoformat(),
            "builder_version": BUILDER_VERSION,
            "reason": payload.note,
            "initiated_by": _INITIATED_BY,
        },
        run_after=utc_now(),
        coalesce_key=coalesce_key_for(bank.id),
        entity_type=ENTITY_TYPE,
        entity_id=bank.id,
    )
    return BiBackfillRead(
        job_id=job.id,
        job_type=job.job_type,
        status=job.status,
        bank_id=bank.id,
        cursor_date=cursor,
        until_date=payload.until_date,
        builder_version=BUILDER_VERSION,
    )


__all__ = ["ENTITY_TYPE", "JOB_TYPE", "coalesce_key_for", "start_backfill"]
