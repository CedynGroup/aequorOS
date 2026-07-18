"""Out-of-band ML-ETL deduplication pass for large ingestion batches.

The inline ingestion pipeline (``app.services.ingestion``) skips the pairwise /
model-scored dedup + anomaly passes above ``_ETL_INLINE_DEDUP_MAX_RECORDS`` so a
core-banking-scale sync (100k+ rows) is not blocked for tens of minutes. Those
passes emit linkage / anomaly METADATA only — they never change which canonical
records persist — so the sole thing lost by skipping them inline is the
entity-resolution info in ``batch.etl_report`` and the dedup lineage / audit
detail. This module re-derives exactly that, out of band.

:func:`run_etl_dedup` is the ``etl_dedup`` worker handler. It reloads the target
batch (org-scoped), re-extracts the source the same way ingestion does — reading
the persisted raw artifact through the batch's stored mapping — re-runs the pure
:func:`app.etl.run_etl` pass with dedup + anomaly enabled, and MERGES the results
into ``batch.etl_report`` (never clobbering the inline preprocessing summary),
appending an ``ML_ETL_DEDUP`` lineage node and an ``ml_etl.dedup_completed``
audit event with the real counts. It is idempotent (guarded on the report's
``dedup_status`` marker) and never mutates canonical records.
"""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

import app.adapters  # noqa: F401 - importing registers every shipped source adapter
from app.api.deps import TenantContext
from app.domain.ingestion.adapter import get_adapter_class
from app.domain.ingestion.contracts import ENTITY_TYPES, MappingConfig
from app.etl import EtlConfig, ETLResult, etl_summary, run_etl
from app.etl.contracts import ETLOperationType
from app.models import Bank, IngestionBatch, Job, LineageRecord, MappingConfigRecord
from app.services.audit import record_event
from app.services.ingestion import bank_slug, build_adapter_config
from app.storage.client import StorageLocation
from app.storage.factory import get_storage_client

logger = logging.getLogger(__name__)

ETL_DEDUP = "etl_dedup"

_SAMPLE_LIMIT = 5


class EtlDedupJobError(Exception):
    """An etl_dedup job could not run (missing batch, mapping, or artifact)."""


def run_etl_dedup(session: Session, job: Job) -> None:
    """Worker handler: run the deferred ML-ETL dedup pass for one batch.

    Payload: ``{"batch_id": ...}``. Idempotent — a batch whose report already
    reads ``dedup_status == "completed"`` is a no-op, so a retry or a coalesced
    re-enqueue never double-counts linkages or duplicates lineage / audit.
    """
    batch = _batch_or_error(session, job)
    if (batch.etl_report or {}).get("dedup_status") == "completed":
        job.progress = {"batch_id": str(batch.id), "status": "already_completed"}
        return

    bank = _bank_or_error(session, batch)
    mapping = _mapping_or_error(session, batch)
    ctx = TenantContext(organization_id=batch.organization_id, actor_user_id=batch.created_by)

    extraction = _reextract(session, batch, bank, mapping)
    result = run_etl(
        extraction,
        mapping,
        config=EtlConfig(deduplicate=True, detect_anomalies=True),
    )

    overlay = _dedup_anomaly_overlay(result)
    batch.etl_report = {**(batch.etl_report or {}), **overlay}

    anomaly_count = overlay["anomaly_count"]
    _record_dedup_lineage(
        session,
        ctx,
        batch,
        linkages=overlay["linkage_count"],
        anomalies=anomaly_count,
        content_hash_match=extraction.content_hash == batch.content_hash,
    )
    record_event(
        session,
        ctx,
        event_type="ml_etl.dedup_completed",
        entity_type="ingestion_batch",
        entity_id=batch.id,
        details=batch.etl_report,
    )
    session.commit()
    job.progress = {
        "batch_id": str(batch.id),
        "status": "completed",
        "linkage_count": overlay["linkage_count"],
        "anomaly_count": anomaly_count,
        "records_extracted": len(extraction.records),
    }


def _dedup_anomaly_overlay(result: ETLResult) -> dict[str, Any]:
    """The dedup + anomaly keys to merge into a deferred ``etl_report``.

    Only the entity-resolution / anomaly-derived keys are returned, so merging
    leaves the inline preprocessing summary (record / operation / flag counts,
    sample operations, sample preprocess flags) untouched. The linkage keys
    overwrite the zeroed placeholders the deferred report carried; anomaly counts
    are surfaced under their own keys — the inline pass ran no anomaly detector,
    so its ``flagged_count`` / ``sample_flags`` stay preprocessing-only.
    """
    summary = etl_summary(result, sample_limit=_SAMPLE_LIMIT)
    anomalies = [
        op
        for op in result.operations
        if op.provenance.operation_type is ETLOperationType.ANOMALY_FLAG
    ]
    return {
        "linkage_count": summary["linkage_count"],
        "linkages_by_match_type": summary["linkages_by_match_type"],
        "auto_confirmed_linkages": summary["auto_confirmed_linkages"],
        "sample_linkages": summary["sample_linkages"],
        "anomaly_count": len(anomalies),
        "sample_anomalies": [
            {
                "record_id": op.record_id,
                "field": op.field_name,
                "reason": op.reason,
                "confidence": op.provenance.confidence,
            }
            for op in anomalies[:_SAMPLE_LIMIT]
        ],
        "dedup_status": "completed",
    }


