"""Push ingestion staging: open a push batch, stage record pages, commit.

The push flow is a new SOURCE, not a new pipeline. Staged pages live as JSON
objects in the bank's encrypted ``temp`` storage tier (no staging tables);
commit assembles them into one deterministic JSON document and runs the
EXISTING ingestion orchestration (`start_ingestion`) with source system
``API_PUSH`` — same translation, validation gating, lineage, canonical
persistence, and storage artifacts as a file upload.

Idempotency is two-layered: the client's ``idempotency_key`` names the push
batch (reopening returns it, recommitting returns its batch), and the
assembled document's content hash feeds the orchestrator's existing
accepted-batch reuse.
"""

from __future__ import annotations

import hashlib
import io
import json
from datetime import date
from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters.api_push import identity_mapping_config
from app.api.deps import TenantContext
from app.core.ids import new_uuid7
from app.db.base import utc_now
from app.models import Bank, IngestionBatch, MappingConfigRecord
from app.schemas.ingestion import (
    IngestionBatchCreate,
    IngestionBatchRead,
    IngestionBatchStartRead,
    MappingConfigCreate,
)
from app.schemas.push import (
    MAX_RECORDS_PER_PAGE,
    PushBatchOpen,
    PushBatchStatusRead,
    PushRecordsPage,
)
from app.services.audit import record_event
from app.services.ingestion import (
    TEMP_SCHEME,
    bank_slug,
    create_mapping_config,
    start_ingestion,
)
from app.storage.client import (
    ObjectMetadata,
    StorageClient,
    StorageLocation,
    StorageNotFoundError,
)

EXPIRES_NOTE = (
    "Staged pages live in the bank's temp storage tier; batches never "
    "committed are cleaned up by its 30-day lifecycle."
)

IDENTITY_MAPPING_NAME = "API push identity mapping"


def open_push_batch(
    db: Session,
    ctx: TenantContext,
    bank_id: UUID,
    payload: PushBatchOpen,
    storage: StorageClient,
) -> PushBatchStatusRead:
    bank = _get_bank_or_404(db, ctx, bank_id)
    slug = bank_slug(db, bank)
    storage.ensure_institution(slug)

    existing = _find_by_idempotency_key(ctx, bank, slug, payload.idempotency_key, storage)
    if existing is not None:
        if existing["as_of_date"] != payload.as_of_date.isoformat():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"Idempotency key {payload.idempotency_key!r} was already used for "
                    f"as-of date {existing['as_of_date']}; use a new key per submission."
                ),
            )
        return _status_read(existing)

    push_id = new_uuid7()
    manifest: dict[str, Any] = {
        "push_batch_id": str(push_id),
        "organization_id": str(ctx.organization_id),
        "bank_id": str(bank.id),
        "as_of_date": payload.as_of_date.isoformat(),
        "idempotency_key": payload.idempotency_key,
        "reason": payload.reason,
        "created_by": str(ctx.actor_user_id) if ctx.actor_user_id else None,
        "status": "staging",
        "pages": 0,
        "records_staged": {},
        "committed_batch_id": None,
    }
    _write_json(storage, ctx, slug, _manifest_location(slug, push_id), manifest)
    _write_json(
        storage,
        ctx,
        slug,
        _index_location(slug, payload.idempotency_key),
        {"push_batch_id": str(push_id)},
    )
    record_event(
        db,
        ctx,
        event_type="push_batch.opened",
        entity_type="push_batch",
        entity_id=push_id,
        details={
            "as_of_date": payload.as_of_date.isoformat(),
            "idempotency_key": payload.idempotency_key,
            "reason": payload.reason,
        },
    )
    db.commit()
    return _status_read(manifest)


