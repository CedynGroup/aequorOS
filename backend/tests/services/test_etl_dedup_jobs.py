"""Out-of-band ML-ETL dedup jobs: the handler backfills a deferred batch's
etl_report with linkage/anomaly metadata, is idempotent, is enqueued only when
inline dedup was skipped, and never touches canonical rows.

Tests call the handler directly (never the poll loop), mirroring the other job
suites. Ingestion runs against the in-memory storage client and the handler is
pointed at the same instance so the persisted raw artifact is re-extractable.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from openpyxl import Workbook
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import TenantContext
from app.domain.ingestion.contracts import EntityMapping, MappingConfig
from app.models import (
    AuditEvent,
    Bank,
    CanonicalCounterparty,
    CanonicalPositionSnapshot,
    IngestionBatch,
    Job,
    LineageRecord,
)
from app.schemas.ingestion import IngestionBatchCreate, MappingConfigCreate
from app.services import etl_dedup_jobs, ingestion
from tests.api.helpers import ORG_1, USER_1
from tests.storage.inmemory import InMemoryStorageClient

AS_OF = date(2026, 6, 30)

# A counterparty pair whose shared national id + near-identical name yields one
# CROSS_SOURCE linkage, plus a duplicated loan row the fingerprint detector flags
# as an anomaly — so the dedup pass has real linkage AND anomaly output.
MAPPING = MappingConfig(
    field_mappings={
        "counterparty": EntityMapping(
            source_table="Customers",
            fields={
                "source_reference": "CustomerId",
                "name": "CustomerName",
                "counterparty_type": "Segment",
                "country_code": "Country",
            },
        ),
        "position": EntityMapping(
            source_table="Loans",
            fields={
                "source_reference": "AccountRef",
                "position_type": "Type",
                "currency": "Ccy",
                "balance": "Outstanding",
                "counterparty_reference": "Customer",
                "contractual_maturity": "Maturity",
            },
        ),
    },
    enum_mappings={"counterparty_type": {"RETAIL": "RETAIL_INDIVIDUAL", "CORP": "CORPORATE"}},
)


def _ctx() -> TenantContext:
    return TenantContext(organization_id=ORG_1, actor_user_id=USER_1)


def _bank(db_session: Session) -> Bank:
    bank = Bank(
        organization_id=ORG_1,
        name="ETL Dedup Bank",
        short_name="etl-dedup",
        currency="GHS",
        jurisdiction_code="GH",
        license_type="universal",
    )
    db_session.add(bank)
    db_session.flush()
    return bank


def _mapping_id(db_session: Session, bank: Bank) -> str:
    created = ingestion.create_mapping_config(
        db_session,
        _ctx(),
        bank.id,
        MappingConfigCreate(
            source_system="EXCEL_CSV",
            name="Dedup test mapping",
            config=MAPPING,
            activate=True,
            reason="etl dedup job tests",
        ),
    )
    return str(created.id)


def _workbook(path: Path) -> Path:
    workbook = Workbook()
    customers = workbook.active
    assert customers is not None
    customers.title = "Customers"
    customers.append(["CustomerId", "CustomerName", "Segment", "Country", "NationalId"])
    customers.append(["C-001", "ACME TRADING LTD", "CORP", "GH", "GHA-000111"])
    customers.append(["C-002", "Acme Trading Limited", "CORP", "GH", "GHA-000111"])
    customers.append(["C-003", "Kwame Mensah", "RETAIL", "GH", "GHA-999"])
    loans = workbook.create_sheet("Loans")
    loans.append(["AccountRef", "Type", "Ccy", "Outstanding", "Customer", "Maturity"])
    loans.append(["LN-0001", "LOAN", "GHS", 1000, "C-001", date(2031, 3, 15)])
    loans.append(["LN-0001", "LOAN", "GHS", 1000, "C-001", date(2031, 3, 15)])
    workbook.save(path)
    return path


def _ingest(
    db_session: Session, storage: InMemoryStorageClient, bank: Bank, location: Path
) -> IngestionBatch:
    result = ingestion.start_ingestion(
        db_session,
        _ctx(),
        bank.id,
        IngestionBatchCreate(
            source_system="EXCEL_CSV",
            as_of_date=AS_OF,
            location=str(location),
            mapping_config_id=None,
            reason="dedup job test ingestion",
        ),
        storage,
    )
    batch = db_session.get(IngestionBatch, result.batch.id)
    assert batch is not None
    return batch


def _dedup_job(db_session: Session, batch: IngestionBatch) -> Job:
    jobs = db_session.scalars(
        select(Job).where(
            Job.organization_id == ORG_1,
            Job.job_type == etl_dedup_jobs.ETL_DEDUP,
        )
    ).all()
    matches = [job for job in jobs if job.payload.get("batch_id") == str(batch.id)]
    assert matches, "expected an etl_dedup job to be enqueued"
    return matches[0]


def _force_defer(monkeypatch: pytest.MonkeyPatch, storage: InMemoryStorageClient) -> None:
    """Skip inline dedup for any batch, and point the handler at ``storage``."""
    monkeypatch.setattr(ingestion, "_ETL_INLINE_DEDUP_MAX_RECORDS", 0)
    monkeypatch.setattr(etl_dedup_jobs, "get_storage_client", lambda: storage)


def _dedup_lineage_nodes(db_session: Session, batch: IngestionBatch) -> list[LineageRecord]:
    """The out-of-band ML_ETL_DEDUP nodes (deferred_pass) this batch carries."""
    nodes = db_session.scalars(
        select(LineageRecord).where(
            LineageRecord.organization_id == ORG_1,
            LineageRecord.ingestion_batch_id == batch.id,
            LineageRecord.operation_type == "ML_ETL_DEDUP",
        )
    ).all()
    return [node for node in nodes if node.details.get("deferred_pass") is True]


def test_deferred_batch_enqueues_job_and_marks_report(
    db_session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    storage = InMemoryStorageClient()
    _force_defer(monkeypatch, storage)
    bank = _bank(db_session)
    _mapping_id(db_session, bank)

    batch = _ingest(db_session, storage, bank, _workbook(tmp_path / "book.xlsx"))

    assert batch.etl_report is not None
    assert batch.etl_report["dedup_status"] == "deferred"
    # No dedup/anomaly pass ran inline — the linkage keys are zeroed placeholders.
    assert batch.etl_report["linkage_count"] == 0
    assert batch.etl_report["sample_linkages"] == []
    # A job is queued to backfill it.
    job = _dedup_job(db_session, batch)
    assert job.payload["batch_id"] == str(batch.id)


def test_run_etl_dedup_backfills_report_lineage_and_audit(
    db_session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    storage = InMemoryStorageClient()
    _force_defer(monkeypatch, storage)
    bank = _bank(db_session)
    _mapping_id(db_session, bank)
    batch = _ingest(db_session, storage, bank, _workbook(tmp_path / "book.xlsx"))
    assert batch.etl_report is not None
    preprocess_keys = {
        key: batch.etl_report[key]
        for key in ("record_count", "operation_count", "sanctioned_count", "flagged_count")
    }
    job = _dedup_job(db_session, batch)

    etl_dedup_jobs.run_etl_dedup(db_session, job)

    db_session.refresh(batch)
    report = batch.etl_report
    assert report is not None
    # Dedup + anomaly metadata is now present.
    assert report["dedup_status"] == "completed"
    assert report["linkage_count"] == 1
    assert report["linkages_by_match_type"]["CROSS_SOURCE"] == 1
    assert report["anomaly_count"] == 2
    assert "sample_anomalies" in report
    # The inline preprocessing summary was merged into, not clobbered.
    for key, value in preprocess_keys.items():
        assert report[key] == value

    # An ML_ETL_DEDUP lineage node with the real counts was appended.
    nodes = _dedup_lineage_nodes(db_session, batch)
    assert len(nodes) == 1
    assert nodes[0].details["linkages"] == 1
    assert nodes[0].details["anomalies"] == 2
    assert nodes[0].details["content_hash_match"] is True

    # A dedup-completed audit event carries the merged report.
    event = db_session.scalar(
        select(AuditEvent).where(
            AuditEvent.organization_id == ORG_1,
            AuditEvent.event_type == "ml_etl.dedup_completed",
            AuditEvent.entity_id == batch.id,
        )
    )
    assert event is not None
    assert event.details["linkage_count"] == 1

    # The job records the real counts in its progress.
    assert job.progress["status"] == "completed"
    assert job.progress["linkage_count"] == 1
    assert job.progress["anomaly_count"] == 2


def test_run_etl_dedup_is_idempotent(
    db_session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    storage = InMemoryStorageClient()
    _force_defer(monkeypatch, storage)
    bank = _bank(db_session)
    _mapping_id(db_session, bank)
    batch = _ingest(db_session, storage, bank, _workbook(tmp_path / "book.xlsx"))
    job = _dedup_job(db_session, batch)

    etl_dedup_jobs.run_etl_dedup(db_session, job)
    etl_dedup_jobs.run_etl_dedup(db_session, job)  # re-run must not double-count or error

    db_session.refresh(batch)
    assert batch.etl_report is not None
    assert batch.etl_report["linkage_count"] == 1  # not doubled
    assert job.progress["status"] == "already_completed"
    # Exactly one out-of-band lineage node and one audit event, not two.
    assert len(_dedup_lineage_nodes(db_session, batch)) == 1
    events = db_session.scalars(
        select(AuditEvent).where(
            AuditEvent.organization_id == ORG_1,
            AuditEvent.event_type == "ml_etl.dedup_completed",
            AuditEvent.entity_id == batch.id,
        )
    ).all()
    assert len(events) == 1


def test_inline_dedup_does_not_enqueue_a_job(
    db_session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Default threshold: the 5-record batch is deduped inline, nothing deferred.
    storage = InMemoryStorageClient()
    monkeypatch.setattr(etl_dedup_jobs, "get_storage_client", lambda: storage)
    bank = _bank(db_session)
    _mapping_id(db_session, bank)

    batch = _ingest(db_session, storage, bank, _workbook(tmp_path / "book.xlsx"))

    assert batch.etl_report is not None
    assert batch.etl_report["dedup_status"] == "completed"
    assert batch.etl_report["linkage_count"] == 1  # inline pass produced it
    jobs = db_session.scalars(
        select(Job).where(Job.organization_id == ORG_1, Job.job_type == etl_dedup_jobs.ETL_DEDUP)
    ).all()
    assert jobs == []


def test_run_etl_dedup_never_mutates_canonical_rows(
    db_session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    storage = InMemoryStorageClient()
    _force_defer(monkeypatch, storage)
    bank = _bank(db_session)
    _mapping_id(db_session, bank)
    batch = _ingest(db_session, storage, bank, _workbook(tmp_path / "book.xlsx"))

    def _canonical_fingerprint() -> tuple[set[tuple[str, str]], set[tuple[str, str]]]:
        counterparties = {
            (str(cp.id), cp.name)
            for cp in db_session.scalars(
                select(CanonicalCounterparty).where(
                    CanonicalCounterparty.organization_id == ORG_1,
                    CanonicalCounterparty.bank_id == bank.id,
                    CanonicalCounterparty.superseded_by.is_(None),
                )
            )
        }
        snapshots = {
            (str(s.id), str(s.balance))
            for s in db_session.scalars(
                select(CanonicalPositionSnapshot).where(
                    CanonicalPositionSnapshot.organization_id == ORG_1,
                    CanonicalPositionSnapshot.bank_id == bank.id,
                    CanonicalPositionSnapshot.superseded_by.is_(None),
                )
            )
        }
        return counterparties, snapshots

    before = _canonical_fingerprint()
    assert before[0]  # the batch really did land canonical rows

    etl_dedup_jobs.run_etl_dedup(db_session, _dedup_job(db_session, batch))

    assert _canonical_fingerprint() == before