def _reextract(
    session: Session, batch: IngestionBatch, bank: Bank, mapping: MappingConfig
):
    """Reconstruct the batch's extraction from its persisted raw artifact.

    Mirrors ingestion's extract path: materialize the untouched source file from
    the ``raw`` tier into a scratch dir (its original name preserved so the
    adapter recognizes the format), then run the same adapter over the mapping's
    table-resolution config. The original ``adapter_options`` are not persisted
    on the batch, so they are not reconstructed — they tune extraction, not the
    canonical records, and the content-hash match recorded in lineage flags any
    divergence.
    """
    if not batch.raw_artifact_path:
        msg = f"Batch {batch.id} has no raw artifact to re-extract for ML-ETL dedup."
        raise EtlDedupJobError(msg)
    storage = get_storage_client()
    slug = bank_slug(session, bank)
    _, stream = storage.read(
        StorageLocation(institution_slug=slug, tier="raw", object_path=batch.raw_artifact_path)
    )
    scratch = Path(tempfile.mkdtemp(prefix="aequoros-etl-dedup-"))
    local = scratch / (Path(batch.raw_artifact_path).name or "source")
    local.write_bytes(stream.read())

    adapter = get_adapter_class(batch.source_system)()
    adapter_config = build_adapter_config(str(local), mapping)
    return adapter.extract(adapter_config, batch.as_of_date, list(ENTITY_TYPES))


def _record_dedup_lineage(  # noqa: PLR0913 - mirrors the inline lineage node's shape
    session: Session,
    ctx: TenantContext,
    batch: IngestionBatch,
    *,
    linkages: int,
    anomalies: int,
    content_hash_match: bool,
) -> None:
    """Append an ML_ETL_DEDUP lineage node for the out-of-band pass.

    Chained onto the batch's inline ML_ETL_PREPROCESS node when present so the
    node sits in the same extract → preprocess → dedup graph the inline pass
    builds; the canonical records keep their VALIDATION lineage id either way.
    """
    preprocess_id = session.scalar(
        select(LineageRecord.id).where(
            LineageRecord.organization_id == ctx.organization_id,
            LineageRecord.ingestion_batch_id == batch.id,
            LineageRecord.operation_type == "ML_ETL_PREPROCESS",
        )
    )
    node = LineageRecord(
        organization_id=ctx.organization_id,
        ingestion_batch_id=batch.id,
        operation_type="ML_ETL_DEDUP",
        operation_ref="ml_etl/dedup",
        input_lineage_ids=[str(preprocess_id)] if preprocess_id is not None else [],
        details={
            "linkages": linkages,
            "anomalies": anomalies,
            "deferred_pass": True,
            "content_hash_match": content_hash_match,
        },
    )
    session.add(node)
    session.flush()


def _batch_or_error(session: Session, job: Job) -> IngestionBatch:
    raw_id = job.payload.get("batch_id")
    if not raw_id:
        msg = f"Job {job.id} payload carries no batch_id."
        raise EtlDedupJobError(msg)
    batch = session.scalar(
        select(IngestionBatch).where(
            IngestionBatch.id == UUID(str(raw_id)),
            IngestionBatch.organization_id == job.organization_id,
        )
    )
    if batch is None:
        msg = f"Job {job.id} references unknown ingestion batch {raw_id}."
        raise EtlDedupJobError(msg)
    return batch


def _bank_or_error(session: Session, batch: IngestionBatch) -> Bank:
    bank = session.scalar(
        select(Bank).where(
            Bank.id == batch.bank_id, Bank.organization_id == batch.organization_id
        )
    )
    if bank is None:
        msg = f"Ingestion batch {batch.id} references unknown bank {batch.bank_id}."
        raise EtlDedupJobError(msg)
    return bank


def _mapping_or_error(session: Session, batch: IngestionBatch) -> MappingConfig:
    if batch.mapping_config_id is None:
        msg = f"Batch {batch.id} has no mapping config to reconstruct its extraction."
        raise EtlDedupJobError(msg)
    record = session.scalar(
        select(MappingConfigRecord).where(
            MappingConfigRecord.id == batch.mapping_config_id,
            MappingConfigRecord.organization_id == batch.organization_id,
        )
    )
    if record is None:
        msg = f"Batch {batch.id} references unknown mapping config {batch.mapping_config_id}."
        raise EtlDedupJobError(msg)
    return MappingConfig.model_validate(record.config)
