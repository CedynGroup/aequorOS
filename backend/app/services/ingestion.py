"""Ingestion orchestration: adapter → translate → validate → persist.

The orchestrator is the only writer of canonical state. It owns batch
lifecycle, idempotency, lineage recording, gating on validation outcome, and
supersession of prior generations. Adapters stay ignorant of the database;
validation stays ignorant of persistence.

The MVP engine executes synchronously within the request, mirroring the
first calculation engine; lifecycle states are still recorded so history
reads identically once execution moves to a worker.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import tempfile
from collections.abc import Callable
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

import app.adapters  # noqa: F401 - importing registers every shipped source adapter
from app.api.deps import TenantContext
from app.core.config import get_settings
from app.core.ids import new_uuid7
from app.db.base import utc_now
from app.domain.ingestion.adapter import SourceAdapter, get_adapter_class
from app.domain.ingestion.constants import BATCH_ACCEPTED_STATUSES, SourceSystem
from app.domain.ingestion.contracts import (
    ENTITY_TYPES,
    AdapterConfig,
    CanonicalRecords,
    ExtractionResult,
    MappingConfig,
)
from app.domain.ingestion.enrichment import apply_manual_override
from app.domain.ingestion.validation import (
    Finding,
    ValidationContext,
    build_validation_report,
    default_validation_config,
    run_validation,
)
from app.etl import EtlConfig, etl_summary, run_etl
from app.models import (
    AuditEvent,
    Bank,
    CanonicalCounterparty,
    CanonicalGlAccount,
    CanonicalPosition,
    CanonicalPositionSnapshot,
    CanonicalProduct,
    CanonicalReferenceRow,
    IngestionBatch,
    LineageRecord,
    MappingConfigRecord,
    TranslationFailure,
)
from app.models.canonical import CanonicalMetadataMixin
from app.schemas.ingestion import (
    CanonicalCountsRead,
    CanonicalPositionFacetsRead,
    CanonicalPositionListRead,
    CanonicalPositionRead,
    IngestionBatchCreate,
    IngestionBatchListRead,
    IngestionBatchRead,
    IngestionBatchStartRead,
    IngestionSourceSummaryRead,
    IngestionSummaryRead,
    IngestionUploadRead,
    LineageNodeRead,
    LineageWalkRead,
    MappingConfigCreate,
    MappingConfigListRead,
    MappingConfigRead,
    PositionFacetValueRead,
    PositionSnapshotOverrideCreate,
    PositionSnapshotRead,
    TranslationFailureListRead,
    TranslationFailureRead,
)
from app.services import job_queue
from app.services.audit import record_event
from app.services.data_activation import ACTIVATION_EVENT
from app.storage.client import (
    ObjectMetadata,
    StorageClient,
    StorageError,
    StorageLocation,
)

_MAX_LINEAGE_DEPTH = 50
# Above this extraction size the inline ML-ETL dedup + anomaly passes (pairwise /
# model-scored) are skipped so a core-banking-scale sync completes; deterministic
# per-record preprocessing still runs and the full cleaned canonical data persists.
# Entity-resolution at scale belongs in an out-of-band pass (follow-up).
_ETL_INLINE_DEDUP_MAX_RECORDS = 5000


def create_mapping_config(
    db: Session, ctx: TenantContext, bank_id: UUID, payload: MappingConfigCreate
) -> MappingConfigRead:
    bank = _get_bank_or_404(db, ctx, bank_id)
    next_version = (
        db.scalar(
            select(func.coalesce(func.max(MappingConfigRecord.version), 0)).where(
                MappingConfigRecord.organization_id == ctx.organization_id,
                MappingConfigRecord.bank_id == bank.id,
                MappingConfigRecord.source_system == payload.source_system,
            )
        )
        or 0
    ) + 1

    if payload.activate:
        current_active = db.scalar(
            select(MappingConfigRecord).where(
                MappingConfigRecord.organization_id == ctx.organization_id,
                MappingConfigRecord.bank_id == bank.id,
                MappingConfigRecord.source_system == payload.source_system,
                MappingConfigRecord.status == "active",
            )
        )
        if current_active is not None:
            current_active.status = "retired"
            db.flush()

    record = MappingConfigRecord(
        organization_id=ctx.organization_id,
        bank_id=bank.id,
        source_system=payload.source_system,
        version=next_version,
        status="active" if payload.activate else "draft",
        name=payload.name,
        config=payload.config.model_dump(mode="json"),
        created_by=ctx.actor_user_id,
    )
    db.add(record)
    db.flush()
    record_event(
        db,
        ctx,
        event_type="mapping_config.created",
        entity_type="mapping_config",
        entity_id=record.id,
        details={
            "source_system": payload.source_system,
            "version": next_version,
            "activated": payload.activate,
            "reason": payload.reason,
        },
    )
    db.commit()
    return MappingConfigRead.model_validate(record, from_attributes=True)


def list_mapping_configs(db: Session, ctx: TenantContext, bank_id: UUID) -> MappingConfigListRead:
    bank = _get_bank_or_404(db, ctx, bank_id)
    records = db.scalars(
        select(MappingConfigRecord)
        .where(
            MappingConfigRecord.organization_id == ctx.organization_id,
            MappingConfigRecord.bank_id == bank.id,
        )
        .order_by(MappingConfigRecord.source_system, MappingConfigRecord.version.desc())
    )
    return MappingConfigListRead(
        bank_id=bank.id,
        configs=[
            MappingConfigRead.model_validate(record, from_attributes=True) for record in records
        ],
    )


class _BatchFailure(Exception):
    """Carries the persisted failed-batch response out of the prepare phase."""

    def __init__(self, response: IngestionBatchStartRead) -> None:
        self.response = response


def build_adapter_config(
    location: str, mapping: MappingConfig, *, extra_options: dict[str, Any] | None = None
) -> AdapterConfig:
    """Adapter config for one extraction: the source location plus the table
    resolution options derived from the mapping (entity + reference tables).

    The out-of-band ETL dedup job rebuilds the identical config from a batch's
    stored mapping so its re-extraction reproduces the original ingestion pass.
    """
    entity_tables = {
        entity_type: [entity_mapping.source_table, *entity_mapping.source_table_aliases]
        for entity_type, entity_mapping in mapping.field_mappings.items()
    }
    reference_tables = {
        name: {
            "tables": [reference.source_table, *reference.source_table_aliases],
            "dataset_kind": reference.dataset_kind,
        }
        for name, reference in mapping.reference_mappings.items()
    }
    return AdapterConfig(
        location=location,
        options={
            "entity_tables": entity_tables,
            "reference_tables": reference_tables,
            **(extra_options or {}),
        },
    )


def _prepare_extraction(
    db: Session,
    ctx: TenantContext,
    bank: Bank,
    payload: IngestionBatchCreate,
    storage: StorageClient,
):
    """Resolve mapping, materialize the source, and extract raw records.

    Any expected failure (unreachable source, staged object missing, broken
    file) is persisted as a failed batch and raised as :class:`_BatchFailure`
    so the orchestrator has a single success path.
    """
    mapping_record = _resolve_mapping_config(db, ctx, bank, payload)
    mapping = MappingConfig.model_validate(mapping_record.config)
    adapter = _resolve_adapter(payload.source_system)

    def fail(code: str, message: str) -> _BatchFailure:
        batch = _new_batch(ctx, bank, payload, adapter, mapping_record)
        return _BatchFailure(_fail_batch(db, ctx, batch, payload.reason, code, message))

    try:
        source_path = _materialize_source(db, bank, payload, storage)
    except StorageError as exc:
        raise fail("storage_error", str(exc)) from exc

    adapter_config = build_adapter_config(
        source_path, mapping, extra_options=payload.adapter_options
    )

    connection = adapter.validate_connection(adapter_config)
    if not connection.ok:
        raise fail("connection_failed", connection.detail)

    try:
        extraction = adapter.extract(adapter_config, payload.as_of_date, list(ENTITY_TYPES))
    except Exception as exc:  # noqa: BLE001 - a broken source fails the batch, not the API
        raise fail("extraction_failed", str(exc)) from exc

    return adapter, mapping_record, mapping, source_path, extraction


def start_ingestion(  # noqa: PLR0915 - the batch lifecycle is one linear orchestration
    db: Session,
    ctx: TenantContext,
    bank_id: UUID,
    payload: IngestionBatchCreate,
    storage: StorageClient,
) -> IngestionBatchStartRead:
    bank = _get_bank_or_404(db, ctx, bank_id)
    try:
        adapter, mapping_record, mapping, source_path, extraction = _prepare_extraction(
            db, ctx, bank, payload, storage
        )
    except _BatchFailure as failure:
        return failure.response

    existing = _find_accepted_batch(
        db, ctx, bank, payload, extraction.content_hash, mapping_record.id
    )
    if existing is not None:
        return IngestionBatchStartRead(
            batch=IngestionBatchRead.model_validate(existing, from_attributes=True), reused=True
        )

    batch = _new_batch(ctx, bank, payload, adapter, mapping_record)
    batch.status = "extracting"
    batch.started_at = utc_now()
    batch.content_hash = extraction.content_hash
    batch.records_extracted = len(extraction.records)
    db.add(batch)
    db.flush()

    extract_node = _lineage(
        db,
        ctx,
        batch,
        operation_type="ADAPTER_EXTRACT",
        operation_ref=f"{adapter.identify().name}_v{adapter.identify().version}/{payload.location}",
        inputs=(),
        details={"records": len(extraction.records), "warnings": extraction.warnings},
    )

    storage_failure = _artifact_step(
        db,
        ctx,
        (bank, payload, adapter, mapping_record),
        lambda: _persist_raw_artifact(
            ctx, bank_slug(db, bank), batch, extract_node, source_path, storage
        ),
    )
    if storage_failure is not None:
        return storage_failure

    # -- ML-ETL (Data Engine layer between adapters and the canonical model): resolve
    # source fields to canonical concepts, apply audit-sanctioned preprocessing (which
    # FLAGS, never silently modifies, regulatory-critical values) and deduplicate
    # entities across sources/time. run_etl is a pure pass; this layer owns the ETL's
    # lineage + audit persistence and feeds the cleaned extraction to translation.
    #
    # Preprocessing is O(n) per record and always runs inline. Dedup + anomaly are
    # pairwise / model passes that would block a core-banking-scale sync (100k+ rows);
    # since they emit linkage/flag METADATA (never canonical values), above a bound we
    # skip them inline and defer entity-resolution to an out-of-band pass. The cleaned,
    # preprocessed canonical data still persists in full either way.
    etl_inline_dedup = len(extraction.records) <= _ETL_INLINE_DEDUP_MAX_RECORDS
    etl_result = run_etl(
        extraction,
        mapping,
        config=EtlConfig(deduplicate=etl_inline_dedup, detect_anomalies=etl_inline_dedup),
    )
    extraction = etl_result.cleaned
    # dedup_status lets every reader tell an out-of-band-pending report ("deferred")
    # from a complete one ("completed") without inferring it from zeroed linkage
    # counts; the etl_dedup job flips a deferred report to "completed".
    batch.etl_report = {
        **etl_summary(etl_result),
        "dedup_status": "completed" if etl_inline_dedup else "deferred",
    }
    preprocess_node = _lineage(
        db,
        ctx,
        batch,
        operation_type="ML_ETL_PREPROCESS",
        operation_ref="ml_etl/preprocess",
        inputs=(extract_node.id,),
        details={
            "operations": len(etl_result.operations),
            "flags": len(etl_result.flags),
            "sanctioned": etl_result.sanctioned_count,
        },
    )
    dedup_node = _lineage(
        db,
        ctx,
        batch,
        operation_type="ML_ETL_DEDUP",
        operation_ref="ml_etl/dedup",
        inputs=(preprocess_node.id,),
        # When deferred, no linkage/anomaly pass ran inline; the count is a
        # placeholder, not a genuine "found zero". The out-of-band etl_dedup job
        # appends its own ML_ETL_DEDUP node with the real counts.
        details={"linkages": len(etl_result.linkages), "deferred": not etl_inline_dedup},
    )
    record_event(
        db,
        ctx,
        event_type="ml_etl.completed",
        entity_type="ingestion_batch",
        entity_id=batch.id,
        details=batch.etl_report,
    )

    batch.status = "translating"
    records = adapter.translate(extraction, mapping)
    batch.records_translated = records.record_count
    translate_node = _lineage(
        db,
        ctx,
        batch,
        operation_type="ADAPTER_TRANSLATE",
        operation_ref=f"mapping_config/v{mapping_record.version}",
        inputs=(dedup_node.id,),
        details={"translated": records.record_count, "failures": len(records.failures)},
    )
    for failure in records.failures:
        db.add(
            TranslationFailure(
                organization_id=ctx.organization_id,
                bank_id=bank.id,
                ingestion_batch_id=batch.id,
                entity_type=failure.entity_type,
                source_locator=failure.source_locator,
                raw_record=failure.raw_record,
                error_code=failure.error_code,
                error_message=failure.error_message,
            )
        )

    batch.status = "validating"
    known_counterparties, known_products, known_gl_accounts = _known_references(db, ctx, bank)
    context = ValidationContext(
        as_of_date=payload.as_of_date,
        prior_balances=_prior_balances(db, ctx, bank, payload.as_of_date),
        known_counterparties=known_counterparties,
        known_products=known_products,
        known_gl_accounts=known_gl_accounts,
    )
    outcome = run_validation(
        records,
        default_validation_config(),
        context,
        extra_findings=_table_resolution_findings(extraction, mapping),
    )
    validate_node = _lineage(
        db,
        ctx,
        batch,
        operation_type="VALIDATION",
        operation_ref="validation_config/default",
        inputs=(translate_node.id,),
        details={"overall_status": outcome.overall_status, "findings": len(outcome.findings)},
    )

    statuses = list(outcome.record_statuses.values())
    batch.records_accepted = statuses.count("accepted")
    batch.records_warning = statuses.count("warning")
    batch.records_error = statuses.count("error")
    batch.records_blocked = statuses.count("blocked")
    report = build_validation_report(
        outcome,
        records_extracted=len(extraction.records),
        records_translated=records.record_count,
        reference_rows=records.reference_row_counts,
        tables=_tables_breakdown(extraction, records, outcome.record_statuses),
    )
    batch.validation_report = report
    batch.status = outcome.overall_status
    batch.completed_at = utc_now()

    if outcome.overall_status != "rejected":
        _persist_canonical(db, ctx, bank, batch, validate_node, records, outcome.record_statuses)

    storage_failure = _artifact_step(
        db,
        ctx,
        (bank, payload, adapter, mapping_record),
        lambda: _persist_report_artifact(ctx, bank_slug(db, bank), batch, validate_node, storage),
    )
    if storage_failure is not None:
        return storage_failure

    storage.flush_access_log()
    _record_batch_event(db, ctx, batch, payload.reason)
    _enqueue_live_refresh(db, ctx, bank, payload, batch)
    if not etl_inline_dedup:
        _enqueue_etl_dedup(db, ctx, bank, batch)
    db.commit()
    return IngestionBatchStartRead(
        batch=IngestionBatchRead.model_validate(batch, from_attributes=True), reused=False
    )


def _enqueue_live_refresh(
    db: Session,
    ctx: TenantContext,
    bank: Bank,
    payload: IngestionBatchCreate,
    batch: IngestionBatch,
) -> None:
    """Trigger the automatic live pipeline for accepted batches.

    Debounced + coalesced so a multi-file upload burst for one (bank, as-of)
    settles into a single refresh. Rejected batches carry no canonical data, so
    they never enqueue. Push ingestion routes through here too (it calls
    ``start_ingestion``), so there is one trigger point for every source.
    """
    if batch.status not in BATCH_ACCEPTED_STATUSES:
        return
    job_payload: dict[str, str] = {"as_of_date": payload.as_of_date.isoformat()}
    if ctx.actor_user_id is not None:
        job_payload["actor_user_id"] = str(ctx.actor_user_id)
    debounce = get_settings().worker.pipeline_debounce_seconds
    job_queue.enqueue(
        db,
        ctx.organization_id,
        "pipeline_refresh",
        bank_id=bank.id,
        payload=job_payload,
        run_after=utc_now() + timedelta(seconds=debounce),
        coalesce_key=f"refresh:{bank.id}:{payload.as_of_date.isoformat()}",
    )


def _enqueue_etl_dedup(
    db: Session,
    ctx: TenantContext,
    bank: Bank,
    batch: IngestionBatch,
) -> None:
    """Enqueue the out-of-band ML-ETL dedup pass for a large accepted batch.

    Called only when inline dedup was skipped for size. The pass re-derives the
    entity-resolution linkage + anomaly metadata (never canonical values) that
    would have gone in ``etl_report`` inline, so the sync request is not blocked
    by the pairwise/model passes. Gated on an accepted status like
    :func:`_enqueue_live_refresh`: a rejected batch persists no canonical data,
    so there is nothing to link. Coalesced per batch so a retry of the same
    ingestion does not stack duplicate jobs.
    """
    if batch.status not in BATCH_ACCEPTED_STATUSES:
        return
    job_queue.enqueue(
        db,
        ctx.organization_id,
        "etl_dedup",
        bank_id=bank.id,
        payload={"batch_id": str(batch.id)},
        coalesce_key=f"etl_dedup:{batch.id}",
        entity_type="ingestion_batch",
        entity_id=batch.id,
    )


def list_batches(
    db: Session,
    ctx: TenantContext,
    bank_id: UUID,
    source_system: SourceSystem | None = None,
) -> IngestionBatchListRead:
    bank = _get_bank_or_404(db, ctx, bank_id)
    query = select(IngestionBatch).where(
        IngestionBatch.organization_id == ctx.organization_id,
        IngestionBatch.bank_id == bank.id,
    )
    if source_system is not None:
        query = query.where(IngestionBatch.source_system == source_system)
    batches = db.scalars(query.order_by(IngestionBatch.id.desc()))
    return IngestionBatchListRead(
        bank_id=bank.id,
        batches=[
            IngestionBatchRead.model_validate(batch, from_attributes=True) for batch in batches
        ],
    )


def get_ingestion_summary(db: Session, ctx: TenantContext, bank_id: UUID) -> IngestionSummaryRead:
    """Per-source ingestion rollup plus current canonical model counts.

    Cheap aggregates only: batch counts/record totals grouped by source
    system, the latest batch per source (batch ids are UUIDv7, so ``max(id)``
    is the most recent), current-generation canonical counts, and the
    activation history the data-activation listing reads.
    """
    bank = _get_bank_or_404(db, ctx, bank_id)
    org_id = ctx.organization_id

    rollups = db.execute(
        select(
            IngestionBatch.source_system,
            func.count(IngestionBatch.id),
            func.coalesce(func.sum(IngestionBatch.records_accepted), 0),
            func.coalesce(func.sum(IngestionBatch.records_warning), 0),
        )
        .where(IngestionBatch.organization_id == org_id, IngestionBatch.bank_id == bank.id)
        .group_by(IngestionBatch.source_system)
    ).all()
    # Latest batch per source. Portable top-1 query per source system (at
    # most 9, all hitting the org/bank index) — Postgres has no max(uuid)
    # aggregate, so a grouped max(id) subquery is not an option.
    latest_by_source = {}
    for source_system, _count, _accepted, _warning in rollups:
        latest_by_source[source_system] = db.scalar(
            select(IngestionBatch)
            .where(
                IngestionBatch.organization_id == org_id,
                IngestionBatch.bank_id == bank.id,
                IngestionBatch.source_system == source_system,
            )
            .order_by(IngestionBatch.id.desc())
            .limit(1)
        )
    sources = [
        IngestionSourceSummaryRead(
            source_system=source_system,  # type: ignore[arg-type]
            batches=batch_count,
            last_batch_at=(
                (latest.started_at or latest.created_at)
                if (latest := latest_by_source.get(source_system)) is not None
                else None
            ),
            last_status=latest.status if latest is not None else None,  # type: ignore[arg-type]
            records_accepted_total=int(accepted_total),
            records_warning_total=int(warning_total),
        )
        for source_system, batch_count, accepted_total, warning_total in rollups
    ]
    sources.sort(key=lambda source: source.source_system)

    def _current_count(model: type[CanonicalMetadataMixin]) -> int:
        return (
            db.scalar(
                select(func.count())
                .select_from(model)
                .where(
                    model.organization_id == org_id,
                    model.bank_id == bank.id,
                    model.superseded_by.is_(None),
                )
            )
            or 0
        )

    # Reference rows the modules actually consume: the latest batch per
    # dataset kind (mirrors fact derivation's read pattern).
    reference_groups = db.execute(
        select(
            CanonicalReferenceRow.dataset_kind,
            CanonicalReferenceRow.ingestion_batch_id,
            func.count(),
        )
        .where(
            CanonicalReferenceRow.organization_id == org_id,
            CanonicalReferenceRow.bank_id == bank.id,
        )
        .group_by(CanonicalReferenceRow.dataset_kind, CanonicalReferenceRow.ingestion_batch_id)
    ).all()
    latest_reference: dict[str, tuple[UUID, int]] = {}
    for dataset_kind, batch_id, row_count in reference_groups:
        current = latest_reference.get(dataset_kind)
        if current is None or str(batch_id) > str(current[0]):
            latest_reference[dataset_kind] = (batch_id, row_count)
    reference_rows = sum(row_count for _, row_count in latest_reference.values())

    activation_events = select(AuditEvent).where(
        AuditEvent.organization_id == org_id,
        AuditEvent.event_type == ACTIVATION_EVENT,
        AuditEvent.entity_type == "bank",
        AuditEvent.entity_id == bank.id,
    )
    activations_count = (
        db.scalar(select(func.count()).select_from(activation_events.subquery())) or 0
    )
    last_activation_at = db.scalar(
        select(func.max(AuditEvent.created_at)).where(
            AuditEvent.organization_id == org_id,
            AuditEvent.event_type == ACTIVATION_EVENT,
            AuditEvent.entity_type == "bank",
            AuditEvent.entity_id == bank.id,
        )
    )

    return IngestionSummaryRead(
        bank_id=bank.id,
        sources=sources,
        canonical_counts=CanonicalCountsRead(
            positions=_current_count(CanonicalPosition),
            position_snapshots=_current_count(CanonicalPositionSnapshot),
            counterparties=_current_count(CanonicalCounterparty),
            gl_accounts=_current_count(CanonicalGlAccount),
            products=_current_count(CanonicalProduct),
            reference_rows=reference_rows,
        ),
        activations_count=int(activations_count),
        last_activation_at=last_activation_at,
    )


def get_batch(db: Session, ctx: TenantContext, bank_id: UUID, batch_id: UUID) -> IngestionBatchRead:
    batch = _get_batch_or_404(db, ctx, bank_id, batch_id)
    return IngestionBatchRead.model_validate(batch, from_attributes=True)


def list_translation_failures(
    db: Session, ctx: TenantContext, bank_id: UUID, batch_id: UUID
) -> TranslationFailureListRead:
    batch = _get_batch_or_404(db, ctx, bank_id, batch_id)
    failures = db.scalars(
        select(TranslationFailure)
        .where(
            TranslationFailure.organization_id == ctx.organization_id,
            TranslationFailure.ingestion_batch_id == batch.id,
        )
        .order_by(TranslationFailure.id)
    )
    return TranslationFailureListRead(
        batch_id=batch.id,
        failures=[
            TranslationFailureRead.model_validate(failure, from_attributes=True)
            for failure in failures
        ],
    )


def _escape_like(value: str) -> str:
    """Escape LIKE metacharacters so ``value`` matches literally."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def list_positions(  # noqa: PLR0913 - one keyword-only filter per blotter control
    db: Session,
    ctx: TenantContext,
    bank_id: UUID,
    as_of_date: date | None,
    *,
    limit: int = 100,
    offset: int = 0,
    position_type: str | None = None,
    currency: str | None = None,
    q: str | None = None,
) -> CanonicalPositionListRead:
    """One page of the current-generation position book, with the filtered total.

    Filters compose over current-generation identities: ``position_type`` and
    ``currency`` match exactly (currency is uppercase-normalized), ``q`` is a
    case-insensitive substring match on ``source_reference``, and
    ``as_of_date`` keeps the historical semantics — only positions whose
    current snapshot carries that business date. Ordering is deterministic
    (``source_reference``, then id) so pages are stable and disjoint.
    """
    bank = _get_bank_or_404(db, ctx, bank_id)
    filters = [
        CanonicalPosition.organization_id == ctx.organization_id,
        CanonicalPosition.bank_id == bank.id,
        CanonicalPosition.superseded_by.is_(None),
    ]
    if position_type is not None:
        filters.append(CanonicalPosition.position_type == position_type)
    if currency is not None:
        filters.append(CanonicalPosition.currency == currency.upper())
    if q:
        filters.append(
            CanonicalPosition.source_reference.ilike(f"%{_escape_like(q)}%", escape="\\")
        )
    if as_of_date is not None:
        filters.append(
            select(CanonicalPositionSnapshot.id)
            .where(
                CanonicalPositionSnapshot.position_id == CanonicalPosition.id,
                CanonicalPositionSnapshot.organization_id == ctx.organization_id,
                CanonicalPositionSnapshot.superseded_by.is_(None),
                CanonicalPositionSnapshot.as_of_date == as_of_date,
            )
            .exists()
        )

    total = int(
        db.scalar(select(func.count()).select_from(CanonicalPosition).where(*filters)) or 0
    )
    # Two-phase page fetch: resolve the page's ids first so the ordered,
    # filtered walk stays an index-only scan (type/currency/reference live in
    # the blotter index), then fetch just those ~100 full rows by id. A
    # single full-row query would heap-fetch every filtered-out candidate.
    page_ids = list(
        db.scalars(
            select(CanonicalPosition.id)
            .where(*filters)
            .order_by(CanonicalPosition.source_reference, CanonicalPosition.id)
            .limit(limit)
            .offset(offset)
        )
    )
    positions = (
        list(
            db.scalars(
                select(CanonicalPosition)
                .where(
                    CanonicalPosition.organization_id == ctx.organization_id,
                    CanonicalPosition.id.in_(page_ids),
                )
                .order_by(CanonicalPosition.source_reference, CanonicalPosition.id)
            )
        )
        if page_ids
        else []
    )

    snapshots: dict[UUID, CanonicalPositionSnapshot] = {}
    if positions:
        # A position carries one current snapshot per business date (long-lived
        # accounts hold a hundred-plus), and the blotter shows the latest.
        # Resolve (position, max as-of) first — an index-only walk of the
        # current-snapshot unique index — then fetch just those ~page-size
        # rows, instead of hydrating every date's snapshot for the page.
        latest_filters = [
            CanonicalPositionSnapshot.organization_id == ctx.organization_id,
            CanonicalPositionSnapshot.superseded_by.is_(None),
            CanonicalPositionSnapshot.position_id.in_([position.id for position in positions]),
        ]
        if as_of_date is not None:
            latest_filters.append(CanonicalPositionSnapshot.as_of_date == as_of_date)
        latest = (
            select(
                CanonicalPositionSnapshot.position_id.label("position_id"),
                func.max(CanonicalPositionSnapshot.as_of_date).label("as_of"),
            )
            .where(*latest_filters)
            .group_by(CanonicalPositionSnapshot.position_id)
            .subquery()
        )
        snapshot_query = (
            select(CanonicalPositionSnapshot)
            .join(
                latest,
                (CanonicalPositionSnapshot.position_id == latest.c.position_id)
                & (CanonicalPositionSnapshot.as_of_date == latest.c.as_of),
            )
            .where(
                CanonicalPositionSnapshot.organization_id == ctx.organization_id,
                CanonicalPositionSnapshot.bank_id == bank.id,
                CanonicalPositionSnapshot.superseded_by.is_(None),
            )
        )
        snapshots = {snapshot.position_id: snapshot for snapshot in db.scalars(snapshot_query)}

    items: list[CanonicalPositionRead] = []
    for position in positions:
        snapshot = snapshots.get(position.id)
        items.append(
            CanonicalPositionRead(
                id=position.id,
                source_system=position.source_system,  # type: ignore[arg-type]
                source_reference=position.source_reference,
                position_type=position.position_type,
                currency=position.currency,
                validation_status=(
                    snapshot.validation_status if snapshot else position.validation_status
                ),  # type: ignore[arg-type]
                as_of_date=snapshot.as_of_date if snapshot else position.as_of_date,
                snapshot_id=snapshot.id if snapshot else None,
                balance=snapshot.balance if snapshot else None,
                interest_rate=snapshot.interest_rate if snapshot else None,
                rate_type=snapshot.rate_type if snapshot else None,
                contractual_maturity=snapshot.contractual_maturity if snapshot else None,
                lineage_id=snapshot.lineage_id if snapshot else position.lineage_id,
            )
        )
    return CanonicalPositionListRead(
        bank_id=bank.id,
        as_of_date=as_of_date,
        positions=items,
        total=total,
        limit=limit,
        offset=offset,
    )