def stage_push_records(  # noqa: PLR0913 - mirrors record_event's shape
    db: Session,
    ctx: TenantContext,
    bank_id: UUID,
    push_batch_id: UUID,
    page: PushRecordsPage,
    storage: StorageClient,
) -> PushBatchStatusRead:
    bank = _get_bank_or_404(db, ctx, bank_id)
    slug = bank_slug(db, bank)
    manifest = _get_manifest_or_404(ctx, bank, slug, push_batch_id, storage)
    if manifest["status"] == "committed":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Push batch is already committed; open a new push batch for new records.",
        )
    if page.record_count == 0:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Records page contains no records.",
        )
    if page.record_count > MAX_RECORDS_PER_PAGE:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=(
                f"Records page exceeds {MAX_RECORDS_PER_PAGE} records "
                f"({page.record_count} sent); split it across pages."
            ),
        )

    page_number = int(manifest["pages"]) + 1
    _write_json(
        storage,
        ctx,
        slug,
        _page_location(slug, push_batch_id, page_number),
        {"entities": page.entities, "reference": page.reference},
    )
    totals: dict[str, int] = dict(manifest["records_staged"])
    for section in (page.entities, page.reference):
        for key, rows in section.items():
            totals[key] = totals.get(key, 0) + len(rows)
    manifest["pages"] = page_number
    manifest["records_staged"] = totals
    _write_json(storage, ctx, slug, _manifest_location(slug, push_batch_id), manifest)

    record_event(
        db,
        ctx,
        event_type="push_batch.records_staged",
        entity_type="push_batch",
        entity_id=push_batch_id,
        details={"page": page_number, "records": page.record_count},
    )
    db.commit()
    return _status_read(manifest)


def commit_push_batch(
    db: Session,
    ctx: TenantContext,
    bank_id: UUID,
    push_batch_id: UUID,
    storage: StorageClient,
) -> IngestionBatchStartRead:
    bank = _get_bank_or_404(db, ctx, bank_id)
    slug = bank_slug(db, bank)
    manifest = _get_manifest_or_404(ctx, bank, slug, push_batch_id, storage)

    if manifest["committed_batch_id"] is not None:
        batch = _get_batch_or_404(db, ctx, bank.id, UUID(manifest["committed_batch_id"]))
        return IngestionBatchStartRead(
            batch=IngestionBatchRead.model_validate(batch, from_attributes=True), reused=True
        )
    if int(manifest["pages"]) == 0:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="No records staged; stage at least one records page before committing.",
        )

    document = _assemble_document(slug, push_batch_id, manifest, storage)
    source_location = _source_location(slug, push_batch_id)
    _write_json(storage, ctx, slug, source_location, document, sort_keys=True)

    _ensure_active_mapping(db, ctx, bank)
    started = start_ingestion(
        db,
        ctx,
        bank.id,
        IngestionBatchCreate(
            source_system="API_PUSH",
            as_of_date=date.fromisoformat(manifest["as_of_date"]),
            location=f"{TEMP_SCHEME}{source_location.object_path}",
            reason=manifest["reason"],
        ),
        storage,
    )

    manifest["status"] = "committed"
    manifest["committed_batch_id"] = str(started.batch.id)
    _write_json(storage, ctx, slug, _manifest_location(slug, push_batch_id), manifest)
    record_event(
        db,
        ctx,
        event_type="push_batch.committed",
        entity_type="push_batch",
        entity_id=push_batch_id,
        details={
            "ingestion_batch_id": str(started.batch.id),
            "batch_status": started.batch.status,
            "reused": started.reused,
        },
    )
    db.commit()
    return started


def get_push_batch(
    db: Session,
    ctx: TenantContext,
    bank_id: UUID,
    push_batch_id: UUID,
    storage: StorageClient,
) -> PushBatchStatusRead:
    bank = _get_bank_or_404(db, ctx, bank_id)
    slug = bank_slug(db, bank)
    return _status_read(_get_manifest_or_404(ctx, bank, slug, push_batch_id, storage))


def _assemble_document(
    slug: str,
    push_batch_id: UUID,
    manifest: dict[str, Any],
    storage: StorageClient,
) -> dict[str, Any]:
    """Merge the staged pages into the adapter's source document.

    Page order is preserved so record order (and therefore locators like
    ``source.json#position!R14``) is stable and the assembled document —
    serialized with sorted keys — hashes deterministically for batch reuse.
    """
    entities: dict[str, list[dict[str, Any]]] = {}
    reference: dict[str, list[dict[str, Any]]] = {}
    for page_number in range(1, int(manifest["pages"]) + 1):
        _, stream = storage.read(_page_location(slug, push_batch_id, page_number))
        page = json.loads(stream.read().decode("utf-8"))
        for key, rows in page.get("entities", {}).items():
            entities.setdefault(key, []).extend(rows)
        for key, rows in page.get("reference", {}).items():
            reference.setdefault(key, []).extend(rows)
    return {
        "as_of_date": manifest["as_of_date"],
        "entities": entities,
        "reference": reference,
    }


