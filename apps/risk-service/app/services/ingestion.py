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
from collections.abc import Callable
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

import app.adapters  # noqa: F401 - importing registers every shipped source adapter
from app.api.deps import TenantContext
from app.core.ids import new_uuid7
from app.db.base import utc_now
from app.domain.ingestion.adapter import SourceAdapter, get_adapter_class
from app.domain.ingestion.constants import BATCH_ACCEPTED_STATUSES
from app.domain.ingestion.contracts import (
    ENTITY_TYPES,
    AdapterConfig,
    CanonicalRecords,
    MappingConfig,
)
from app.domain.ingestion.enrichment import apply_manual_override
from app.domain.ingestion.validation import (
    ValidationContext,
    build_validation_report,
    default_validation_config,
    run_validation,
)
from app.models import (
    Bank,
    CanonicalCounterparty,
    CanonicalGlAccount,
    CanonicalPosition,
    CanonicalPositionSnapshot,
    CanonicalProduct,
    IngestionBatch,
    LineageRecord,
    MappingConfigRecord,
    TranslationFailure,
)
from app.schemas.ingestion import (
    CanonicalPositionListRead,
    CanonicalPositionRead,
    IngestionBatchCreate,
    IngestionBatchListRead,
    IngestionBatchRead,
    IngestionBatchStartRead,
    LineageNodeRead,
    LineageWalkRead,
    MappingConfigCreate,
    MappingConfigListRead,
    MappingConfigRead,
    PositionSnapshotOverrideCreate,
    PositionSnapshotRead,
    TranslationFailureListRead,
    TranslationFailureRead,
)
from app.services.audit import record_event
from app.storage.client import (
    ObjectMetadata,
    StorageClient,
    StorageError,
    StorageLocation,
)

_MAX_LINEAGE_DEPTH = 50


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