def list_position_facets(
    db: Session, ctx: TenantContext, bank_id: UUID
) -> CanonicalPositionFacetsRead:
    """Distinct position types and currencies with current-generation counts."""
    bank = _get_bank_or_404(db, ctx, bank_id)
    current = (
        CanonicalPosition.organization_id == ctx.organization_id,
        CanonicalPosition.bank_id == bank.id,
        CanonicalPosition.superseded_by.is_(None),
    )

    def _facet(column: Any) -> list[PositionFacetValueRead]:
        rows = db.execute(
            select(column, func.count())
            .where(*current)
            .group_by(column)
            .order_by(func.count().desc(), column)
        ).all()
        return [PositionFacetValueRead(value=value, count=int(count)) for value, count in rows]

    total = int(
        db.scalar(select(func.count()).select_from(CanonicalPosition).where(*current)) or 0
    )
    return CanonicalPositionFacetsRead(
        bank_id=bank.id,
        total=total,
        position_types=_facet(CanonicalPosition.position_type),
        currencies=_facet(CanonicalPosition.currency),
    )


_OVERRIDE_COERCIONS: dict[str, Any] = {
    "balance": lambda value: Decimal(str(value)),
    "interest_rate": lambda value: Decimal(str(value)) if value is not None else None,
    "ifrs9_stage": lambda value: int(value) if value is not None else None,
    "behavioral_maturity_months": lambda value: int(value) if value is not None else None,
}


