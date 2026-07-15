"""Push API: institutions' middleware POSTs data as JSON instead of files.

Three-call flow — open a push batch (idempotency key), stage record pages,
commit. Commit runs the exact same ingestion pipeline as a file upload and
returns the same batch + validation report shape, so downstream tooling does
not care how the data arrived. Public contract: docs/API_INTEGRATION.md.

Auth (MVP): the same tenant headers as the rest of the API (X-Org-Id /
X-User-Id). Production integration adds OAuth2 client-credentials / mTLS in
front of these endpoints; the resource design does not change.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter

from app.api.deps import DbSession, MutationTenant, Tenant
from app.features.ingest_data import IngestionStorage
from app.schemas.ingestion import IngestionBatchStartRead
from app.schemas.push import PushBatchOpen, PushBatchStatusRead, PushRecordsPage
from app.services import push_ingestion

router = APIRouter(tags=["ingestion"])


@router.post(
    "/banks/{bank_id}/push-batches",
    response_model=PushBatchStatusRead,
    status_code=201,
    operation_id="openPushBatch",
)
def open_push_batch(
    bank_id: UUID,
    payload: PushBatchOpen,
    db: DbSession,
    ctx: MutationTenant,
    storage: IngestionStorage,
) -> PushBatchStatusRead:
    return push_ingestion.open_push_batch(db, ctx, bank_id, payload, storage)


@router.post(
    "/banks/{bank_id}/push-batches/{push_batch_id}/records",
    response_model=PushBatchStatusRead,
    operation_id="stagePushBatchRecords",
)
def stage_push_batch_records(  # noqa: PLR0913 - mirrors the other ingestion routes' shape
    bank_id: UUID,
    push_batch_id: UUID,
    payload: PushRecordsPage,
    db: DbSession,
    ctx: MutationTenant,
    storage: IngestionStorage,
) -> PushBatchStatusRead:
    return push_ingestion.stage_push_records(db, ctx, bank_id, push_batch_id, payload, storage)


@router.post(
    "/banks/{bank_id}/push-batches/{push_batch_id}/commit",
    response_model=IngestionBatchStartRead,
    status_code=201,
    operation_id="commitPushBatch",
)
def commit_push_batch(
    bank_id: UUID,
    push_batch_id: UUID,
    db: DbSession,
    ctx: MutationTenant,
    storage: IngestionStorage,
) -> IngestionBatchStartRead:
    return push_ingestion.commit_push_batch(db, ctx, bank_id, push_batch_id, storage)


@router.get(
    "/banks/{bank_id}/push-batches/{push_batch_id}",
    response_model=PushBatchStatusRead,
    operation_id="getPushBatch",
)
def get_push_batch(
    bank_id: UUID,
    push_batch_id: UUID,
    db: DbSession,
    ctx: Tenant,
    storage: IngestionStorage,
) -> PushBatchStatusRead:
    return push_ingestion.get_push_batch(db, ctx, bank_id, push_batch_id, storage)
