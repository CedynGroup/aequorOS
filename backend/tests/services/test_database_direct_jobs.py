"""Database-Direct health probes: daily scheduling, coalescing, and the handler.

The probe reuses the live connection test over the offline fixture driver, so
no live database is required. The load-bearing concerns: the scheduler stays
inert without ``DATABASE_DIRECT_HEALTH_ENABLED``, a probe is due at most once
per day per connection, and the tick actually reschedules itself when the probe
flag is the only one enabled (a probe flag outside the inert check would let
the tick chain die).
"""

from __future__ import annotations

from datetime import timedelta
from typing import Never

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.adapters.database_direct.config import ConnectionConfig
from app.adapters.database_direct.drivers.base import (
    ColumnSchema,
    DbCredentials,
    TableSchema,
)
from app.adapters.database_direct.errors import (
    BankFacingError,
    DatabaseDirectError,
    DbDirectErrorCode,
)
from app.adapters.database_direct.fixtures import Dump, OfflineDumpDriver
from app.api.deps import TenantContext
from app.core.config import get_settings
from app.db.base import utc_now
from app.models import Bank, Job
from app.models.database_connection import DatabaseDirectConnection
from app.schemas.database_connection import DatabaseConnectionCreate
from app.services import database_connections, database_direct_jobs, job_queue, scheduler
from tests.api.helpers import ORG_1, USER_1
from tests.factories.outbound import stub_public_dns

MASTER_KEY = "db-direct-jobs-test-master-key"
CREDENTIALS = {"username": "AEQUOROS_RO", "password": "probe-password"}


@pytest.fixture
def vault_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CREDENTIAL_VAULT_MASTER_KEY", MASTER_KEY)
    get_settings.cache_clear()