def override_position_snapshot(
    db: Session,
    ctx: TenantContext,
    bank_id: UUID,
    snapshot_id: UUID,
    payload: PositionSnapshotOverrideCreate,
) -> PositionSnapshotRead:
    """Overlay a human decision on a snapshot field.

    Overrides never mutate in place: a new snapshot generation is written with
    a HUMAN_OVERRIDE lineage node and field-level provenance naming who, when,
    and why, and the prior generation is superseded. Only the current
    generation can be overridden.
    """
    bank = _get_bank_or_404(db, ctx, bank_id)
    snapshot = db.scalar(
        select(CanonicalPositionSnapshot).where(
            CanonicalPositionSnapshot.id == snapshot_id,
            CanonicalPositionSnapshot.organization_id == ctx.organization_id,
            CanonicalPositionSnapshot.bank_id == bank.id,
        )
    )
    if snapshot is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Position snapshot not found."
        )
    if snapshot.superseded_by is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Snapshot has been superseded; override the current generation.",
        )

    try:
        new_value = _OVERRIDE_COERCIONS[payload.field](payload.value)
    except (ValueError, ArithmeticError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Cannot read {payload.value!r} as a value for {payload.field}.",
        ) from exc
    original_value = getattr(snapshot, payload.field)

    now = utc_now()
    override = apply_manual_override(
        field_name=payload.field,
        value=new_value,
        original_value=original_value,
        user_id=str(ctx.actor_user_id),
        reason=payload.reason,
        now=now,
    )

    batch = db.get(IngestionBatch, snapshot.ingestion_batch_id)
    assert batch is not None  # FK-guaranteed
    node = _lineage(
        db,
        ctx,
        batch,
        operation_type="HUMAN_OVERRIDE",
        operation_ref=f"user/{ctx.actor_user_id}",
        inputs=(snapshot.lineage_id,),
        details={"field": payload.field, "reason": payload.reason},
    )

    replacement = CanonicalPositionSnapshot(
        id=new_uuid7(),
        organization_id=ctx.organization_id,
        bank_id=bank.id,
        as_of_date=snapshot.as_of_date,
        ingested_at=now,
        source_system=snapshot.source_system,
        source_reference=snapshot.source_reference,
        ingestion_batch_id=snapshot.ingestion_batch_id,
        validation_status=snapshot.validation_status,
        lineage_id=node.id,
        created_by=ctx.actor_user_id,
        position_id=snapshot.position_id,
        counterparty_id=snapshot.counterparty_id,
        product_id=snapshot.product_id,
        gl_account_id=snapshot.gl_account_id,
        balance=snapshot.balance,
        notional=snapshot.notional,
        interest_rate=snapshot.interest_rate,
        rate_type=snapshot.rate_type,
        rate_index=snapshot.rate_index,
        rate_spread=snapshot.rate_spread,
        contractual_maturity=snapshot.contractual_maturity,
        next_repricing_date=snapshot.next_repricing_date,
        ifrs9_stage=snapshot.ifrs9_stage,
        behavioral_maturity_months=snapshot.behavioral_maturity_months,
        enrichment_provenance={
            **snapshot.enrichment_provenance,
            **override.provenance_json(),
        },
        attributes=snapshot.attributes,
    )
    setattr(replacement, payload.field, new_value)
    snapshot.superseded_by = replacement.id
    db.flush()
    db.add(replacement)
    db.flush()

    record_event(
        db,
        ctx,
        event_type="position_snapshot.overridden",
        entity_type="canonical_position_snapshot",
        entity_id=replacement.id,
        details={
            "field": payload.field,
            "original_value": str(original_value),
            "new_value": str(new_value),
            "reason": payload.reason,
            "superseded_snapshot_id": str(snapshot.id),
        },
    )
    db.commit()
    read = PositionSnapshotRead.model_validate(replacement, from_attributes=True)
    return read.model_copy(update={"superseded_snapshot_id": snapshot.id})


