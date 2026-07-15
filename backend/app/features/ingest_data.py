from __future__ import annotations

from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status

from app.api.deps import DbSession, MutationTenant, Tenant
from app.schemas.ingestion import (
    CanonicalPositionListRead,
    IngestionBatchCreate,
    IngestionBatchListRead,
    IngestionBatchRead,
    IngestionBatchStartRead,
    IngestionUploadRead,
    LineageWalkRead,
    MappingConfigCreate,
    MappingConfigListRead,
    MappingConfigRead,
    PositionSnapshotOverrideCreate,
    PositionSnapshotRead,
    TranslationFailureListRead,
)
from app.services import ingestion
from app.storage.client import StorageClient, StorageValidationError
from app.storage.config import StorageRetiredError
from app.storage.factory import get_storage_client

router = APIRouter(tags=["ingestion"])

MAX_UPLOAD_BYTES = 50_000_000


def get_ingestion_storage() -> StorageClient:
    """The storage engine, or 503 when it is unconfigured or retired."""
    try:
        return get_storage_client()
    except (StorageValidationError, StorageRetiredError, NotImplementedError) as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Storage is unavailable: {exc}",
        ) from exc


IngestionStorage = Annotated[StorageClient, Depends(get_ingestion_storage)]


@router.post(
    "/banks/{bank_id}/mapping-configs",
    response_model=MappingConfigRead,
    operation_id="createMappingConfig",
)
def create_mapping_config(
    bank_id: UUID, payload: MappingConfigCreate, db: DbSession, ctx: MutationTenant
) -> MappingConfigRead:
    return ingestion.create_mapping_config(db, ctx, bank_id, payload)


@router.get(
    "/banks/{bank_id}/mapping-configs",
    response_model=MappingConfigListRead,
    operation_id="listMappingConfigs",
)
def list_mapping_configs(bank_id: UUID, db: DbSession, ctx: Tenant) -> MappingConfigListRead:
    return ingestion.list_mapping_configs(db, ctx, bank_id)


@router.post(
    "/banks/{bank_id}/ingestion-uploads",
    response_model=IngestionUploadRead,
    status_code=201,
    operation_id="uploadIngestionSource",
)
async def upload_ingestion_source(
    bank_id: UUID,
    db: DbSession,
    ctx: MutationTenant,
    storage: IngestionStorage,
    file: UploadFile,
) -> IngestionUploadRead:
    content = await file.read()
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=f"Upload exceeds the {MAX_UPLOAD_BYTES // 1_000_000} MB limit.",
        )
    if not content:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Uploaded file is empty.",
        )
    return ingestion.upload_source(db, ctx, bank_id, storage, file.filename or "upload", content)


@router.post(
    "/banks/{bank_id}/ingestion-batches",
    response_model=IngestionBatchStartRead,
    status_code=201,
    operation_id="startIngestionBatch",
)
def start_ingestion_batch(
    bank_id: UUID,
    payload: IngestionBatchCreate,
    db: DbSession,
    ctx: MutationTenant,
    storage: IngestionStorage,
) -> IngestionBatchStartRead:
    return ingestion.start_ingestion(db, ctx, bank_id, payload, storage)


@router.get(
    "/banks/{bank_id}/ingestion-batches",
    response_model=IngestionBatchListRead,
    operation_id="listIngestionBatches",
)
def list_ingestion_batches(bank_id: UUID, db: DbSession, ctx: Tenant) -> IngestionBatchListRead:
    return ingestion.list_batches(db, ctx, bank_id)


@router.get(
    "/banks/{bank_id}/ingestion-batches/{batch_id}",
    response_model=IngestionBatchRead,
    operation_id="getIngestionBatch",
)
def get_ingestion_batch(
    bank_id: UUID, batch_id: UUID, db: DbSession, ctx: Tenant
) -> IngestionBatchRead:
    return ingestion.get_batch(db, ctx, bank_id, batch_id)


@router.get(
    "/banks/{bank_id}/ingestion-batches/{batch_id}/translation-failures",
    response_model=TranslationFailureListRead,
    operation_id="listTranslationFailures",
)
def list_translation_failures(
    bank_id: UUID, batch_id: UUID, db: DbSession, ctx: Tenant
) -> TranslationFailureListRead:
    return ingestion.list_translation_failures(db, ctx, bank_id, batch_id)


@router.get(
    "/banks/{bank_id}/canonical-positions",
    response_model=CanonicalPositionListRead,
    operation_id="listCanonicalPositions",
)
def list_canonical_positions(
    bank_id: UUID,
    db: DbSession,
    ctx: Tenant,
    as_of_date: date | None = None,
) -> CanonicalPositionListRead:
    return ingestion.list_positions(db, ctx, bank_id, as_of_date)


@router.post(
    "/banks/{bank_id}/position-snapshots/{snapshot_id}/override",
    response_model=PositionSnapshotRead,
    operation_id="overridePositionSnapshot",
)
def override_position_snapshot(
    bank_id: UUID,
    snapshot_id: UUID,
    payload: PositionSnapshotOverrideCreate,
    db: DbSession,
    ctx: MutationTenant,
) -> PositionSnapshotRead:
    return ingestion.override_position_snapshot(db, ctx, bank_id, snapshot_id, payload)


@router.get(
    "/lineage/{lineage_id}",
    response_model=LineageWalkRead,
    operation_id="walkLineage",
)
def walk_lineage(lineage_id: UUID, db: DbSession, ctx: Tenant) -> LineageWalkRead:
    return ingestion.walk_lineage(db, ctx, lineage_id)
