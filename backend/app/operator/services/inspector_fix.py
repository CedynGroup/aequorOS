"""Session-gated deep tenant FIXES for the Tenant Inspector (the write side).

Where ``inspector_reads`` assembles diagnostic views, this module REMEDIATES:
it recomputes a bank's live metrics, mints an immutable official filing run,
re-processes an ingestion batch, or applies a scoped, reversible configuration
change. Every function runs on the operator's cross-tenant BYPASSRLS session and
is therefore explicitly ``organization_id``-scoped — the same cross-tenant read
discipline the reads follow. Nothing here mints a tenant token: writes stay OUT
of the tenant-auth path.

Functions MUTATE and flush but never COMMIT — the feature router
(``app.operator.features.inspector_fix``) owns the single commit so the action
and its ``operator_audit_log`` row land (or roll back) atomically, after the
session gate has admitted the caller.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.base import utc_now
from app.domain.ingestion.constants import STUCK_DEDUP_STATUSES
from app.models import (
    Bank,
    BankReportingPeriod,
    IngestionBatch,
    MappingConfigRecord,
    ParamCapitalThreshold,
    ParamLiquidityThreshold,
)
from app.schemas.operator import (
    FixConfigChangeRead,
    FixConfigRequest,
    FixJobEnqueuedRead,
    FixOfficialRunRequest,
    FixRecomputeRequest,
    FixRedriveDedupRead,
    FixRedriveDedupRequest,
    FixRerunIngestionRead,
    FixRerunIngestionRequest,
)
from app.services import job_queue
from app.services.public_ids import normalize_public_id

#: The out-of-band ML-ETL dedup job type, named literally for the same reason as
#: in ``operator_views``: importing ``app.services.etl_dedup_jobs`` would drag the
#: ETL and model-loading stack into the operator app. ``job_queue.enqueue``
#: validates it against the allow-list.
_ETL_DEDUP_JOB_TYPE = "etl_dedup"

#: Marks jobs enqueued from the operator inspector so the tenant's own audit
#: trail and the worker can distinguish a support-triggered run from a
#: bank-initiated one. Lives in the job payload, not the tenant audit spine.
_INITIATED_BY = "operator_inspector_fix"


@dataclass(frozen=True)
class JobFixResult:
    """A job-enqueuing fix plus the extra fields the router writes to the audit."""

    read: FixJobEnqueuedRead
    bank_id: str
    as_of_date: str


# -- bank / period resolution ----------------------------------------------------
def _resolve_bank(db: Session, organization_id: str, bank_id: str | None) -> Bank:
    """Resolve the bank a fix targets, always scoped to ``organization_id``.

    An explicit ``bank_id`` is honored (404 if it is not this org's). Otherwise
    the org's single bank is used; zero banks or an ambiguous multi-bank org
    without an explicit id is a 409 rather than a silent wrong-bank action.
    """
    if bank_id is not None:
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
    banks = list(
        db.scalars(
            select(Bank)
            .where(Bank.organization_id == organization_id)
            .order_by(Bank.created_at)
        )
    )
    if not banks:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This organization has no bank to act on.",
        )
    if len(banks) > 1:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This organization has multiple banks; specify bank_id.",
        )
    return banks[0]


def _latest_period(db: Session, organization_id: str, bank: Bank) -> BankReportingPeriod | None:
    return db.scalar(
        select(BankReportingPeriod)
        .where(
            BankReportingPeriod.organization_id == organization_id,
            BankReportingPeriod.bank_id == bank.id,
        )
        .order_by(BankReportingPeriod.period_end.desc())
        .limit(1)
    )


# -- recompute / official run ----------------------------------------------------
def recompute(
    db: Session, organization_id: str, payload: FixRecomputeRequest
) -> JobFixResult:
    """Enqueue an immediate live recompute (mirrors ``live_view.refresh_bank_data``)."""
    bank = _resolve_bank(db, organization_id, payload.bank_id)
    period = _latest_period(db, organization_id, bank)
    if period is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Bank has no reporting period to recompute; ingest data first.",
        )
    as_of = period.period_end.isoformat()
    job = job_queue.enqueue(
        db,
        organization_id,
        "pipeline_refresh",
        bank_id=bank.id,
        payload={"as_of_date": as_of, "reason": payload.note, "initiated_by": _INITIATED_BY},
        run_after=utc_now(),
        coalesce_key=f"refresh:{bank.id}:{as_of}",
    )
    return JobFixResult(
        read=FixJobEnqueuedRead(job_id=job.id, job_type=job.job_type, status=job.status),
        bank_id=bank.id,
        as_of_date=as_of,
    )


def official_run(
    db: Session, organization_id: str, payload: FixOfficialRunRequest
) -> JobFixResult:
    """Enqueue an immutable official filing run (mirrors ``live_view.mint_official_run``)."""
    bank = _resolve_bank(db, organization_id, payload.bank_id)
    if payload.as_of_date is not None:
        as_of_date = payload.as_of_date
    else:
        period = _latest_period(db, organization_id, bank)
        if period is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Bank has no reporting period; provide as_of_date or ingest data first."
                ),
            )
        as_of_date = period.period_end
    as_of = as_of_date.isoformat()
    job = job_queue.enqueue(
        db,
        organization_id,
        "official_run",
        bank_id=bank.id,
        payload={"as_of_date": as_of, "reason": payload.note, "initiated_by": _INITIATED_BY},
        run_after=utc_now(),
        coalesce_key=f"official:{bank.id}:{as_of}",
    )
    return JobFixResult(
        read=FixJobEnqueuedRead(job_id=job.id, job_type=job.job_type, status=job.status),
        bank_id=bank.id,
        as_of_date=as_of,
    )


# -- re-run ingestion ------------------------------------------------------------
def rerun_ingestion(
    db: Session, organization_id: str, payload: FixRerunIngestionRequest
) -> FixRerunIngestionRead:
    """Re-process one ingestion batch.

    There is NO in-place batch re-translation path in the service: an accepted
    batch is immutable history (idempotency + audit boundary), and translation
    runs off the raw source object at upload time. So the actionable remediation
    from here is a live recompute (``pipeline_refresh``) from currently-canonical
    data; ``detail`` tells the console that fixing a *source-parsing* problem
    still requires re-uploading the file through the Data Engine.
    """
    batch = db.scalar(
        select(IngestionBatch).where(
            IngestionBatch.id == payload.batch_id,
            IngestionBatch.organization_id == organization_id,
        )
    )
    if batch is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Ingestion batch not found for this organization.",
        )
    as_of = batch.as_of_date.isoformat()
    job = job_queue.enqueue(
        db,
        organization_id,
        "pipeline_refresh",
        bank_id=batch.bank_id,
        payload={
            "as_of_date": as_of,
            "reason": payload.note,
            "initiated_by": _INITIATED_BY,
            "rerun_of_batch_id": str(batch.id),
        },
        run_after=utc_now(),
        coalesce_key=f"refresh:{batch.bank_id}:{as_of}",
    )
    detail = (
        "Enqueued a live recompute (pipeline_refresh) from currently-canonical data. "
        "There is no in-place batch re-translation path: if the source file itself "
        "needs re-parsing, re-upload it through the Data Engine."
    )
    return FixRerunIngestionRead(
        job_id=job.id,
        job_type=job.job_type,
        status=job.status,
        batch_id=batch.id,
        detail=detail,
    )


def redrive_dedup(
    db: Session, organization_id: str, payload: FixRedriveDedupRequest
) -> FixRedriveDedupRead:
    """Re-enqueue the out-of-band ML-ETL dedup pass for one batch.

    The handler (``etl_dedup_jobs.run_etl_dedup``) is idempotent and re-runnable
    — a completed batch is a no-op, a failed one is retried — so the only thing
    missing was a way to ask for it again. The queue stops at ``max_attempts``,
    so a batch whose job exhausted its attempts is stranded for ever with no
    surface to recover it: four batches / 611,869 records sat that way on the
    primary for weeks.

    Deliberately NOT automatic. Those four failed for three unrelated reasons —
    a severed DB connection across the long non-SQL phase, a live job reclaimed
    at the 900s stale-job threshold while legitimately running for 2h02m, and a
    ``MappingConfig`` schema skew since healed. A retry timer would have hidden
    all three, and two of them needed a code change before any retry could
    succeed. An operator reads the recorded error, then asks.

    Refuses a batch whose pass already completed (409): re-running it would
    duplicate nothing (the handler short-circuits) but the request means the
    operator has misread the board, and saying so is better than enqueuing a
    no-op that reports success.
    """
    batch = db.scalar(
        select(IngestionBatch).where(
            IngestionBatch.id == payload.batch_id,
            IngestionBatch.organization_id == organization_id,
        )
    )
    if batch is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Ingestion batch not found for this organization.",
        )
    dedup_status = (batch.etl_report or {}).get("dedup_status")
    if dedup_status not in STUCK_DEDUP_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "This batch's deduplication pass is not awaiting a re-drive "
                f"(dedup_status={dedup_status!r}); only a deferred or failed pass "
                "can be re-driven."
            ),
        )
    previous = job_queue.latest_for_entity(
        db,
        organization_id=organization_id,
        job_type=_ETL_DEDUP_JOB_TYPE,
        entity_id=batch.id,
    )
    if previous is not None and previous.status in {"queued", "running"}:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "A deduplication job for this batch is already "
                f"{previous.status}; wait for it to finish before re-driving."
            ),
        )
    job = job_queue.enqueue(
        db,
        organization_id,
        _ETL_DEDUP_JOB_TYPE,
        bank_id=batch.bank_id,
        payload={
            "batch_id": str(batch.id),
            "reason": payload.note,
            "initiated_by": _INITIATED_BY,
            "redrive_of_job_id": str(previous.id) if previous is not None else None,
        },
        run_after=utc_now(),
        coalesce_key=f"etl_dedup:{batch.id}",
        entity_type="ingestion_batch",
        entity_id=batch.id,
    )
    return FixRedriveDedupRead(
        job_id=job.id,
        job_type=job.job_type,
        status=job.status,
        batch_id=batch.id,
        previous_dedup_status=str(dedup_status),
        previous_job_id=previous.id if previous is not None else None,
        previous_job_error=previous.error if previous is not None else None,
    )


# -- scoped config change --------------------------------------------------------
def apply_config(
    db: Session, organization_id: str, payload: FixConfigRequest, *, approved_by: str
) -> FixConfigChangeRead:
    """Apply one of the two supported scoped, reversible config changes."""
    if payload.kind == "mapping_active":
        return _toggle_mapping_active(db, organization_id, payload)
    return _supersede_threshold(db, organization_id, payload, approved_by=approved_by)


def _as_uuid(target_id: str) -> UUID:
    try:
        return UUID(str(target_id))
    except ValueError as exc:
        # A malformed id is indistinguishable from "no such row in this org".
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Target not found for this organization.",
        ) from exc


def _toggle_mapping_active(
    db: Session, organization_id: str, payload: FixConfigRequest
) -> FixConfigChangeRead:
    if not isinstance(payload.value, bool):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="mapping_active requires a boolean value.",
        )
    mapping = db.scalar(
        select(MappingConfigRecord).where(
            MappingConfigRecord.id == _as_uuid(payload.target_id),
            MappingConfigRecord.organization_id == organization_id,
        )
    )
    if mapping is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Mapping config not found for this organization.",
        )
    before_status = mapping.status
    new_status = "active" if payload.value else "retired"
    if new_status == "active" and before_status != "active":
        # Respect the single-active-per-scope partial unique index: a burst of
        # activations must not silently trip a DB IntegrityError (→ 500).
        conflict = db.scalar(
            select(MappingConfigRecord).where(
                MappingConfigRecord.organization_id == organization_id,
                MappingConfigRecord.bank_id == mapping.bank_id,
                MappingConfigRecord.source_system == mapping.source_system,
                MappingConfigRecord.source_ref == mapping.source_ref,
                MappingConfigRecord.status == "active",
                MappingConfigRecord.id != mapping.id,
            )
        )
        if conflict is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Another mapping is already active for this source; "
                    "deactivate it before activating this one."
                ),
            )
    mapping.status = new_status
    db.flush()
    return FixConfigChangeRead(
        kind="mapping_active",
        target_id=str(mapping.id),
        before={"status": before_status},
        after={
            "status": new_status,
            "bank_id": mapping.bank_id,
            "source_system": mapping.source_system,
            "source_ref": mapping.source_ref,
        },
    )


def _supersede_threshold(
    db: Session, organization_id: str, payload: FixConfigRequest, *, approved_by: str
) -> FixConfigChangeRead:
    # bool is a subclass of int — reject it explicitly so `true` is never read as 1.
    if isinstance(payload.value, bool) or not isinstance(payload.value, (int, float)):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="threshold_value requires a numeric value.",
        )
    try:
        new_value = Decimal(str(payload.value))
    except InvalidOperation as exc:  # pragma: no cover - guarded by the isinstance check
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="threshold_value requires a numeric value.",
        ) from exc
    target_uuid = _as_uuid(payload.target_id)
    now = utc_now()
    today = now.date()

    liquidity = db.scalar(
        select(ParamLiquidityThreshold).where(
            ParamLiquidityThreshold.id == target_uuid,
            ParamLiquidityThreshold.organization_id == organization_id,
            ParamLiquidityThreshold.effective_to.is_(None),
        )
    )
    if liquidity is not None:
        return _supersede_liquidity(db, liquidity, new_value, approved_by, now, today)

    capital = db.scalar(
        select(ParamCapitalThreshold).where(
            ParamCapitalThreshold.id == target_uuid,
            ParamCapitalThreshold.organization_id == organization_id,
            ParamCapitalThreshold.effective_to.is_(None),
        )
    )
    if capital is not None:
        return _supersede_capital(db, capital, new_value, approved_by, now, today)

    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="Current Board threshold not found for this organization.",
    )


def _guard_same_day(effective_from: date) -> None:
    """A new generation dated today collides with a current row also dated today.

    The register is append-only and its scope uniqueness includes
    ``effective_from``; closing the current row and inserting a new one both
    dated today would trip the constraint. Refuse clearly rather than 500.
    """
    if effective_from >= utc_now().date():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "This threshold's current generation is already dated today; "
                "supersede it tomorrow or via the desk register."
            ),
        )


def _supersede_liquidity(  # noqa: PLR0913 - the full supersession record, one arg each
    db: Session,
    current: ParamLiquidityThreshold,
    new_value: Decimal,
    approved_by: str,
    now: datetime,
    today: date,
) -> FixConfigChangeRead:
    _guard_same_day(current.effective_from)
    before_value = float(current.threshold_pct)
    current.effective_to = today
    new_row = ParamLiquidityThreshold(
        organization_id=current.organization_id,
        jurisdiction_code=current.jurisdiction_code,
        institution_class=current.institution_class,
        threshold_code=current.threshold_code,
        threshold_pct=new_value,
        notes=current.notes,
        effective_from=today,
        effective_to=None,
        approved_by=approved_by,
        approval_timestamp=now,
    )
    db.add(new_row)
    db.flush()
    return FixConfigChangeRead(
        kind="threshold_value",
        target_id=str(new_row.id),
        before={
            "register": "liquidity",
            "threshold_code": current.threshold_code,
            "institution_class": current.institution_class,
            "value_pct": before_value,
            "prior_row_id": str(current.id),
        },
        after={
            "register": "liquidity",
            "threshold_code": new_row.threshold_code,
            "institution_class": new_row.institution_class,
            "value_pct": float(new_row.threshold_pct),
            "effective_from": new_row.effective_from.isoformat(),
        },
    )


def _supersede_capital(  # noqa: PLR0913 - the full supersession record, one arg each
    db: Session,
    current: ParamCapitalThreshold,
    new_value: Decimal,
    approved_by: str,
    now: datetime,
    today: date,
) -> FixConfigChangeRead:
    _guard_same_day(current.effective_from)
    before_value = float(current.value_pct)
    current.effective_to = today
    new_row = ParamCapitalThreshold(
        organization_id=current.organization_id,
        jurisdiction_code=current.jurisdiction_code,
        threshold_code=current.threshold_code,
        value_pct=new_value,
        effective_from=today,
        effective_to=None,
        approved_by=approved_by,
        approval_timestamp=now,
    )
    db.add(new_row)
    db.flush()
    return FixConfigChangeRead(
        kind="threshold_value",
        target_id=str(new_row.id),
        before={
            "register": "capital",
            "threshold_code": current.threshold_code,
            "value_pct": before_value,
            "prior_row_id": str(current.id),
        },
        after={
            "register": "capital",
            "threshold_code": new_row.threshold_code,
            "value_pct": float(new_row.value_pct),
            "effective_from": new_row.effective_from.isoformat(),
        },
    )