def walk_lineage(db: Session, ctx: TenantContext, lineage_id: UUID) -> LineageWalkRead:
    """Walk a lineage node back to its roots, newest first."""
    nodes: list[LineageNodeRead] = []
    seen: set[UUID] = set()
    frontier = [lineage_id]
    while frontier and len(nodes) < _MAX_LINEAGE_DEPTH:
        node_id = frontier.pop(0)
        if node_id in seen:
            continue
        seen.add(node_id)
        node = db.scalar(
            select(LineageRecord).where(
                LineageRecord.id == node_id,
                LineageRecord.organization_id == ctx.organization_id,
            )
        )
        if node is None:
            if not nodes:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND, detail="Lineage record not found."
                )
            continue
        parents = [UUID(value) for value in node.input_lineage_ids]
        nodes.append(
            LineageNodeRead(
                id=node.id,
                operation_type=node.operation_type,
                operation_ref=node.operation_ref,
                ingestion_batch_id=node.ingestion_batch_id,
                input_lineage_ids=parents,
                details=node.details,
                occurred_at=node.occurred_at,
            )
        )
        frontier.extend(parents)
    return LineageWalkRead(nodes=nodes)


def _get_bank_or_404(db: Session, ctx: TenantContext, bank_id: UUID) -> Bank:
    bank = db.scalar(
        select(Bank).where(Bank.id == bank_id, Bank.organization_id == ctx.organization_id)
    )
    if bank is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bank not found.")
    return bank