def start_ingestion(
    db: Session,
    ctx: TenantContext,
    bank_id: UUID,
    payload: IngestionBatchCreate,
    storage: StorageClient,
) -> IngestionBatchStartRead:
    bank = _get_bank_or_404(db, ctx, bank_id)
    mapping_record = _resolve_mapping_config(db, ctx, bank, payload)
    mapping = MappingConfig.model_validate(mapping_record.config)
    adapter = _resolve_adapter(payload.source_system)

    entity_tables = {
        entity_type: entity_mapping.source_table
        for entity_type, entity_mapping in mapping.field_mappings.items()
    }
    adapter_config = AdapterConfig(
        location=payload.location,
        options={"entity_tables": entity_tables, **payload.adapter_options},
    )

    connection = adapter.validate_connection(adapter_config)
    if not connection.ok:
        batch = _new_batch(ctx, bank, payload, adapter, mapping_record)
        return _fail_batch(db, ctx, batch, payload.reason, "connection_failed", connection.detail)

    try:
        extraction = adapter.extract(adapter_config, payload.as_of_date, list(ENTITY_TYPES))
    except Exception as exc:  # noqa: BLE001 - a broken source fails the batch, not the API
        batch = _new_batch(ctx, bank, payload, adapter, mapping_record)
        return _fail_batch(db, ctx, batch, payload.reason, "extraction_failed", str(exc))

    existing = _find_accepted_batch(db, ctx, bank, payload, extraction.content_hash)
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
            ctx, bank_slug(db, bank), batch, extract_node, payload, storage
        ),
    )
    if storage_failure is not None:
        return storage_failure

    batch.status = "translating"
    records = adapter.translate(extraction, mapping)
    batch.records_translated = records.record_count
    translate_node = _lineage(
        db,
        ctx,
        batch,
        operation_type="ADAPTER_TRANSLATE",
        operation_ref=f"mapping_config/v{mapping_record.version}",
        inputs=(extract_node.id,),
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
    context = ValidationContext(
        as_of_date=payload.as_of_date,
        prior_balances=_prior_balances(db, ctx, bank, payload.as_of_date),
    )
    outcome = run_validation(records, default_validation_config(), context)
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
    db.commit()
    return IngestionBatchStartRead(
        batch=IngestionBatchRead.model_validate(batch, from_attributes=True), reused=False
    )


def list_batches(db: Session, ctx: TenantContext, bank_id: UUID) -> IngestionBatchListRead:
    bank = _get_bank_or_404(db, ctx, bank_id)
    batches = db.scalars(
        select(IngestionBatch)
        .where(
            IngestionBatch.organization_id == ctx.organization_id,
            IngestionBatch.bank_id == bank.id,
        )
        .order_by(IngestionBatch.id.desc())
    )
    return IngestionBatchListRead(
        bank_id=bank.id,
        batches=[
            IngestionBatchRead.model_validate(batch, from_attributes=True) for batch in batches
        ],
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


def list_positions(
    db: Session, ctx: TenantContext, bank_id: UUID, as_of_date: date | None
) -> CanonicalPositionListRead:
    bank = _get_bank_or_404(db, ctx, bank_id)
    snapshot_query = select(CanonicalPositionSnapshot).where(
        CanonicalPositionSnapshot.organization_id == ctx.organization_id,
        CanonicalPositionSnapshot.bank_id == bank.id,
        CanonicalPositionSnapshot.superseded_by.is_(None),
    )
    if as_of_date is not None:
        snapshot_query = snapshot_query.where(CanonicalPositionSnapshot.as_of_date == as_of_date)
    snapshots = {snapshot.position_id: snapshot for snapshot in db.scalars(snapshot_query)}

    positions = db.scalars(
        select(CanonicalPosition)
        .where(
            CanonicalPosition.organization_id == ctx.organization_id,
            CanonicalPosition.bank_id == bank.id,
            CanonicalPosition.superseded_by.is_(None),
        )
        .order_by(CanonicalPosition.source_reference)
    )
    items: list[CanonicalPositionRead] = []
    for position in positions:
        snapshot = snapshots.get(position.id)
        if as_of_date is not None and snapshot is None:
            continue
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
    return CanonicalPositionListRead(bank_id=bank.id, as_of_date=as_of_date, positions=items)


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


def _find_accepted_batch(
    db: Session,
    ctx: TenantContext,
    bank: Bank,
    payload: IngestionBatchCreate,
    content_hash: str | None,
) -> IngestionBatch | None:
    if content_hash is None:
        return None
    return db.scalar(
        select(IngestionBatch).where(
            IngestionBatch.organization_id == ctx.organization_id,
            IngestionBatch.bank_id == bank.id,
            IngestionBatch.source_system == payload.source_system,
            IngestionBatch.as_of_date == payload.as_of_date,
            IngestionBatch.content_hash == content_hash,
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


def _persist_canonical(  # noqa: PLR0913
    db: Session,
    ctx: TenantContext,
    bank: Bank,
    batch: IngestionBatch,
    lineage_node: LineageRecord,
    records: CanonicalRecords,
    record_statuses: dict[tuple[str, str], str],
) -> None:
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

    gl_ids: dict[str, UUID] = {}
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
        _supersede_current(
            db,
            CanonicalGlAccount,
            row.id,
            CanonicalGlAccount.organization_id == ctx.organization_id,
            CanonicalGlAccount.bank_id == bank.id,
            CanonicalGlAccount.account_code == data.account_code,
            CanonicalGlAccount.as_of_date == batch.as_of_date,
        )
        db.add(row)
        gl_ids[data.account_code] = row.id
    db.flush()
    # Second pass wires the hierarchy once every account id exists.
    for data in records.gl_accounts:
        if data.parent_account_code and data.parent_account_code in gl_ids:
            child = db.get(CanonicalGlAccount, gl_ids[data.account_code])
            if child is not None:
                child.parent_account_id = gl_ids[data.parent_account_code]

    counterparty_ids: dict[str, UUID] = {}
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
        _supersede_current(
            db,
            CanonicalCounterparty,
            row.id,
            CanonicalCounterparty.organization_id == ctx.organization_id,
            CanonicalCounterparty.bank_id == bank.id,
            CanonicalCounterparty.source_system == batch.source_system,
            CanonicalCounterparty.source_reference == data.source_reference,
            CanonicalCounterparty.as_of_date == batch.as_of_date,
        )
        db.add(row)
        counterparty_ids[data.source_reference] = row.id

    product_ids: dict[str, UUID] = {}
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
        _supersede_current(
            db,
            CanonicalProduct,
            row.id,
            CanonicalProduct.organization_id == ctx.organization_id,
            CanonicalProduct.bank_id == bank.id,
            CanonicalProduct.product_code == data.product_code,
            CanonicalProduct.as_of_date == batch.as_of_date,
        )
        db.add(row)
        product_ids[data.product_code] = row.id
    db.flush()

    for data in records.positions:
        position = db.scalar(
            select(CanonicalPosition).where(
                CanonicalPosition.organization_id == ctx.organization_id,
                CanonicalPosition.bank_id == bank.id,
                CanonicalPosition.source_system == batch.source_system,
                CanonicalPosition.source_reference == data.source_reference,
                CanonicalPosition.superseded_by.is_(None),
            )
        )
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
            db.add(position)
            db.flush()

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
        _supersede_current(
            db,
            CanonicalPositionSnapshot,
            snapshot.id,
            CanonicalPositionSnapshot.organization_id == ctx.organization_id,
            CanonicalPositionSnapshot.position_id == position.id,
            CanonicalPositionSnapshot.as_of_date == batch.as_of_date,
        )
        db.add(snapshot)
    db.flush()


def _supersede_current(db: Session, model: type, new_id: UUID, *conditions: Any) -> None:
    current = db.scalar(
        select(model).where(*conditions, model.superseded_by.is_(None))  # type: ignore[attr-defined]
    )
    if current is not None:
        current.superseded_by = new_id
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
    payload: IngestionBatchCreate,
    storage: StorageClient,
) -> None:
    """Store the untouched source file in the raw tier (storage.md §1.3)."""
    storage.ensure_institution(slug)
    source = Path(payload.location)
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
