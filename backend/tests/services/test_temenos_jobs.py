"""Temenos pull jobs: handler execution over a fixture transport, the retry vs
park error policy, and tick scheduling gated on TEMENOS_PULL_ENABLED."""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters.temenos_t24.credential_vault import TemenosCredentialVault
from app.adapters.temenos_t24.errors import TemenosError
from app.adapters.temenos_t24.transport import FixtureTransport
from app.api.deps import TenantContext
from app.core.config import get_settings
from app.models import Bank, CanonicalPosition, Job
from app.models.temenos import TemenosConnection
from app.services import job_queue, temenos_jobs
from tests.api.helpers import ORG_1, USER_1
from tests.storage.inmemory import InMemoryStorageClient

OFS_FIXTURES = Path(__file__).resolve().parents[1] / "adapters/temenos_t24/ofs/fixtures"
MASTER_KEY = "temenos-jobs-test-key"
AS_OF = date(2026, 6, 30)
NOW = datetime(2026, 6, 30, 18, 0, tzinfo=UTC)
CREDS = {"username": "SVC.AEQUOROS", "password": "must-never-leak"}


def _bank(db_session: Session) -> Bank:
    bank = Bank(
        organization_id=ORG_1,
        name="T24 Jobs Bank",
        short_name="t24-jobs",
        currency="GHS",
        jurisdiction_code="GH",
        license_type="universal",
    )
    db_session.add(bank)
    db_session.flush()
    return bank


def _connection(db_session: Session, bank: Bank, *, status: str = "ACTIVE") -> TemenosConnection:
    connection = TemenosConnection(
        organization_id=ORG_1,
        bank_id=bank.id,
        connection_mode="OFS",
        display_name="Core OFS",
        endpoint="ofs://sample-bank",
        status=status,
        vault_path="",
        companies=["GH0010001"],
        domains=[],  # empty → pull every supported domain
        schedule={},
        catalog_overrides={},
        created_by=USER_1,
    )
    db_session.add(connection)
    db_session.flush()
    return connection


def _store_creds(
    db_session: Session, connection: TemenosConnection, mp: pytest.MonkeyPatch
) -> None:
    mp.setenv("CREDENTIAL_VAULT_MASTER_KEY", MASTER_KEY)
    get_settings.cache_clear()
    TemenosCredentialVault(db_session, master_key=MASTER_KEY).store(connection, credentials=CREDS)


def _pull_job(db_session: Session, connection: TemenosConnection) -> Job:
    job = job_queue.enqueue(
        db_session,
        ORG_1,
        "temenos_pull",
        bank_id=connection.bank_id,
        payload={"connection_id": str(connection.id), "as_of_date": AS_OF.isoformat()},
    )
    db_session.commit()
    return job


def _use_fixture_transport(mp: pytest.MonkeyPatch) -> InMemoryStorageClient:
    storage = InMemoryStorageClient()

    def _storage() -> InMemoryStorageClient:
        return storage

    def _transport(_connection: TemenosConnection) -> FixtureTransport:
        return FixtureTransport(OFS_FIXTURES)

    mp.setattr(temenos_jobs, "get_storage_client", _storage)
    mp.setattr(temenos_jobs, "resolve_transport", _transport)
    return storage


def test_run_temenos_pull_ingests_over_fixture_transport(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    bank = _bank(db_session)
    connection = _connection(db_session, bank)
    _store_creds(db_session, connection, monkeypatch)
    _use_fixture_transport(monkeypatch)
    job = _pull_job(db_session, connection)

    temenos_jobs.run_temenos_pull(db_session, job)

    assert connection.last_pull_status == "succeeded"
    assert job.progress["batch_status"] in ("accepted", "accepted_with_warnings")
    positions = db_session.scalars(
        select(CanonicalPosition).where(
            CanonicalPosition.organization_id == ORG_1,
            CanonicalPosition.bank_id == bank.id,
            CanonicalPosition.superseded_by.is_(None),
        )
    ).all()
    assert positions  # the T24 book landed


def test_run_temenos_pull_retries_when_core_unavailable(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    # No transport override → the portal-gated live transport raises
    # CORE_UNAVAILABLE, which is retryable, so the handler re-raises.
    bank = _bank(db_session)
    connection = _connection(db_session, bank)
    _store_creds(db_session, connection, monkeypatch)
    monkeypatch.setattr(temenos_jobs, "get_storage_client", InMemoryStorageClient)
    job = _pull_job(db_session, connection)

    with pytest.raises(TemenosError):
        temenos_jobs.run_temenos_pull(db_session, job)
    assert connection.last_pull_status == "failed"


def test_run_temenos_pull_without_credentials_does_not_retry(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CREDENTIAL_VAULT_MASTER_KEY", MASTER_KEY)
    get_settings.cache_clear()
    bank = _bank(db_session)
    connection = _connection(db_session, bank)  # no stored credentials
    _use_fixture_transport(monkeypatch)
    job = _pull_job(db_session, connection)

    temenos_jobs.run_temenos_pull(db_session, job)  # must NOT raise
    assert job.progress["status"] == "failed_no_retry"


def test_enqueue_due_is_inert_when_pull_disabled(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("TEMENOS_PULL_ENABLED", raising=False)
    get_settings.cache_clear()
    bank = _bank(db_session)
    _connection(db_session, bank)
    assert temenos_jobs.enqueue_due_temenos_pulls(db_session, ORG_1, now=NOW) == []


def test_enqueue_due_enqueues_a_due_connection_when_enabled(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TEMENOS_PULL_ENABLED", "true")
    get_settings.cache_clear()
    bank = _bank(db_session)
    connection = _connection(db_session, bank)  # ACTIVE, never pulled → due
    enqueued = temenos_jobs.enqueue_due_temenos_pulls(db_session, ORG_1, now=NOW)
    assert len(enqueued) == 1
    assert enqueued[0].payload["connection_id"] == str(connection.id)


def test_enqueue_due_skips_recently_pulled_connection(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TEMENOS_PULL_ENABLED", "true")
    get_settings.cache_clear()
    bank = _bank(db_session)
    connection = _connection(db_session, bank)
    connection.last_pull_at = NOW  # pulled this EOD window already
    db_session.flush()
    assert temenos_jobs.enqueue_due_temenos_pulls(db_session, ORG_1, now=NOW) == []


def test_backfill_enqueues_one_job_per_date(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    bank = _bank(db_session)
    connection = _connection(db_session, bank)
    ctx = TenantContext(organization_id=ORG_1, actor_user_id=USER_1)
    jobs = temenos_jobs.enqueue_backfill(
        db_session, ctx, connection, date(2026, 6, 28), date(2026, 6, 30)
    )
    assert len(jobs) == 3
    dates = sorted(j.payload["as_of_date"] for j in jobs)
    assert dates == ["2026-06-28", "2026-06-29", "2026-06-30"]