def _get_batch_or_404(
    db: Session, ctx: TenantContext, bank_id: UUID, batch_id: UUID
) -> IngestionBatch:
    batch = db.scalar(
        select(IngestionBatch).where(
            IngestionBatch.id == batch_id,
            IngestionBatch.organization_id == ctx.organization_id,
            IngestionBatch.bank_id == bank_id,
        )
    )
    if batch is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Ingestion batch not found."
        )
    return batch


def _resolve_mapping_config(
    db: Session, ctx: TenantContext, bank: Bank, payload: IngestionBatchCreate
) -> MappingConfigRecord:
    if payload.mapping_config_id is not None:
        record = db.scalar(
            select(MappingConfigRecord).where(
                MappingConfigRecord.id == payload.mapping_config_id,
                MappingConfigRecord.organization_id == ctx.organization_id,
                MappingConfigRecord.bank_id == bank.id,
            )
        )
        if record is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Mapping config not found."
            )
        return record
    record = db.scalar(
        select(MappingConfigRecord).where(
            MappingConfigRecord.organization_id == ctx.organization_id,
            MappingConfigRecord.bank_id == bank.id,
            MappingConfigRecord.source_system == payload.source_system,
            MappingConfigRecord.status == "active",
        )
    )
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                f"No active mapping config for source system {payload.source_system!r}; "
                "create and activate one first."
            ),
        )
    return record


def _table_resolution_findings(
    extraction: ExtractionResult, mapping: MappingConfig
) -> list[Finding]:
    """Findings for configured tables the source did not contain.

    Every unmatched mapping is a WARNING naming the closest present table, so
    a partial upload (one file of a multi-file mapping) completes as accepted
    with warnings. When NOTHING was extracted, the batch must not complete as
    an accepted no-op: a BLOCKER lists what the source actually contains
    (with row counts) against every table the mapping expects, and the normal
    severity semantics reject the batch.
    """
    findings = [
        Finding(
            rule="table_not_found",
            category="STRUCTURAL",
            severity="WARNING",
            entity_type=unmatched.mapping,
            detail=(
                f"No table matching {list(unmatched.expected)} was found for "
                f"{unmatched.mapping!r}; the mapping was skipped."
                + (
                    f" Closest present table: {unmatched.suggestion!r}."
                    if unmatched.suggestion
                    else ""
                )
            ),
        )
        for unmatched in extraction.unmatched_mappings
    ]
    if extraction.records:
        return findings

    found = (
        "; ".join(f"{table.name} ({table.row_count} rows)" for table in extraction.source_tables)
        or "none"
    )
    expected_parts = [
        f"{entity_type}: {[entry.source_table, *entry.source_table_aliases]}"
        for entity_type, entry in mapping.field_mappings.items()
    ]
    expected_parts += [
        f"reference:{name}: {[entry.source_table, *entry.source_table_aliases]}"
        for name, entry in mapping.reference_mappings.items()
    ]
    expected = "; ".join(expected_parts) or "none (the mapping configures no tables)"
    findings.insert(
        0,
        Finding(
            rule="no_tables_matched",
            category="STRUCTURAL",
            severity="BLOCKER",
            detail=(
                "The active mapping matched no table in this source, so nothing "
                f"was extracted. Tables found in the source: {found}. "
                f"Tables the mapping expects: {expected}."
            ),
        ),
    )
    return findings