@pytest.fixture
def health_enabled(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DATABASE_DIRECT_HEALTH_ENABLED", "true")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _resolvable_core(monkeypatch: pytest.MonkeyPatch) -> None:
    """A probe is a live connect, so the egress guard resolves the host: stub
    DNS so the suite stays offline and deterministic."""
    stub_public_dns(monkeypatch, "core-db.internal")


@pytest.fixture
def ctx() -> TenantContext:
    return TenantContext(organization_id=ORG_1, actor_user_id=USER_1)


def _bank(db: Session) -> Bank:
    bank = Bank(
        organization_id=ORG_1,
        name="Probe Bank",
        short_name="probe",
        currency="GHS",
        jurisdiction_code="GH",
        license_type="universal",
        institution_type="universal_bank",
    )
    db.add(bank)
    db.flush()
    return bank


def _connection(db: Session, ctx: TenantContext, bank: Bank) -> DatabaseDirectConnection:
    read = database_connections.create_connection(
        db,
        ctx,
        bank.id,
        DatabaseConnectionCreate.model_validate(
            {
                "backend": "sqlserver",
                "display_name": "Core SQL Server",
                "host": "core-db.internal",
                "port": 1433,
                "database": "COREBANK",
                "schemas": ["DBO"],
                "credentials": CREDENTIALS,
                "extraction_spec": {
                    "tables": [{"table": "DBO.GL_ACCOUNTS", "record_kind": "gl_account"}],
                    "default_mode": "full",
                },
            }
        ),
    )
    row = db.get(DatabaseDirectConnection, read.id)
    assert row is not None
    assert row.status == "ACTIVE"
    # Creation stamps last_validated_at (shape validation); age it past the
    # probe interval so tests exercise the due steady state.
    row.last_validated_at = utc_now() - timedelta(hours=21)
    db.flush()
    return row


def _offline_driver() -> OfflineDumpDriver:
    tables = (
        TableSchema(
            name="GL_ACCOUNTS",
            schema="DBO",
            columns=(
                ColumnSchema(name="ACCT_CODE", data_type="varchar", nullable=False),
                ColumnSchema(name="NAME", data_type="varchar"),
            ),
        ),
    )
    dump = Dump(
        database="COREBANK",
        tables=tables,
        rows={"DBO.GL_ACCOUNTS": [{"ACCT_CODE": "1000", "NAME": "Cash"}]},
    )
    return OfflineDumpDriver(dump, backend="sqlserver")


class _DownDriver(OfflineDumpDriver):
    """A driver whose connect always fails with a transient, bank-safe error."""

    def connect(self, connection: ConnectionConfig, credentials: DbCredentials) -> Never:
        raise DatabaseDirectError(
            BankFacingError(
                code=DbDirectErrorCode.CORE_UNAVAILABLE,
                message="The core reporting database is not reachable right now.",
                actions=(),
                severity="warning",
            ),
            internal_detail="fixture: connection refused",
        )


def _probe_job(db: Session, connection: DatabaseDirectConnection) -> Job:
    job = job_queue.enqueue(
        db,
        ORG_1,
        database_direct_jobs.DATABASE_DIRECT_HEALTH,
        bank_id=connection.bank_id,
        payload={"connection_id": str(connection.id)},
    )
    db.commit()
    return job


def _count(db: Session, job_type: str) -> int:
    return db.scalar(select(func.count()).select_from(Job).where(Job.job_type == job_type)) or 0


def test_enqueue_is_inert_when_disabled(
    db_session: Session, ctx: TenantContext, vault_key: None
) -> None:
    _connection(db_session, ctx, _bank(db_session))

    enqueued = database_direct_jobs.enqueue_due_database_direct_probes(db_session, ORG_1)

    assert enqueued == []
    assert _count(db_session, "database_direct_health") == 0


def test_enqueue_due_probe_coalesces_daily(
    db_session: Session, ctx: TenantContext, vault_key: None, health_enabled: None
) -> None:
    connection = _connection(db_session, ctx, _bank(db_session))

    first = database_direct_jobs.enqueue_due_database_direct_probes(db_session, ORG_1)
    again = database_direct_jobs.enqueue_due_database_direct_probes(db_session, ORG_1)

    # A stale connection is due; the same day's re-tick coalesces.
    assert len(first) == 1
    assert first[0].payload == {"connection_id": str(connection.id)}
    assert first[0].bank_id == connection.bank_id
    assert len(again) == 1
    assert _count(db_session, "database_direct_health") == 1


def test_enqueue_skips_recently_validated_and_non_probeable(
    db_session: Session, ctx: TenantContext, vault_key: None, health_enabled: None
) -> None:
    bank = _bank(db_session)
    fresh = _connection(db_session, ctx, bank)
    fresh.last_validated_at = utc_now()
    db_session.flush()

    assert database_direct_jobs.enqueue_due_database_direct_probes(db_session, ORG_1) == []

    fresh.last_validated_at = utc_now() - timedelta(hours=21)
    fresh.status = "REVOKED"
    db_session.flush()

    assert database_direct_jobs.enqueue_due_database_direct_probes(db_session, ORG_1) == []

    fresh.status = "ACTIVE"
    db_session.flush()

    assert len(database_direct_jobs.enqueue_due_database_direct_probes(db_session, ORG_1)) == 1


def test_run_probe_marks_connection_validated(
    db_session: Session,
    ctx: TenantContext,
    vault_key: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _connection(db_session, ctx, _bank(db_session))
    before = connection.last_validated_at
    assert before is not None
    monkeypatch.setattr(database_direct_jobs, "resolve_driver", lambda _: _offline_driver())
    job = _probe_job(db_session, connection)

    database_direct_jobs.run_database_direct_health(db_session, job)

    assert job.progress["reachable"] is True
    assert job.progress["latency_ms"] is not None
    after = connection.last_validated_at
    assert after is not None
    assert after.replace(tzinfo=None) > before.replace(tzinfo=None)


def test_run_probe_records_transient_failure_without_retry(
    db_session: Session,
    ctx: TenantContext,
    vault_key: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _connection(db_session, ctx, _bank(db_session))
    down = _DownDriver(Dump(database="COREBANK", tables=(), rows={}), backend="sqlserver")
    monkeypatch.setattr(database_direct_jobs, "resolve_driver", lambda _: down)
    job = _probe_job(db_session, connection)

    # Completes (no raise): worker backoff cannot wake a stopped core.
    database_direct_jobs.run_database_direct_health(db_session, job)

    assert job.progress["reachable"] is False
    assert job.progress["error_code"] == "CORE_UNAVAILABLE"
    # Transient codes never mutate the stored lifecycle status.
    assert connection.status == "ACTIVE"


def test_run_probe_skips_non_probeable_connection(
    db_session: Session, ctx: TenantContext, vault_key: None
) -> None:
    connection = _connection(db_session, ctx, _bank(db_session))
    connection.status = "DISABLED"
    db_session.flush()
    job = _probe_job(db_session, connection)

    database_direct_jobs.run_database_direct_health(db_session, job)

    assert job.progress["status"] == "skipped"


def test_run_tick_enqueues_probes_and_reschedules(
    db_session: Session, ctx: TenantContext, vault_key: None, health_enabled: None
) -> None:
    """The probe flag alone must keep the tick alive — an inert check missing
    the flag would strand the probe with no tick chain to enqueue it."""
    _connection(db_session, ctx, _bank(db_session))
    job_queue.enqueue(db_session, ORG_1, "scheduled_tick", payload={})
    db_session.commit()
    tick = job_queue.claim_next(db_session, utc_now(), ("scheduled_tick",))
    assert tick is not None

    scheduler.run_tick(db_session, tick)

    assert tick.progress["database_direct_probes_enqueued"] == 1
    assert _count(db_session, "database_direct_health") == 1
    queued_ticks = list(
        db_session.scalars(
            select(Job).where(Job.job_type == "scheduled_tick", Job.status == "queued")
        )
    )
    assert len(queued_ticks) == 1
