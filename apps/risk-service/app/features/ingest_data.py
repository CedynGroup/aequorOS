from __future__ import annotations

from datetime import date
from uuid import UUID

from fastapi import APIRouter

from app.api.deps import DbSession, MutationTenant, Tenant
from app.schemas.ingestion import (
    CanonicalPositionListRead,
    IngestionBatchCreate,
    IngestionBatchListRead,
    IngestionBatchRead,
    IngestionBatchStartRead,
    LineageWalkRead,
    MappingConfigCreate,
    MappingConfigListRead,
    MappingConfigRead,
    PositionSnapshotOverrideCreate,
    PositionSnapshotRead,
    TranslationFailureListRead,
)
from app.services import ingestion

router = APIRouter(tags=["ingestion"])


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
    "/banks/{bank_id}/ingestion-batches",
    response_model=IngestionBatchStartRead,
    status_code=201,
    operation_id="startIngestionBatch",
)
def start_ingestion_batch(
    bank_id: UUID, payload: IngestionBatchCreate, db: DbSession, ctx: MutationTenant
) -> IngestionBatchStartRead:
    return ingestion.start_ingestion(db, ctx, bank_id, payload)


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