def _tables_breakdown(
    extraction: ExtractionResult,
    records: CanonicalRecords,
    record_statuses: dict[tuple[str, str], str],
) -> list[dict[str, Any]]:
    """Per-table extraction visibility for the validation report.

    One entry per table the adapter actually FOUND in the source — matched or
    not — so a multi-tab workbook shows which tabs loaded, what each resolved
    to, and how its rows fared through validation. Unmatched tables carry the
    near-miss diagnosis when a configured mapping almost resolved to them.
    """
    extracted: dict[str, int] = {}
    resolved: dict[str, set[str]] = {}
    table_by_locator: dict[str, str] = {}
    for record in extraction.records:
        table = record.source_table
        if table is None:
            continue
        extracted[table] = extracted.get(table, 0) + 1
        target = (
            f"reference:{record.dataset_kind}"
            if record.entity_type == "reference"
            else record.entity_type
        )
        resolved.setdefault(table, set()).add(target)
        table_by_locator[record.source_locator] = table

    status_counts: dict[str, dict[str, int]] = {}

    def count_status(entity_type: str, source_reference: str, source_locator: str) -> None:
        table = table_by_locator.get(source_locator)
        if table is None:
            return
        record_status = record_statuses.get((entity_type, source_reference), "accepted")
        counts = status_counts.setdefault(
            table, {"accepted": 0, "warning": 0, "error": 0, "blocked": 0}
        )
        counts[record_status] = counts.get(record_status, 0) + 1

    for gl_account in records.gl_accounts:
        count_status("gl_account", gl_account.source_reference, gl_account.source_locator)
    for counterparty in records.counterparties:
        count_status("counterparty", counterparty.source_reference, counterparty.source_locator)
    for product in records.products:
        count_status("product", product.source_reference, product.source_locator)
    for position in records.positions:
        count_status("position", position.source_reference, position.source_locator)
    for row in records.reference_rows:
        count_status("reference_row", f"{row.dataset_kind}:{row.row_index}", row.source_locator)

    def suggestion_for(table_name: str) -> str | None:
        for unmatched in extraction.unmatched_mappings:
            if unmatched.suggestion == table_name:
                return (
                    f"Near-match for configured mapping {unmatched.mapping!r} "
                    f"(expected one of {list(unmatched.expected)})."
                )
        return None

    breakdown: list[dict[str, Any]] = []
    for table in extraction.source_tables:
        counts = status_counts.get(table.name, {})
        resolved_to = " + ".join(sorted(resolved.get(table.name, ()))) or None
        breakdown.append(
            {
                "source_table": table.name,
                "resolved_to": resolved_to,
                "rows_extracted": extracted.get(table.name, 0),
                "rows_accepted": counts.get("accepted", 0),
                "rows_warning": counts.get("warning", 0),
                "rows_error": counts.get("error", 0),
                "rows_blocked": counts.get("blocked", 0),
                "suggestion": None if resolved_to else suggestion_for(table.name),
            }
        )
    return breakdown


def _resolve_adapter(source_system: str) -> SourceAdapter:
    try:
        adapter_cls = get_adapter_class(source_system)
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    return adapter_cls()


def _new_batch(
    ctx: TenantContext,
    bank: Bank,
    payload: IngestionBatchCreate,
    adapter: SourceAdapter,
    mapping_record: MappingConfigRecord,
) -> IngestionBatch:
    identity = adapter.identify()
    return IngestionBatch(
        organization_id=ctx.organization_id,
        bank_id=bank.id,
        source_system=payload.source_system,
        adapter_version=identity.version,
        extraction_mode="full",
        status="created",
        as_of_date=payload.as_of_date,
        mapping_config_id=mapping_record.id,
        created_by=ctx.actor_user_id,
    )


def _find_accepted_batch(  # noqa: PLR0913 - mirrors record_event's shape
    db: Session,
    ctx: TenantContext,
    bank: Bank,
    payload: IngestionBatchCreate,
    content_hash: str | None,
    mapping_config_id: UUID,
) -> IngestionBatch | None:
    """Idempotency lookup: the same content under the same mapping for the
    same business date. The mapping config is part of the key — re-ingesting
    one workbook under a different mapping (e.g. its Deposits sheet after its
    Loans sheet) is deliberately a new batch."""
    if content_hash is None:
        return None
    return db.scalar(
        select(IngestionBatch).where(
            IngestionBatch.organization_id == ctx.organization_id,
            IngestionBatch.bank_id == bank.id,
            IngestionBatch.source_system == payload.source_system,
            IngestionBatch.as_of_date == payload.as_of_date,
            IngestionBatch.content_hash == content_hash,
            IngestionBatch.mapping_config_id == mapping_config_id,
            IngestionBatch.status.in_(BATCH_ACCEPTED_STATUSES),
        )
    )


def _fail_batch(  # noqa: PLR0913 - mirrors record_event's shape
    db: Session,
    ctx: TenantContext,
    batch: IngestionBatch,
    reason: str,
    code: str,
    message: str,
) -> IngestionBatchStartRead:
    """Persist a batch that never reached translation; failures are history too."""
    batch.status = "failed"
    batch.error_code = code
    batch.error_message = message
    batch.completed_at = utc_now()
    db.add(batch)
    _record_batch_event(db, ctx, batch, reason)
    db.commit()
    return IngestionBatchStartRead(
        batch=IngestionBatchRead.model_validate(batch, from_attributes=True), reused=False
    )


def _lineage(  # noqa: PLR0913 - mirrors record_event's shape
    db: Session,
    ctx: TenantContext,
    batch: IngestionBatch,
    *,
    operation_type: str,
    operation_ref: str,
    inputs: tuple[UUID, ...],
    details: dict[str, Any],
) -> LineageRecord:
    node = LineageRecord(
        organization_id=ctx.organization_id,
        ingestion_batch_id=batch.id,
        operation_type=operation_type,
        operation_ref=operation_ref,
        input_lineage_ids=[str(input_id) for input_id in inputs],
        details=details,
    )
    db.add(node)
    db.flush()
    return node


def _known_references(
    db: Session, ctx: TenantContext, bank: Bank
) -> tuple[frozenset[str], frozenset[str], frozenset[str]]:
    """Current-generation canonical references already ingested for the bank."""

    def current(column, model) -> frozenset[str]:
        return frozenset(
            db.scalars(
                select(column).where(
                    model.organization_id == ctx.organization_id,
                    model.bank_id == bank.id,
                    model.superseded_by.is_(None),
                )
            )
        )

    return (
        current(CanonicalCounterparty.source_reference, CanonicalCounterparty),
        current(CanonicalProduct.product_code, CanonicalProduct),
        current(CanonicalGlAccount.account_code, CanonicalGlAccount),
    )


def _prior_balances(
    db: Session, ctx: TenantContext, bank: Bank, as_of_date: date
) -> dict[str, Decimal] | None:
    rows = db.execute(
        select(
            CanonicalPosition.source_reference,
            CanonicalPositionSnapshot.balance,
            CanonicalPositionSnapshot.as_of_date,
        )
        .join(
            CanonicalPosition,
            CanonicalPosition.id == CanonicalPositionSnapshot.position_id,
        )
        .where(
            CanonicalPositionSnapshot.organization_id == ctx.organization_id,
            CanonicalPositionSnapshot.bank_id == bank.id,
            CanonicalPositionSnapshot.superseded_by.is_(None),
            CanonicalPositionSnapshot.as_of_date < as_of_date,
        )
        .order_by(
            CanonicalPosition.source_reference,
            CanonicalPositionSnapshot.as_of_date.desc(),
        )
    ).all()
    balances: dict[str, Decimal] = {}
    for source_reference, balance, _snapshot_date in rows:
        balances.setdefault(source_reference, balance)
    return balances or None