def _ensure_active_mapping(db: Session, ctx: TenantContext, bank: Bank) -> None:
    """Provision the identity mapping when the bank has no API_PUSH mapping.

    Conformant clients therefore need zero onboarding configuration; banks
    whose middleware sends foreign field names activate their own
    ``API_PUSH`` mapping config, which then takes precedence.
    """
    active = db.scalar(
        select(MappingConfigRecord).where(
            MappingConfigRecord.organization_id == ctx.organization_id,
            MappingConfigRecord.bank_id == bank.id,
            MappingConfigRecord.source_system == "API_PUSH",
            MappingConfigRecord.status == "active",
        )
    )
    if active is not None:
        return
    create_mapping_config(
        db,
        ctx,
        bank.id,
        MappingConfigCreate(
            source_system="API_PUSH",
            name=IDENTITY_MAPPING_NAME,
            config=identity_mapping_config(),
            activate=True,
            reason=(
                "Auto-provisioned on first push commit: push payloads use "
                "canonical field names (identity mapping)."
            ),
        ),
    )


def _status_read(manifest: dict[str, Any]) -> PushBatchStatusRead:
    records_staged = {key: int(count) for key, count in manifest["records_staged"].items()}
    return PushBatchStatusRead(
        push_batch_id=UUID(manifest["push_batch_id"]),
        bank_id=UUID(manifest["bank_id"]),
        as_of_date=date.fromisoformat(manifest["as_of_date"]),
        idempotency_key=manifest["idempotency_key"],
        status=manifest["status"],
        pages_staged=int(manifest["pages"]),
        records_staged=records_staged,
        total_records_staged=sum(records_staged.values()),
        committed_batch_id=(
            UUID(manifest["committed_batch_id"]) if manifest["committed_batch_id"] else None
        ),
        expires_note=EXPIRES_NOTE,
    )


def _find_by_idempotency_key(
    ctx: TenantContext,
    bank: Bank,
    slug: str,
    idempotency_key: str,
    storage: StorageClient,
) -> dict[str, Any] | None:
    index = _read_json(storage, _index_location(slug, idempotency_key))
    if index is None:
        return None
    manifest = _read_json(storage, _manifest_location(slug, UUID(index["push_batch_id"])))
    if manifest is None or not _manifest_belongs_to(manifest, ctx, bank):
        return None
    return manifest


def _get_manifest_or_404(
    ctx: TenantContext,
    bank: Bank,
    slug: str,
    push_batch_id: UUID,
    storage: StorageClient,
) -> dict[str, Any]:
    manifest = _read_json(storage, _manifest_location(slug, push_batch_id))
    if manifest is None or not _manifest_belongs_to(manifest, ctx, bank):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Push batch not found.")
    return manifest


def _manifest_belongs_to(manifest: dict[str, Any], ctx: TenantContext, bank: Bank) -> bool:
    return manifest.get("organization_id") == str(ctx.organization_id) and manifest.get(
        "bank_id"
    ) == str(bank.id)


def _manifest_location(slug: str, push_batch_id: UUID) -> StorageLocation:
    return StorageLocation(slug, "temp", f"push/{push_batch_id}/manifest.json")


def _page_location(slug: str, push_batch_id: UUID, page_number: int) -> StorageLocation:
    return StorageLocation(slug, "temp", f"push/{push_batch_id}/pages/{page_number:05d}.json")


def _source_location(slug: str, push_batch_id: UUID) -> StorageLocation:
    return StorageLocation(slug, "temp", f"push/{push_batch_id}/source.json")


def _index_location(slug: str, idempotency_key: str) -> StorageLocation:
    digest = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()
    return StorageLocation(slug, "temp", f"push/index/{digest}.json")


def _write_json(  # noqa: PLR0913 - mirrors record_event's shape
    storage: StorageClient,
    ctx: TenantContext,
    slug: str,
    location: StorageLocation,
    payload: dict[str, Any],
    *,
    sort_keys: bool = False,
) -> None:
    content = json.dumps(payload, sort_keys=sort_keys).encode("utf-8")
    storage.write(
        location,
        io.BytesIO(content),
        ObjectMetadata(
            institution_slug=slug,
            tier="temp",
            checksum_sha256=hashlib.sha256(content).hexdigest(),
            written_at=utc_now(),
            written_by=str(ctx.actor_user_id),
            source_reference=location.object_path.rsplit("/", 1)[-1],
        ),
        content_type="application/json",
    )


def _read_json(storage: StorageClient, location: StorageLocation) -> dict[str, Any] | None:
    try:
        _, stream = storage.read(location)
    except StorageNotFoundError:
        return None
    return json.loads(stream.read().decode("utf-8"))


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