def _persist_canonical(  # noqa: PLR0913, PLR0915
    db: Session,
    ctx: TenantContext,
    bank: Bank,
    batch: IngestionBatch,
    lineage_node: LineageRecord,
    records: CanonicalRecords,
    record_statuses: dict[tuple[str, str], str],
) -> None:
    """Write the batch's canonical rows, superseding same-key current rows.

    Current-generation lookups are preloaded per entity type instead of
    queried per record, so a 139k-row deposit book persists in a handful of
    statements. Supersessions of pre-existing rows are flushed before the
    batch's inserts so the partial unique indexes over the current generation
    never see two live rows for one natural key.
    """
    common = {
        "organization_id": ctx.organization_id,
        "bank_id": bank.id,
        "ingestion_batch_id": batch.id,
        "lineage_id": lineage_node.id,
        "created_by": ctx.actor_user_id,
        "ingested_at": utc_now(),
    }

    def status_of(entity_type: str, source_reference: str) -> str:
        return record_statuses.get((entity_type, source_reference), "accepted")

    def existing_ids(key_column: Any, model: Any) -> dict[str, UUID]:
        rows = db.execute(
            select(key_column, model.id).where(
                model.organization_id == ctx.organization_id,
                model.bank_id == bank.id,
                model.superseded_by.is_(None),
            )
        )
        return {key: row_id for key, row_id in rows}

    def current_by_key(key_column, model, *conditions: Any) -> dict[str, Any]:
        rows = db.execute(
            select(key_column, model).where(
                model.organization_id == ctx.organization_id,
                model.bank_id == bank.id,
                model.superseded_by.is_(None),
                *conditions,
            )
        )
        return {key: row for key, row in rows}

    def supersede(current: dict[Any, Any], key: Any, row: Any) -> None:
        """Mark the current-generation holder of ``key`` superseded by ``row``.

        Handles intra-batch duplicates too: the map always tracks the newest
        generation, whether persistent or pending.
        """
        prior = current.get(key)
        if prior is not None:
            prior.superseded_by = row.id
        current[key] = row

    # Batch records supersede same-key existing rows below, so batch entries
    # overwrite these lookups as they are (re)persisted.
    gl_ids: dict[str, UUID] = existing_ids(CanonicalGlAccount.account_code, CanonicalGlAccount)
    current_gl = current_by_key(
        CanonicalGlAccount.account_code,
        CanonicalGlAccount,
        CanonicalGlAccount.as_of_date == batch.as_of_date,
    )
    new_gl: dict[str, CanonicalGlAccount] = {}
    new_gl_rows: list[CanonicalGlAccount] = []
    for data in records.gl_accounts:
        row = CanonicalGlAccount(
            id=new_uuid7(),
            as_of_date=batch.as_of_date,
            source_system=batch.source_system,
            source_reference=data.source_reference,
            validation_status=status_of("gl_account", data.source_reference),
            account_code=data.account_code,
            name=data.name,
            account_class=data.account_class,
            currency=data.currency,
            balance=data.balance,
            attributes=data.attributes,
            **common,
        )
        supersede(current_gl, data.account_code, row)
        gl_ids[data.account_code] = row.id
        new_gl[data.account_code] = row
        new_gl_rows.append(row)

    counterparty_ids: dict[str, UUID] = existing_ids(
        CanonicalCounterparty.source_reference, CanonicalCounterparty
    )
    current_counterparties = current_by_key(
        CanonicalCounterparty.source_reference,
        CanonicalCounterparty,
        CanonicalCounterparty.source_system == batch.source_system,
        CanonicalCounterparty.as_of_date == batch.as_of_date,
    )
    new_counterparties: list[CanonicalCounterparty] = []
    for data in records.counterparties:
        row = CanonicalCounterparty(
            id=new_uuid7(),
            as_of_date=batch.as_of_date,
            source_system=batch.source_system,
            source_reference=data.source_reference,
            validation_status=status_of("counterparty", data.source_reference),
            name=data.name,
            counterparty_type=data.counterparty_type,
            country_code=data.country_code,
            rating=data.rating,
            rating_source=data.rating_source,
            group_reference=data.group_reference,
            external_identifiers=data.external_identifiers,
            attributes=data.attributes,
            **common,
        )
        supersede(current_counterparties, data.source_reference, row)
        counterparty_ids[data.source_reference] = row.id
        new_counterparties.append(row)

    product_ids: dict[str, UUID] = existing_ids(CanonicalProduct.product_code, CanonicalProduct)
    current_products = current_by_key(
        CanonicalProduct.product_code,
        CanonicalProduct,
        CanonicalProduct.as_of_date == batch.as_of_date,
    )
    new_products: list[CanonicalProduct] = []
    for data in records.products:
        row = CanonicalProduct(
            id=new_uuid7(),
            as_of_date=batch.as_of_date,
            source_system=batch.source_system,
            source_reference=data.source_reference,
            validation_status=status_of("product", data.source_reference),
            product_code=data.product_code,
            name=data.name,
            regulatory_category=data.regulatory_category,
            risk_weight_code=data.risk_weight_code,
            attributes=data.attributes,
            **common,
        )
        supersede(current_products, data.product_code, row)
        product_ids[data.product_code] = row.id
        new_products.append(row)

    current_positions = current_by_key(
        CanonicalPosition.source_reference,
        CanonicalPosition,
        CanonicalPosition.source_system == batch.source_system,
    )
    current_snapshots = current_by_key(
        CanonicalPositionSnapshot.position_id,
        CanonicalPositionSnapshot,
        CanonicalPositionSnapshot.as_of_date == batch.as_of_date,
    )
    new_positions: list[CanonicalPosition] = []
    new_snapshots: list[CanonicalPositionSnapshot] = []
    for data in records.positions:
        position = current_positions.get(data.source_reference)
        if position is None:
            position = CanonicalPosition(
                id=new_uuid7(),
                as_of_date=batch.as_of_date,
                source_system=batch.source_system,
                source_reference=data.source_reference,
                validation_status=status_of("position", data.source_reference),
                position_type=data.position_type,
                currency=data.currency,
                origination_date=data.origination_date,
                **common,
            )
            current_positions[data.source_reference] = position
            new_positions.append(position)

        snapshot = CanonicalPositionSnapshot(
            id=new_uuid7(),
            as_of_date=batch.as_of_date,
            source_system=batch.source_system,
            source_reference=data.source_reference,
            validation_status=status_of("position", data.source_reference),
            position_id=position.id,
            counterparty_id=(
                counterparty_ids.get(data.counterparty_reference)
                if data.counterparty_reference
                else None
            ),
            product_id=product_ids.get(data.product_code) if data.product_code else None,
            gl_account_id=gl_ids.get(data.gl_account_code) if data.gl_account_code else None,
            balance=data.balance,
            notional=data.notional,
            interest_rate=data.interest_rate,
            rate_type=data.rate_type,
            rate_index=data.rate_index,
            rate_spread=data.rate_spread,
            contractual_maturity=data.contractual_maturity,
            next_repricing_date=data.next_repricing_date,
            ifrs9_stage=data.ifrs9_stage,
            attributes=data.attributes,
            **common,
        )
        supersede(current_snapshots, position.id, snapshot)
        new_snapshots.append(snapshot)

    # Reference rows are batch-scoped; they share the batch's VALIDATION
    # lineage node like every other record of the batch (one node per batch,
    # not per row — cheap and consistent).
    new_references = [
        CanonicalReferenceRow(
            id=new_uuid7(),
            organization_id=ctx.organization_id,
            bank_id=bank.id,
            ingestion_batch_id=batch.id,
            as_of_date=batch.as_of_date,
            dataset_kind=data.dataset_kind,
            row_index=data.row_index,
            payload=dict(data.payload),
            source_reference=data.source_locator[:255],
            lineage_id=lineage_node.id,
        )
        for data in records.reference_rows
    ]

    # Supersession UPDATEs of persistent rows go first so the partial unique
    # indexes never see two current-generation rows during the inserts. The
    # inserts are then flushed in FK dependency order explicitly: without
    # relationship() mappings the unit of work does not order tables itself.
    db.flush()
    db.add_all(new_gl_rows)
    db.flush()
    # Second pass wires the GL hierarchy once every account row exists.
    for data in records.gl_accounts:
        child = new_gl.get(data.account_code)
        if child is not None and data.parent_account_code and data.parent_account_code in gl_ids:
            child.parent_account_id = gl_ids[data.parent_account_code]
    db.add_all(new_counterparties)
    db.add_all(new_products)
    db.add_all(new_positions)
    db.flush()
    db.add_all(new_snapshots)
    db.add_all(new_references)
    db.flush()


def _artifact_step(
    db: Session,
    ctx: TenantContext,
    batch_context: tuple[Bank, IngestionBatchCreate, SourceAdapter, MappingConfigRecord],
    persist: Callable[[], None],
) -> IngestionBatchStartRead | None:
    """Run a storage persistence step; a StorageError fails the batch loudly.

    The in-flight transaction (including any canonical rows) is rolled back —
    a batch whose artifacts cannot be stored must not present itself as
    accepted history (storage.md §1.3).
    """
    try:
        persist()
        return None
    except StorageError as exc:
        db.rollback()
        bank, payload, adapter, mapping_record = batch_context
        batch = _new_batch(ctx, bank, payload, adapter, mapping_record)
        return _fail_batch(db, ctx, batch, payload.reason, "storage_error", str(exc))


TEMP_SCHEME = "temp://"
_FILENAME_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


def upload_source(  # noqa: PLR0913 - mirrors record_event's shape
    db: Session,
    ctx: TenantContext,
    bank_id: UUID,
    storage: StorageClient,
    filename: str,
    content: bytes,
) -> IngestionUploadRead:
    """Stage an uploaded source file in the bank's temp tier.

    Uploads are proxied through the API rather than presigned so every byte
    flows through StorageClient: encrypted, access-logged, and stamped with
    provenance metadata. The temp tier's 30-day lifecycle cleans up staged
    files that never get ingested.
    """
    bank = _get_bank_or_404(db, ctx, bank_id)
    slug = bank_slug(db, bank)
    storage.ensure_institution(slug)

    safe_name = _FILENAME_UNSAFE.sub("_", Path(filename or "upload").name) or "upload"
    checksum = hashlib.sha256(content).hexdigest()
    object_path = f"uploads/{new_uuid7()}/{safe_name}"
    location = StorageLocation(institution_slug=slug, tier="temp", object_path=object_path)
    storage.write(
        location,
        io.BytesIO(content),
        ObjectMetadata(
            institution_slug=slug,
            tier="temp",
            checksum_sha256=checksum,
            written_at=utc_now(),
            written_by=str(ctx.actor_user_id),
            source_reference=safe_name,
        ),
    )
    record_event(
        db,
        ctx,
        event_type="ingestion_upload.staged",
        entity_type="bank",
        entity_id=bank.id,
        details={"object_path": object_path, "filename": safe_name, "byte_size": len(content)},
    )
    db.commit()
    return IngestionUploadRead(
        object_path=object_path,
        filename=safe_name,
        byte_size=len(content),
        checksum_sha256=checksum,
        location=f"{TEMP_SCHEME}{object_path}",
    )


def _materialize_source(
    db: Session, bank: Bank, payload: IngestionBatchCreate, storage: StorageClient
) -> str:
    """Resolve the batch source to a local file path the adapter can read.

    ``temp://{object_path}`` locations are fetched from the bank's temp tier
    into a scratch directory, preserving the original filename so the adapter
    recognizes the format by suffix. Plain paths pass through unchanged.
    """
    if not payload.location.startswith(TEMP_SCHEME):
        return payload.location
    object_path = payload.location[len(TEMP_SCHEME) :]
    slug = bank_slug(db, bank)
    _, stream = storage.read(
        StorageLocation(institution_slug=slug, tier="temp", object_path=object_path)
    )
    scratch = Path(tempfile.mkdtemp(prefix="aequoros-ingest-"))
    local = scratch / (Path(object_path).name or "upload")
    local.write_bytes(stream.read())
    return str(local)


_SLUG_UNSAFE = re.compile(r"[^a-z0-9-]+")


def bank_slug(db: Session, bank: Bank) -> str:
    """The bank's DNS-safe storage slug, assigned on first use.

    Derived from the short name plus an id fragment so it stays unique and
    stable even if two banks share a short name; persisted so bucket names
    never change once assigned.
    """
    if bank.storage_slug is None:
        base = _SLUG_UNSAFE.sub("-", bank.short_name.lower()).strip("-") or "bank"
        bank.storage_slug = f"{base[:40]}-{bank.id.hex[:6]}"
        db.flush()
    return bank.storage_slug


def _persist_raw_artifact(  # noqa: PLR0913 - mirrors record_event's shape
    ctx: TenantContext,
    slug: str,
    batch: IngestionBatch,
    extract_node: LineageRecord,
    source_path: str,
    storage: StorageClient,
) -> None:
    """Store the untouched source file in the raw tier (storage.md §1.3)."""
    storage.ensure_institution(slug)
    source = Path(source_path)
    content = source.read_bytes()
    location = StorageLocation(
        institution_slug=slug,
        tier="raw",
        object_path=(f"{batch.source_system.lower()}/{batch.as_of_date}/{batch.id}/{source.name}"),
    )
    storage.write(
        location,
        io.BytesIO(content),
        ObjectMetadata(
            institution_slug=slug,
            tier="raw",
            checksum_sha256=batch.content_hash or hashlib.sha256(content).hexdigest(),
            written_at=utc_now(),
            written_by=str(ctx.actor_user_id),
            as_of_date=str(batch.as_of_date),
            ingestion_batch_id=str(batch.id),
            lineage_node_id=str(extract_node.id),
            source_system=batch.source_system,
            source_reference=source.name,
        ),
    )
    batch.raw_artifact_path = location.object_path


def _persist_report_artifact(
    ctx: TenantContext,
    slug: str,
    batch: IngestionBatch,
    validate_node: LineageRecord,
    storage: StorageClient,
) -> None:
    """Store the operator-facing validation report in the outputs tier."""
    content = json.dumps(batch.validation_report, sort_keys=True).encode()
    location = StorageLocation(
        institution_slug=slug,
        tier="outputs",
        object_path=(f"validation_reports/{batch.as_of_date}/{batch.id}/validation_report.json"),
    )
    storage.write(
        location,
        io.BytesIO(content),
        ObjectMetadata(
            institution_slug=slug,
            tier="outputs",
            checksum_sha256=hashlib.sha256(content).hexdigest(),
            written_at=utc_now(),
            written_by=str(ctx.actor_user_id),
            as_of_date=str(batch.as_of_date),
            ingestion_batch_id=str(batch.id),
            lineage_node_id=str(validate_node.id),
            source_system=batch.source_system,
        ),
        content_type="application/json",
    )
    batch.report_artifact_path = location.object_path


def _record_batch_event(
    db: Session, ctx: TenantContext, batch: IngestionBatch, reason: str
) -> None:
    record_event(
        db,
        ctx,
        event_type=f"ingestion_batch.{batch.status}",
        entity_type="ingestion_batch",
        entity_id=batch.id,
        details={
            "source_system": batch.source_system,
            "as_of_date": str(batch.as_of_date),
            "records_extracted": batch.records_extracted,
            "records_accepted": batch.records_accepted,
            "reason": reason,
        },
    )
