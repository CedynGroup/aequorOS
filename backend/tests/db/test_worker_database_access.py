"""Worker behavior and refusals through authenticated, non-bypass PostgreSQL roles."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable, Iterator, Mapping
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import cast
from uuid import uuid4

import jwt
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, rule
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from alembic import command
from app import worker
from app.bi.service import partitions, query_log
from app.bi.service.versions import BUILDER_VERSION
from app.core import audit_integrity, security
from app.core.config import get_operator_settings, get_settings
from app.core.key_management.local import LocalKeyProvider
from app.core.key_management.models import BankEncryptionKey
from app.core.key_management.registry import DatabaseEnvelopeStore
from app.core.key_management.settings import get_key_settings
from app.core.key_management.types import KeyReference, KeyUnavailableError
from app.db import session as database
from app.db.session import get_storage_session, worker_tenant_context
from app.identity.api import auth as auth_api
from app.identity.service import sso_config
from app.identity.public import Bank
from app.models import (
    AuditEvent,
    DeskDetermination,
    DeskMethodology,
    DeskObservation,
    DeskSourceCapture,
    Job,
    OperatorAuditLog,
    Organization,
    User,
)
from app.operator.deps import get_operator_sessionmaker, resolve_operator_database_url
from app.services import job_queue, scheduler
from app.services.market_desk import capture_job
from app.services.market_desk.sources import ParseResult, ingest_capture
from app.storage.client import StorageLocation
from app.storage.encryption import ObjectEncryption
from tests.db import test_database_roles as foundation
from tests.db import test_postgres_migrations as migrations
from tests.live_data import conftest as live_data
from tests.support.helpers import ORG_1, ORG_2
from tests.support.stateful import run_database_machine

administrator = foundation.administrator
role_banks = foundation.role_banks
role_engines = foundation.role_engines
role_schema = foundation.role_schema

pytestmark = pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="PostgreSQL required")
NOW = datetime(2026, 10, 12, tzinfo=UTC)


@pytest.fixture(autouse=True)
def empty_queue(administrator: Engine) -> None:
    with administrator.begin() as connection:
        connection.execute(text("DELETE FROM jobs"))


def scoped(engine: Engine, org: str, bank: str | None = None) -> Session:
    session = Session(engine, expire_on_commit=False)
    session.info.update(organization_id=org, bank_id=bank)
    return session


@pytest.fixture
def legacy_engines(
    administrator: Engine, monkeypatch: pytest.MonkeyPatch
) -> Iterator[dict[str, Engine]]:
    engines: dict[str, Engine] = {}
    names: list[str] = []
    monkeypatch.setenv("OPERATOR_DATABASE_URL", "")
    get_operator_settings.cache_clear()
    try:
        for purpose in ("preauth", "operator"):
            name, password = f"legacy_{purpose}_{uuid4().hex}", uuid4().hex
            with administrator.begin() as connection:
                connection.execute(
                    text(
                        f"CREATE ROLE {name} LOGIN PASSWORD '{password}' INHERIT "
                        "BYPASSRLS NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION"
                    )
                )
                names.append(name)
                connection.execute(text(f"GRANT aqs_api TO {name}"))
            engines[purpose] = create_engine(
                make_url(administrator.url).set(username=name, password=password)
            )
        yield engines
    finally:
        get_operator_settings.cache_clear()
        for engine in engines.values():
            for application in ("aequoros-preauth", "aequoros-operator"):
                database.get_engine(
                    engine.url.render_as_string(hide_password=False), application
                ).dispose()
            engine.dispose()
        with administrator.begin() as connection:
            for name in names:
                connection.execute(text(f"DROP ROLE {name}"))


def test_claim_reclaim_and_seed_without_unscoped_tenant_reads(
    role_engines: dict[str, Engine], administrator: Engine, role_banks: dict[str, str]
) -> None:
    worker_engine = role_engines["aqs_worker"]
    with Session(administrator) as admin:
        for org in (ORG_1, ORG_2):
            job_queue.enqueue(admin, org, "pipeline_refresh", bank_id=role_banks[org])
        admin.commit()
    with Session(worker_engine) as session:
        assert session.scalars(select(Job)).all() == []
        first = job_queue.claim_next(session, NOW, ("pipeline_refresh",), claimed_by="worker-one")
        second = job_queue.claim_next(session, NOW, ("pipeline_refresh",), claimed_by="worker-two")
        assert first is not None and second is not None and first.id != second.id
        assert {first.organization_id, second.organization_id} == {ORG_1, ORG_2}
        assert session.scalars(select(Job)).all() == []
        assert job_queue.claim_next(session, NOW, ("pipeline_refresh",)) is None
        assert (
            job_queue.reclaim_stale(
                session, NOW + timedelta(hours=1), stale_after=timedelta(minutes=15)
            )
            == 2
        )
        assert scheduler.seed_ticks(session) == 2
        assert scheduler.seed_ticks(session) == 2
    with Session(administrator) as admin:
        ticks = admin.scalars(select(Job).where(Job.job_type == "scheduled_tick")).all()
        assert len(ticks) == 2
        assert {job.organization_id for job in ticks} == {ORG_1, ORG_2}


def test_context_survives_transactions_and_clears_on_pool_reuse(
    role_engines: dict[str, Engine], role_banks: dict[str, str]
) -> None:
    engine = role_engines["aqs_worker"]
    for org in (ORG_1, ORG_2):
        with scoped(engine, org, role_banks[org]) as session:
            for _ in range(2):
                assert tuple(
                    session.execute(
                        text(
                            "SELECT current_setting('app.organization_id'), "
                            "current_setting('app.bank_id')"
                        )
                    ).one()
                ) == (org, role_banks[org])
                session.commit()
    with Session(engine) as session:
        assert tuple(
            session.execute(
                text(
                    "SELECT current_setting('app.organization_id'), current_setting('app.bank_id')"
                )
            ).one()
        ) == ("", "")
        assert session.scalars(select(Job)).all() == []


def test_live_data_session_preserves_readonly_tenant_across_transactions(
    role_engines: dict[str, Engine],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        live_data, "_URL", role_engines["aqs_api"].url.render_as_string(hide_password=False)
    )
    monkeypatch.setenv("LIVE_DATA_ORG_ID", ORG_1)
    fixture = cast(Callable[[], Iterator[Session]], getattr(live_data.live_db, "__wrapped__"))
    sessions = fixture()
    try:
        session = next(sessions)
        for end_transaction in (session.commit, session.rollback, session.rollback):
            assert session.scalars(text("SELECT id FROM organizations")).all() == [ORG_1]
            assert session.scalar(text("SHOW transaction_read_only")) == "on"
            assert session.scalar(text("SELECT current_setting('app.organization_id')")) == ORG_1
            end_transaction()
        with pytest.raises(DBAPIError):
            session.execute(text("UPDATE organizations SET name = 'forbidden'"))
        session.rollback()
        assert session.scalars(text("SELECT id FROM organizations")).all() == [ORG_1]
    finally:
        sessions.close()


@pytest.mark.parametrize("role", ["aqs_api", "aqs_bi", "aqs_operator", "aqs_audit_verifier"])
@pytest.mark.parametrize(
    "command",
    [
        "SELECT * FROM aqs_worker_claim_job(now(), ARRAY['pipeline_refresh'], 'intruder')",
        "SELECT aqs_worker_reclaim_jobs(now(), jsonb_build_object('pipeline_refresh', 900))",
        "SELECT aqs_worker_seed_ticks(now())",
        "SELECT aqs_worker_enqueue_platform_daily('desk_capture', CURRENT_DATE, '{}')",
        "SELECT * FROM aqs_worker_bank_key('synthetic')",
        "SELECT aqs_worker_storage_key_required('synthetic')",
        "SELECT * FROM aqs_worker_audit_check(now())",
        "SELECT aqs_worker_desk_schema()",
        "SELECT aqs_worker_overdue_jobs(900, ARRAY['pipeline_refresh'], false)",
    ],
)
def test_other_roles_cannot_use_worker_platform_entry_points(
    role_engines: dict[str, Engine], role: str, command: str
) -> None:
    with Session(role_engines[role]) as session, pytest.raises(DBAPIError):
        session.execute(text(command))


def test_worker_key_resolution_is_job_scoped_and_storage_uses_worker_credentials(
    role_engines: dict[str, Engine],
    role_banks: dict[str, str],
    administrator: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with Session(administrator) as admin:
        for org in (ORG_1, ORG_2):
            admin.add(
                BankEncryptionKey(
                    bank_id=role_banks[org],
                    organization_id=org,
                    storage_slug=f"worker-{org}",
                    provider="aws_kms",
                    key_id=f"synthetic-{org}",
                    region="eu-west-1",
                    owner_account="123456789012",
                    status="active",
                )
            )
        admin.commit()
    monkeypatch.setattr(
        "app.db.session.get_worker_sessionmaker", lambda: sessionmaker(role_engines["aqs_worker"])
    )
    with worker_tenant_context(ORG_1, role_banks[ORG_1]), get_storage_session() as session:
        own = DatabaseEnvelopeStore.resolve_bank(session, f"worker-{ORG_1}")
        assert own is not None and own.bank_id == role_banks[ORG_1]
        assert DatabaseEnvelopeStore.resolve_bank(session, f"worker-{ORG_2}") is None
        with pytest.raises(DBAPIError):
            session.scalars(select(BankEncryptionKey)).all()
    with Session(role_engines["aqs_worker"]) as session:
        assert DatabaseEnvelopeStore.resolve_bank(session, f"worker-{ORG_1}") is None

    provider = LocalKeyProvider()
    provider.add_key(KeyReference("aws_kms", f"synthetic-{ORG_1}", "eu-west-1", "123456789012"))
    store = DatabaseEnvelopeStore(get_storage_session, lambda _name: provider)
    context = {"purpose": "worker-storage-fixture"}
    with worker_tenant_context(ORG_1, role_banks[ORG_1]):
        prepared = store.prepare(f"worker-{ORG_1}", context)
        reopened = store.open(f"worker-{ORG_1}", prepared.envelope_id, context)
        assert reopened.plaintext == prepared.plaintext
        with pytest.raises(KeyUnavailableError):
            store.prepare(f"worker-{ORG_2}", context)
    with worker_tenant_context(ORG_2, role_banks[ORG_2]), pytest.raises(KeyUnavailableError):
        store.open(f"worker-{ORG_1}", prepared.envelope_id, context)


def test_optional_encryption_refuses_foreign_and_same_org_other_bank_storage(
    role_engines: dict[str, Engine],
    administrator: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with Session(administrator) as admin:
        banks = [
            Bank(
                organization_id=org,
                name="Storage scope fixture",
                short_name="Scope",
                currency="GHS",
                jurisdiction_code="GH",
                license_type="bank",
                institution_type="universal_bank",
                storage_slug=f"worker-scope-{uuid4().hex}",
            )
            for org in (ORG_1, ORG_1, ORG_2)
        ]
        admin.add_all(banks)
        admin.commit()
        bank_ids = [bank.id for bank in banks]
        slugs = [cast(str, bank.storage_slug) for bank in banks]
    monkeypatch.setenv("BANK_KEY_REQUIRED", "0")
    get_key_settings.cache_clear()
    monkeypatch.setattr(
        database, "get_worker_sessionmaker", lambda: sessionmaker(role_engines["aqs_worker"])
    )
    encryption = ObjectEncryption()
    try:
        with worker_tenant_context(ORG_1, bank_ids[0]):
            assert not encryption.bank_key_required(StorageLocation(slugs[0], "raw", "fixture"))
            for slug in (*slugs[1:], "unknown-storage-fixture"):
                with pytest.raises(KeyUnavailableError):
                    encryption.bank_key_required(StorageLocation(slug, "raw", "fixture"))
        with worker_tenant_context(ORG_1, bank_ids[1]):
            # A cached storage client picks up the next job's credential context.
            assert not encryption.bank_key_required(StorageLocation(slugs[1], "raw", "fixture"))
            with pytest.raises(KeyUnavailableError):
                encryption.bank_key_required(StorageLocation(slugs[0], "raw", "fixture"))
    finally:
        get_key_settings.cache_clear()


@pytest.mark.parametrize("organization_id", (None, ORG_1, ORG_2))
def test_worker_can_verify_both_audit_streams_without_reading_them(
    role_engines: dict[str, Engine],
    administrator: Engine,
    monkeypatch: pytest.MonkeyPatch,
    organization_id: str | None,
) -> None:
    monkeypatch.setenv("AUDIT_INTEGRITY_ENABLED", "1")
    for org in (ORG_1, ORG_2):
        with scoped(role_engines["aqs_api"], org) as session:
            session.add(
                AuditEvent(organization_id=org, event_type="worker.integrity.probe", details={})
            )
            session.commit()
    with administrator.begin() as connection:
        entries = cast(int, connection.scalar(text("SELECT count(*) FROM audit_events")))
        epoch = cast(
            int, connection.scalar(text("SELECT recovery_epoch FROM audit_integrity_schedule"))
        )
        connection.execute(
            text("UPDATE audit_integrity_schedule SET run_after = :now"), {"now": NOW}
        )
    factory = sessionmaker(role_engines["aqs_worker"], info={"organization_id": organization_id})
    results = audit_integrity.AuditIntegrityTask().run(factory, NOW)
    assert results is not None
    assert {result.stream for result in results} == {"audit_events", "operator_audit_log"}
    assert all(result.valid for result in results)
    assert next(result.entries for result in results if result.stream == "audit_events") == entries
    assert audit_integrity.AuditIntegrityTask().run(factory, NOW) is None
    with administrator.connect() as connection:
        assert tuple(
            connection.execute(
                text("SELECT run_after, recovery_epoch FROM audit_integrity_schedule")
            ).one()
        ) == (NOW + timedelta(hours=1), epoch + 1)
    with Session(role_engines["aqs_worker"]) as session:
        assert session.scalar(text("SELECT count(*) FROM audit_events")) == 0
    for table in (
        "operator_audit_log",
        "audit_chain_entries",
        "audit_chain_heads",
        "audit_integrity_schedule",
    ):
        with factory() as session, pytest.raises(DBAPIError):
            session.execute(text(f"SELECT * FROM {table}"))


def test_desk_views_allow_robot_drafts_and_refuse_approval_or_base_writes(
    role_engines: dict[str, Engine],
    administrator: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with Session(administrator) as admin:
        admin.add(
            DeskMethodology(
                methodology_code="worker-fixture",
                version=1,
                status="approved",
                parameters={},
                change_rationale="Synthetic fixture",
                proposed_by="fixture-maker",
                approved_by="fixture-checker",
                effective_from=date(2026, 1, 1),
            )
        )
        admin.commit()
    monkeypatch.setattr(
        worker, "get_worker_sessionmaker", lambda: sessionmaker(role_engines["aqs_worker"])
    )
    with worker.job_session(ORG_1) as session:
        draft = DeskDetermination(
            cob_date=NOW.date(),
            methodology_code="worker-fixture",
            methodology_version=1,
            input_snapshot=[],
            input_digest="0" * 64,
            prepared_by="desk-capture-job@aequoros.system",
            status="draft",
        )
        session.add(draft)
        session.commit()
        draft_id = draft.id
        draft.status = "approved"
        with pytest.raises(DBAPIError):
            session.commit()
        session.rollback()
    with Session(administrator) as admin:
        persisted = admin.get(DeskDetermination, draft_id)
        assert persisted is not None and persisted.status == "draft"
    with Session(role_engines["aqs_worker"]) as session, pytest.raises(DBAPIError):
        session.execute(
            text("UPDATE desk_determinations SET status = 'approved' WHERE id = :id"),
            {"id": draft_id},
        )


@pytest.fixture
def worker_desk_session(
    role_engines: dict[str, Engine], monkeypatch: pytest.MonkeyPatch
) -> Iterator[Session]:
    monkeypatch.setattr(
        worker, "get_worker_sessionmaker", lambda: sessionmaker(role_engines["aqs_worker"])
    )
    with worker.job_session(ORG_1) as session:
        yield session


def _gss_grr_payload(value: str) -> bytes:
    return json.dumps(
        {"data": [{"key": ["2026M06", "Ghana reference rate"], "values": [value]}]}
    ).encode()


def _ingest_worker_capture(
    session: Session, source_key: str, raw: bytes
) -> tuple[DeskSourceCapture, ParseResult]:
    capture = DeskSourceCapture(
        source_key=source_key,
        as_of_date=NOW.date(),
        content_sha256=hashlib.sha256(raw).hexdigest(),
        parser_version="pending",
        created_by=capture_job.CAPTURE_IDENTITY,
    )
    session.add(capture)
    session.flush()
    result = ingest_capture(session, capture, raw)
    session.commit()
    return capture, result


@pytest.mark.parametrize("gss_first", (False, True))
def test_worker_capture_commits_cross_source_reconciliation_and_reingestion(
    worker_desk_session: Session, administrator: Engine, gss_first: bool
) -> None:
    raw = (
        Path(__file__).parents[1] / "fixtures/market_desk/raw/bog_summary_econ_fin_data_2026-07.pdf"
    ).read_bytes()
    sources = [("bog_sefd_pdf", raw), ("gss_interest_px", _gss_grr_payload("29.4"))]
    if gss_first:
        sources.reverse()
    captures: dict[str, DeskSourceCapture] = {}
    results: list[ParseResult] = []
    for source_key, payload in sources:
        capture, result = _ingest_worker_capture(worker_desk_session, source_key, payload)
        assert result.errors == []
        assert capture.status == "parsed"
        captures[source_key] = capture
        results.append(result)
    assert any("cross-source mismatch" in warning for warning in results[-1].warnings)

    with Session(administrator) as admin:
        rows = {
            row.series_code: row
            for row in admin.scalars(
                select(DeskObservation).where(
                    DeskObservation.series_code.in_(("GHS.SEFD.GRR", "GHS.GRR")),
                    DeskObservation.as_of_date == date(2026, 6, 1),
                    DeskObservation.superseded_by.is_(None),
                )
            )
        }
        assert set(rows) == {"GHS.SEFD.GRR", "GHS.GRR"}
        for series, source, value, other in (
            ("GHS.SEFD.GRR", "bog_sefd_pdf", "10.02", "GHS.GRR"),
            ("GHS.GRR", "gss_interest_px", "29.4", "GHS.SEFD.GRR"),
        ):
            row = rows[series]
            assert row.value == Decimal(value)
            assert row.capture_id == captures[source].id
            assert row.entered_by is None
            assert "cross_source_mismatch" in row.quality_flags
            attributes = cast(dict[str, object], row.attributes)
            mismatch = cast(dict[str, str], attributes["cross_source_mismatch"])
            assert set(mismatch) == {"against", "other_value", "tolerance"}
            assert mismatch["against"] == other
            assert Decimal(mismatch["other_value"]) == rows[other].value
            assert mismatch["tolerance"] == "0.5"
        old_id = rows["GHS.GRR"].id

    corrected, result = _ingest_worker_capture(
        worker_desk_session, "gss_interest_px", _gss_grr_payload("29.5")
    )
    assert result.errors == []
    with Session(administrator) as admin:
        old = admin.get(DeskObservation, old_id)
        assert old is not None and old.value == Decimal("29.4")
        assert old.superseded_by is not None
        current = admin.get(DeskObservation, old.superseded_by)
        assert current is not None and current.value == Decimal("29.5")
        assert current.capture_id == corrected.id
        assert "cross_source_mismatch" in current.quality_flags
    old = worker_desk_session.get(DeskObservation, old_id)
    assert old is not None
    old.quality_flags = [*cast(list[object], old.quality_flags), "cross_source_mismatch"]
    with pytest.raises(DBAPIError, match="append-only"):
        worker_desk_session.commit()
    worker_desk_session.rollback()


@pytest.mark.parametrize("superseding", (False, True))
@pytest.mark.parametrize(
    "field, new_value",
    (
        ("value", Decimal("30.0")),
        ("as_of_date", date(2026, 6, 2)),
        ("capture_id", None),
        ("entered_by", "other-robot@aequoros.system"),
        ("series_code", "GHS.OTHER"),
        ("unit", "rate"),
        ("id", uuid4()),
        ("created_at", NOW),
        ("attributes", {"month_key": "2026M05"}),
    ),
)
def test_worker_observation_refuses_financial_and_lineage_changes(
    worker_desk_session: Session,
    administrator: Engine,
    field: str,
    new_value: object,
    superseding: bool,
) -> None:
    capture, result = _ingest_worker_capture(
        worker_desk_session, "gss_interest_px", _gss_grr_payload("29.4")
    )
    assert result.errors == []
    row = worker_desk_session.scalars(
        select(DeskObservation).where(DeskObservation.capture_id == capture.id)
    ).one()
    row_id = row.id
    with Session(administrator) as admin:
        before = dict(
            cast(
                Mapping[str, object],
                admin.execute(select(DeskObservation.__table__).where(DeskObservation.id == row_id))
                .mappings()
                .one(),
            )
        )
    setattr(row, field, new_value)
    row.quality_flags = [*cast(list[object], row.quality_flags), "cross_source_mismatch"]
    if superseding:
        row.superseded_by = uuid4()
    with pytest.raises(DBAPIError, match="append-only"):
        worker_desk_session.commit()
    worker_desk_session.rollback()
    with Session(administrator) as admin:
        after = dict(
            cast(
                Mapping[str, object],
                admin.execute(select(DeskObservation.__table__).where(DeskObservation.id == row_id))
                .mappings()
                .one(),
            )
        )
        assert after == before


@pytest.mark.parametrize("envelope_bank", [True, False])
def test_dispatch_refuses_conflicting_or_foreign_payload_bank_before_handler(
    role_engines: dict[str, Engine],
    role_banks: dict[str, str],
    administrator: Engine,
    monkeypatch: pytest.MonkeyPatch,
    envelope_bank: bool,
) -> None:
    monkeypatch.setenv("BI_SCHEDULER_ENABLED", "0")
    monkeypatch.setattr(
        worker, "get_worker_sessionmaker", lambda: sessionmaker(role_engines["aqs_worker"])
    )
    with Session(administrator) as admin:
        job = job_queue.enqueue(
            admin,
            ORG_1,
            "bi_mart_refresh",
            bank_id=role_banks[ORG_1] if envelope_bank else None,
            payload={"bank_id": role_banks[ORG_2]},
        )
        admin.commit()
        job_id = job.id
    assert worker.run_once(("bi_mart_refresh",), worker_id="bank-scope-probe")
    with Session(administrator) as admin:
        persisted = admin.get(Job, job_id)
        assert persisted is not None
        assert persisted.status == "queued" and persisted.attempts == 1
        assert persisted.error is not None
        assert (
            "envelope and payload disagree" if envelope_bank else "does not belong"
        ) in persisted.error


def test_worker_dispatch_state_machine(
    role_engines: dict[str, Engine],
    administrator: Engine,
    role_banks: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Queue → running → success/retry; wrong-lane polls and terminal polls do nothing.

    Financial filing/maker-checker transitions are inapplicable to this queue;
    handlers retain their own authorization and immutability checks.
    """
    monkeypatch.setattr(
        worker,
        "get_worker_sessionmaker",
        lambda: sessionmaker(role_engines["aqs_worker"], expire_on_commit=False),
    )
    monkeypatch.setenv("BI_SCHEDULER_ENABLED", "0")

    class QueueMachine(RuleBasedStateMachine):
        def __init__(self) -> None:
            super().__init__()
            self.now = NOW
            self.expected: dict[str, str] = {}
            self.kinds: dict[str, str] = {}
            self.attempts: dict[str, int] = {}
            self.cooling: set[str] = set()
            with administrator.begin() as connection:
                connection.execute(text("DELETE FROM jobs"))
            monkeypatch.setattr(worker, "utc_now", lambda: self.now)
            monkeypatch.setattr(job_queue, "utc_now", lambda: self.now)

        @rule(
            org=st.sampled_from((ORG_1, ORG_2)),
            kind=st.sampled_from(("bi_mart_refresh", "official_run")),
        )
        def enqueue(self, org: str, kind: str) -> None:
            with scoped(role_engines["aqs_worker"], org, role_banks[org]) as session:
                job = job_queue.enqueue(session, org, kind, bank_id=role_banks[org])
                session.commit()
                key = str(job.id)
                self.expected[key] = "queued"
                self.kinds[key] = kind
                self.attempts[key] = 0

        def next_ready(self) -> str | None:
            return next(
                (
                    key
                    for key, state in self.expected.items()
                    if state == "queued" and key not in self.cooling
                ),
                None,
            )

        @rule()
        def dispatch(self) -> None:
            candidate = self.next_ready()
            worked = worker.run_once(
                ("bi_mart_refresh", "official_run"), worker_id="worker-state-machine"
            )
            assert worked == (candidate is not None)
            if candidate is not None:
                if self.kinds[candidate] == "bi_mart_refresh":
                    self.expected[candidate] = "succeeded"
                elif self.attempts[candidate] < 3:
                    self.expected[candidate] = "queued"
                    self.attempts[candidate] += 1
                    self.cooling.add(candidate)
                else:
                    self.expected[candidate] = "failed"

        @rule()
        def orphan_claim(self) -> None:
            candidate = self.next_ready()
            with Session(role_engines["aqs_worker"]) as session:
                job = job_queue.claim_next(session, self.now, ("bi_mart_refresh", "official_run"))
                assert (str(job.id) if job is not None else None) == candidate
                if candidate is not None:
                    self.expected[candidate] = "running"

        @rule()
        def wrong_lane(self) -> None:
            assert worker.run_once(("icaap_ai_draft",), worker_id="worker-state-machine") is False

        @rule()
        def reclaim(self) -> None:
            self.now += timedelta(hours=1)
            self.cooling.clear()
            running = [key for key, state in self.expected.items() if state == "running"]
            with Session(role_engines["aqs_worker"]) as session:
                assert job_queue.reclaim_stale(
                    session, self.now, stale_after=timedelta(minutes=15)
                ) == len(running)
            for key in running:
                if self.attempts[key] < 3:
                    self.expected[key] = "queued"
                    self.attempts[key] += 1
                else:
                    self.expected[key] = "failed"

        @rule()
        def other_tenant_cannot_mutate(self) -> None:
            with scoped(role_engines["aqs_worker"], ORG_2) as session:
                assert session.scalars(select(Job).where(Job.organization_id == ORG_1)).all() == []
                assert (
                    session.execute(
                        text(
                            "UPDATE jobs SET status = 'failed' "
                            "WHERE organization_id = :org RETURNING id"
                        ),
                        {"org": ORG_1},
                    ).all()
                    == []
                )
                session.commit()

        @invariant()
        def persisted_states_match(self) -> None:
            with Session(administrator) as session:
                rows = cast(
                    list[tuple[object, str, int]],
                    session.execute(select(Job.id, Job.status, Job.attempts)).all(),
                )
                assert {str(key): state for key, state, _ in rows} == self.expected
                assert {str(key): attempts for key, _, attempts in rows} == self.attempts

    run_database_machine(QueueMachine)


def test_claim_skips_a_locked_candidate_and_rollback_preserves_it(
    administrator: Engine,
    role_engines: dict[str, Engine],
) -> None:
    with Session(administrator) as session:
        first = job_queue.enqueue(session, ORG_1, "pipeline_refresh")
        second = job_queue.enqueue(session, ORG_2, "pipeline_refresh")
        session.commit()
        first_id, second_id = first.id, second.id
    with Session(role_engines["aqs_worker"]) as locked:
        row = locked.execute(
            text("SELECT id FROM aqs_worker_claim_job(:now, ARRAY['pipeline_refresh'], 'first')"),
            {"now": NOW},
        ).one()
        assert row[0] == first_id
        with Session(role_engines["aqs_worker"]) as concurrent:
            claimed = job_queue.claim_next(concurrent, NOW, ("pipeline_refresh",))
            assert claimed is not None and claimed.id == second_id
        locked.rollback()
    with Session(role_engines["aqs_worker"]) as session:
        claimed = job_queue.claim_next(session, NOW, ("pipeline_refresh",))
        assert claimed is not None and claimed.id == first_id


def test_partition_wrappers_allow_builder_parents_and_refuse_other_drops(
    role_engines: dict[str, Engine],
) -> None:
    with scoped(role_engines["aqs_worker"], ORG_1) as session:
        parent = "bi_fact_position_daily"
        created = partitions.ensure_month_partition(session, parent, date(2025, 2, 1))
        assert created is not None
        session.commit()
        assert partitions.drop_month_partition(session, parent, date(2025, 2, 1)) is True
        session.commit()
        for forbidden in ("bi_fact_position_eom", "bi_fact_loan_event", "bi_query_log"):
            with pytest.raises(DBAPIError):
                session.execute(
                    text(
                        "SELECT aqs_worker_drop_daily_partition(CAST(:parent AS regclass), :month)"
                    ),
                    {"parent": forbidden, "month": date(2025, 2, 1)},
                )
            session.rollback()
        query_partition = partitions.ensure_month_partition(
            session, "bi_query_log", date(2025, 2, 1)
        )
        assert query_partition is not None
        session.commit()
        with pytest.raises(DBAPIError):
            session.execute(text("CREATE TABLE forbidden_worker_ddl (id integer)"))


def test_backlog_counts_are_metadata_only_and_split_lanes(
    administrator: Engine,
    role_engines: dict[str, Engine],
) -> None:
    with Session(administrator) as session:
        for org, kind in ((ORG_1, "pipeline_refresh"), (ORG_2, "bi_commentary")):
            job_queue.enqueue(session, org, kind, run_after=NOW - timedelta(days=1))
        session.commit()
    with Session(role_engines["aqs_worker"]) as session:
        for exclude in (True, False):
            count = cast(
                int,
                session.scalar(
                    text("SELECT aqs_worker_overdue_jobs(900, ARRAY['bi_commentary'], :exclude)"),
                    {"exclude": exclude},
                ),
            )
            assert count == 1
        assert session.scalars(select(Job)).all() == []


@pytest.mark.parametrize("role", ("aqs_worker", "aqs_api"))
def test_worker_visibility_accepts_claim_capability_and_refuses_api_role(
    role_engines: dict[str, Engine],
    monkeypatch: pytest.MonkeyPatch,
    role: str,
) -> None:
    factory = sessionmaker(role_engines[role])
    monkeypatch.setattr(database, "get_worker_sessionmaker", lambda: factory)
    assert database.worker_visibility().can_claim == (role == "aqs_worker")


def test_worker_audit_check_without_inherited_audit_ownership(administrator: Engine) -> None:
    with administrator.connect() as admin, admin.begin():
        if (admin.dialect.server_version_info or (0,))[0] >= 16:
            admin.execute(text("GRANT aequoros_audit_owner TO aqs_owner WITH INHERIT FALSE"))
        assert admin.scalar(text("SELECT rolinherit FROM pg_roles WHERE rolname='aqs_owner'")) is False
        assert (
            admin.scalar(text("SELECT pg_has_role('aqs_owner', 'aequoros_audit_owner', 'USAGE')"))
            is False
        )
        admin.execute(
            text(
                "INSERT INTO audit_events (id,organization_id,event_type,details,created_at) "
                "VALUES (gen_random_uuid(), :org, 'worker_probe', '{}', now())"
            ),
            [{"org": ORG_1}, {"org": ORG_2}],
        )
        admin.execute(
            text(
                "INSERT INTO operator_audit_log "
                "(id,operator_email,auth_mode,action,detail,created_at) "
                "VALUES (gen_random_uuid(), 'fixture@example.test', "
                "'oidc', 'worker_probe', '{}', now())"
            )
        )
        expected: list[dict[str, object]] = [
            {
                "stream": stream,
                "entries": cast(int, admin.scalar(text(f"SELECT count(*) FROM {stream}"))),
                "valid": True,
            }
            for stream in ("audit_events", "operator_audit_log")
        ]
        before = cast(
            int,
            admin.execute(
                text("UPDATE audit_integrity_schedule SET run_after = :now RETURNING recovery_epoch"),
                {"now": NOW},
            ).scalar_one(),
        )
        admin.execute(text("SET LOCAL ROLE aqs_worker"))
        epoch, results = cast(
            tuple[int, list[dict[str, object]]],
            tuple(
                admin.execute(
                    text("SELECT * FROM aqs_worker_audit_check(:now)"), {"now": NOW}
                ).one()
            ),
        )
        assert results == expected
        assert epoch == before + 1
        assert admin.execute(
            text("SELECT * FROM aqs_worker_audit_check(:now)"), {"now": NOW}
        ).one() == (epoch, None)
        admin.execute(text("RESET ROLE"))
        assert admin.scalar(text("SELECT run_after FROM audit_integrity_schedule")) == (
            NOW + timedelta(hours=1)
        )
        admin.rollback()


def test_worker_audit_metadata_detects_tampering_without_disclosing_payloads(
    administrator: Engine,
    role_engines: dict[str, Engine],
) -> None:
    with Session(administrator) as session:
        session.add(
            OperatorAuditLog(
                operator_email="synthetic-worker@fixture.invalid",
                auth_mode="oidc",
                action="worker_probe",
                detail={"synthetic": True},
            )
        )
        session.commit()
    with administrator.connect() as admin, admin.begin():
        admin.execute(text("ALTER TABLE audit_chain_entries DISABLE TRIGGER USER"))
        admin.execute(
            text(
                "UPDATE audit_chain_entries SET entry_hash = repeat('f',64) "
                "WHERE stream = 'operator_audit_log'"
            )
        )
        admin.execute(text("UPDATE audit_integrity_schedule SET run_after = :now"), {"now": NOW})
        # The deliberately corrupt fixture stays in this transaction and is visible
        # to the metadata function under the worker's permissions.
        admin.execute(text("SET LOCAL ROLE aqs_worker"))
        epoch, verified = cast(
            tuple[int, list[dict[str, object]]],
            tuple(
                admin.execute(
                    text("SELECT * FROM aqs_worker_audit_check(:now)"), {"now": NOW}
                ).one()
            ),
        )
        assert any(
            row == {"stream": "operator_audit_log", "entries": 1, "valid": False}
            for row in verified
        )
        assert all(set(row) == {"stream", "entries", "valid"} for row in verified)
        assert isinstance(epoch, int)
        admin.rollback()


def test_legacy_bypass_worker_operates_before_and_after_compatibility_migration(
    administrator: Engine,
    role_schema: migrations.MigratedPostgresSchema,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    name, password = f"legacy_worker_{uuid4().hex}", uuid4().hex
    config = migrations.alembic_config_for_app()
    monkeypatch.setenv(
        "DATABASE_URL", role_schema.app_engine.url.render_as_string(hide_password=False)
    )
    monkeypatch.setenv("BI_SCHEDULER_ENABLED", "0")
    with administrator.begin() as admin:
        admin.execute(
            text(
                f"CREATE ROLE {name} LOGIN PASSWORD '{password}' "
                "BYPASSRLS NOSUPERUSER NOCREATEDB NOCREATEROLE"
            )
        )
        schema = admin.dialect.identifier_preparer.quote_identifier(role_schema.schema_name)
        admin.execute(text(f"GRANT USAGE ON SCHEMA {schema} TO {name}"))
        admin.execute(text(f"GRANT SELECT, INSERT, UPDATE ON jobs, worker_heartbeats TO {name}"))
    engine = create_engine(make_url(administrator.url).set(username=name, password=password))
    try:
        factory = sessionmaker(engine, expire_on_commit=False)
        monkeypatch.setattr(worker, "get_worker_sessionmaker", lambda: factory)
        command.downgrade(config, "202610120096")
        for after_migration in (False, True):
            if after_migration:
                command.upgrade(config, "head")
            with Session(administrator) as admin:
                job = job_queue.enqueue(admin, ORG_1, "bi_mart_refresh")
                admin.commit()
                job_id = job.id
            assert worker.run_once(("bi_mart_refresh",), worker_id="legacy-worker-probe") is True
            with Session(administrator) as admin:
                persisted = admin.get(Job, job_id)
                assert persisted is not None and persisted.status == "succeeded"
                assert persisted.claimed_by == "legacy-worker-probe"
            with engine.connect() as login:
                assert (
                    login.scalar(
                        text("SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user")
                    )
                    is True
                )
    finally:
        engine.dispose()
        command.upgrade(config, "head")
        with administrator.begin() as admin:
            admin.execute(text(f"DROP OWNED BY {name}"))
            admin.execute(text(f"DROP ROLE {name}"))


def test_platform_functions_keep_nologin_ownership_and_pinned_paths(administrator: Engine) -> None:
    with administrator.connect() as connection:
        rows = cast(
            list[tuple[str, str, bool, list[str]]],
            connection.execute(
                text(
                    "SELECT p.proname, pg_get_userbyid(p.proowner), p.prosecdef, p.proconfig "
                    "FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
                    "WHERE n.nspname = current_schema() AND p.proname LIKE 'aqs_worker_%'"
                )
            ).all(),
        )
        assert len(rows) == 15
        for name, owner, definer, config in rows:
            assert owner == "aqs_owner", name
            assert definer, name
            paths = [entry for entry in config if entry.startswith("search_path=")]
            assert len(paths) == 1, name
            assert paths[0].startswith("search_path=pg_catalog, "), name
            assert paths[0].endswith("pg_temp"), name
        assert (
            connection.scalar(
                text(
                    "SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
                    "CROSS JOIN LATERAL aclexplode(coalesce(p.proacl, "
                    "acldefault('f',p.proowner))) a "
                    "WHERE n.nspname = current_schema() AND p.proname LIKE 'aqs_worker_%' "
                    "AND a.grantee = 0"
                )
            )
            == 0
        )
        schema = cast(str, connection.scalar(text("SELECT aqs_worker_desk_schema()")))
        assert (
            connection.scalar(
                text("SELECT has_schema_privilege('aqs_worker', :schema, 'CREATE')"),
                {"schema": schema},
            )
            is False
        )


def test_worker_downgrade_preserves_existing_owner_table_and_partition_rights(
    administrator: Engine,
    role_schema: migrations.MigratedPostgresSchema,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tables = (
        "jobs",
        "banks",
        "bank_encryption_keys",
        "desk_source_captures",
        "desk_observations",
        "desk_determinations",
        "desk_methodologies",
        "desk_curve_definitions",
    )
    functions = (
        "bi_ensure_month_partition(regclass,date)",
        "bi_ensure_year_partition(regclass,date)",
        "bi_drop_month_partition(regclass,date)",
    )
    schema = administrator.dialect.identifier_preparer.quote_identifier(role_schema.schema_name)
    monkeypatch.setenv(
        "DATABASE_URL", role_schema.app_engine.url.render_as_string(hide_password=False)
    )
    config = migrations.alembic_config_for_app()
    original_owners: dict[str, str] = {}
    with administrator.begin() as connection:
        for table in tables:
            original_owners[table] = cast(
                str,
                connection.scalar(
                    text(
                        "SELECT pg_get_userbyid(relowner) FROM pg_class "
                        "WHERE oid=CAST(:name AS regclass)"
                    ),
                    {"name": f"{schema}.{table}"},
                ),
            )
            connection.execute(text(f"ALTER TABLE {schema}.{table} OWNER TO aqs_owner"))
        for function in functions:
            original_owners[function] = cast(
                str,
                connection.scalar(
                    text(
                        "SELECT pg_get_userbyid(proowner) FROM pg_proc "
                        "WHERE oid=CAST(:name AS regprocedure)"
                    ),
                    {"name": f"{schema}.{function}"},
                ),
            )
            connection.execute(text(f"ALTER FUNCTION {schema}.{function} OWNER TO aqs_owner"))
    try:
        command.downgrade(config, "202610120096")
        with administrator.connect() as connection:
            for table in tables:
                for privilege in ("SELECT", "INSERT", "UPDATE"):
                    assert (
                        connection.scalar(
                            text("SELECT has_table_privilege('aqs_owner', :name, :privilege)"),
                            {"name": f"{schema}.{table}", "privilege": privilege},
                        )
                        is True
                    )
            for function in functions:
                assert (
                    connection.scalar(
                        text("SELECT has_function_privilege('aqs_owner', :name, 'EXECUTE')"),
                        {"name": f"{schema}.{function}"},
                    )
                    is True
                )
    finally:
        with administrator.begin() as connection:
            for table in tables:
                owner = connection.dialect.identifier_preparer.quote_identifier(
                    original_owners[table]
                )
                connection.execute(text(f"ALTER TABLE {schema}.{table} OWNER TO {owner}"))
            for function in functions:
                owner = connection.dialect.identifier_preparer.quote_identifier(
                    original_owners[function]
                )
                connection.execute(text(f"ALTER FUNCTION {schema}.{function} OWNER TO {owner}"))
        command.upgrade(config, "head")


def test_compatibility_migration_does_not_promote_readonly_legacy_bypass_role(
    administrator: Engine,
    role_schema: migrations.MigratedPostgresSchema,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    name, password = f"legacy_reader_{uuid4().hex}", uuid4().hex
    config = migrations.alembic_config_for_app()
    monkeypatch.setenv(
        "DATABASE_URL", role_schema.app_engine.url.render_as_string(hide_password=False)
    )
    schema = administrator.dialect.identifier_preparer.quote_identifier(role_schema.schema_name)
    with administrator.begin() as connection:
        connection.execute(
            text(
                f"CREATE ROLE {name} LOGIN PASSWORD '{password}' "
                "BYPASSRLS NOSUPERUSER NOCREATEDB NOCREATEROLE"
            )
        )
        connection.execute(text(f"GRANT USAGE ON SCHEMA {schema} TO {name}"))
        connection.execute(text(f"GRANT SELECT ON {schema}.jobs TO {name}"))
    engine = create_engine(make_url(administrator.url).set(username=name, password=password))
    try:
        command.downgrade(config, "202610120096")
        command.upgrade(config, "head")
        with administrator.connect() as connection:
            view_schema = connection.dialect.identifier_preparer.quote_identifier(
                cast(str, connection.scalar(text("SELECT aqs_worker_desk_schema()")))
            )
        for sql in (
            "SELECT * FROM aqs_worker_claim_job(now(), ARRAY['pipeline_refresh'], 'reader')",
            f"UPDATE {view_schema}.desk_source_captures SET id = id",
        ):
            with Session(engine) as session, pytest.raises(DBAPIError, match="permission denied"):
                session.execute(text(sql))
        with engine.connect() as connection:
            assert (
                connection.scalar(
                    text("SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user")
                )
                is True
            )
    finally:
        engine.dispose()
        command.upgrade(config, "head")
        with administrator.begin() as connection:
            connection.execute(text(f"DROP OWNED BY {name}"))
            connection.execute(text(f"DROP ROLE {name}"))


def test_scoped_ticks_preserve_platform_daily_cadence(
    role_engines: dict[str, Engine],
    administrator: Engine,
    role_banks: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DESK_CAPTURE_ENABLED", "1")
    monkeypatch.setenv("DESK_CAPTURE_HOUR_UTC", "0")
    monkeypatch.setenv("BI_SCHEDULER_ENABLED", "1")
    monkeypatch.setattr(scheduler, "utc_now", lambda: NOW)
    get_settings.cache_clear()
    for index, org in enumerate((ORG_1, ORG_2, ORG_1)):
        with scoped(role_engines["aqs_worker"], org) as session:
            tick = job_queue.enqueue(session, org, "scheduled_tick")
            scheduler.run_tick(session, tick)
            session.commit()
            assert tick.progress["desk_capture_enqueued"] is (index == 0)
            assert tick.progress["bi_retention_enqueued"] is (index == 0)
        if org == ORG_1:
            with Session(administrator) as admin:
                for job in admin.scalars(
                    select(Job).where(Job.job_type.in_(("desk_capture", "bi_retention")))
                ):
                    job_queue.complete(admin, job)
                admin.commit()
    with Session(administrator) as session:
        for kind in ("desk_capture", "bi_retention"):
            jobs = session.scalars(select(Job).where(Job.job_type == kind)).all()
            assert len(jobs) == 1
            assert jobs[0].organization_id == ORG_1
            assert jobs[0].status == "succeeded"
            assert jobs[0].bank_id is None
            assert jobs[0].payload == (
                {"cob_date": NOW.date().isoformat()}
                if kind == "desk_capture"
                else {"builder_version": BUILDER_VERSION}
            )
    tomorrow = NOW + timedelta(days=1)
    monkeypatch.setattr(scheduler, "utc_now", lambda: tomorrow)
    for index, org in enumerate((ORG_2, ORG_1)):
        with scoped(role_engines["aqs_worker"], org) as session:
            tick = job_queue.enqueue(session, org, "scheduled_tick")
            scheduler.run_tick(session, tick)
            session.commit()
            assert tick.progress["desk_capture_enqueued"] is (index == 0)
            assert tick.progress["bi_retention_enqueued"] is (index == 0)
    with Session(administrator) as session:
        for kind, prefix in (("desk_capture", "desk_capture"), ("bi_retention", "bi-retention")):
            jobs = session.scalars(select(Job).where(Job.job_type == kind)).all()
            assert len(jobs) == 2
            next_job = next(job for job in jobs if job.organization_id == ORG_2)
            assert next_job.coalesce_key == f"{prefix}:{tomorrow.date().isoformat()}"
            assert next_job.status == "queued"
    with scoped(role_engines["aqs_worker"], ORG_2) as session, pytest.raises(DBAPIError):
        session.execute(
            text("SELECT aqs_worker_enqueue_platform_daily('pipeline_refresh', :day, '{}')"),
            {"day": NOW.date()},
        )
    get_settings.cache_clear()


@pytest.mark.parametrize("enabled, hour", ((False, 18), (True, 17), (True, 18)))
def test_scoped_desk_caller_only_persists_due_captures(
    role_engines: dict[str, Engine],
    administrator: Engine,
    monkeypatch: pytest.MonkeyPatch,
    enabled: bool,
    hour: int,
) -> None:
    monkeypatch.setenv("DESK_CAPTURE_ENABLED", str(enabled))
    monkeypatch.setenv("DESK_CAPTURE_HOUR_UTC", "18")
    get_settings.cache_clear()
    friday = (NOW + timedelta(days=4)).replace(hour=hour)
    due = enabled and hour >= 18
    with scoped(role_engines["aqs_worker"], ORG_2) as session:
        job = capture_job.enqueue_due_desk_capture(session, ORG_2, now=friday)
        assert (job is not None) is due
        session.commit()
    with Session(administrator) as session:
        jobs = session.scalars(select(Job).where(Job.job_type == "desk_capture")).all()
        assert len(jobs) == int(due)
        if due:
            assert jobs[0].organization_id == ORG_2
            assert jobs[0].bank_id is None
            assert jobs[0].coalesce_key == f"desk_capture:{friday.date().isoformat()}"
            assert jobs[0].payload == {"cob_date": friday.date().isoformat(), "auction_pass": True}
    get_settings.cache_clear()


@pytest.mark.parametrize("explicit", [False, True])
def test_split_credentials_preserve_authentication_and_operator_visibility(
    administrator: Engine,
    role_engines: dict[str, Engine],
    legacy_engines: dict[str, Engine],
    monkeypatch: pytest.MonkeyPatch,
    explicit: bool,
) -> None:
    api_url = role_engines["aqs_api"].url.render_as_string(hide_password=False)
    legacy_url = legacy_engines["preauth"].url.render_as_string(hide_password=False)
    operator_url = legacy_engines["operator"].url.render_as_string(hide_password=False)
    worker_url = role_engines["aqs_worker"].url.render_as_string(hide_password=False)
    monkeypatch.setenv("DATABASE_URL", api_url)
    monkeypatch.setenv("WORKER_DATABASE_URL", worker_url if explicit else legacy_url)
    monkeypatch.setenv("PREAUTH_DATABASE_URL", legacy_url if explicit else "")
    monkeypatch.setenv("OPERATOR_DATABASE_URL", operator_url if explicit else "")
    monkeypatch.setenv("AUTH_JWT_SECRET", "worker-cutover-test-signing-secret-0000000000")
    monkeypatch.setenv("SSO_INTERNAL_KEY", "worker-cutover-test-internal-key")
    monkeypatch.setenv("CREDENTIAL_VAULT_MASTER_KEY", "worker-cutover-test-vault-key")
    get_settings.cache_clear()
    get_operator_settings.cache_clear()
    password = "worker-cutover-test-password-00000000"  # noqa: S105 - fixture only
    email = f"worker-cutover-{uuid4().hex}@example.test"
    with Session(administrator) as session:
        user = User(
            organization_id=ORG_1,
            email=email,
            role="account_admin",
            is_active=True,
            password_hash=security.hash_password(password),
        )
        session.add(user)
        sso_config.upsert_connection(
            session,
            organization_id=ORG_1,
            issuer="https://fixture.example.test",
            client_id="worker-cutover-client",
            client_secret="fixture-client-secret",
            allowed_email_domains=["example.test"],
            enabled=True,
            actor_user_id=None,
        )
        session.commit()
        user_id = str(user.id)
    app = FastAPI()
    app.include_router(auth_api.router, prefix="/api/v1")
    with TestClient(app) as client:
        login = client.post("/api/v1/auth/login", json={"email": email, "password": password})
        assert login.status_code == 200, login.text
        tokens = cast(dict[str, object], login.json())
        refresh = client.post(
            "/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
        )
        assert refresh.status_code == 200, refresh.text
        rotated = cast(dict[str, object], refresh.json())
        logout = client.post(
            "/api/v1/auth/logout", json={"refresh_token": rotated["refresh_token"]}
        )
        assert logout.status_code == 204, logout.text
        refused = client.post(
            "/api/v1/auth/refresh", json={"refresh_token": rotated["refresh_token"]}
        )
        assert refused.status_code == 401, refused.text
        assert_sso_lookup(client, email, user_id, monkeypatch)
    with database.get_preauth_sessionmaker()() as session:
        assert session.scalar(text("SELECT current_user")) == legacy_engines["preauth"].url.username
        assert session.scalar(text("SHOW application_name")) == "aequoros-preauth"
        assert session.scalar(
            text("SELECT rolsuper FROM pg_roles WHERE rolname=current_user")
        ) is False
    assert resolve_operator_database_url() == (operator_url if explicit else legacy_url)
    with get_operator_sessionmaker()() as session:
        assert set(session.scalars(select(Organization.id))) == {ORG_1, ORG_2}
        assert session.scalar(text("SHOW application_name")) == "aequoros-operator"
        assert session.scalar(text("SELECT current_user")) == (
            legacy_engines["operator" if explicit else "preauth"].url.username
        )
    with database.get_sessionmaker()() as session:
        assert session.scalars(select(User)).all() == []
        assert session.scalar(text("SHOW application_name")) == "aequoros-api"
    with Session(role_engines["aqs_worker"]) as session:
        assert tuple(
            session.execute(
                text(
                    "SELECT rolsuper, rolcreatedb, rolcreaterole, rolbypassrls "
                    "FROM pg_roles WHERE rolname=current_user"
                )
            ).one()
        ) == (False, False, False, False)
        assert session.scalars(select(User)).all() == []
        with pytest.raises(DBAPIError):
            session.execute(text("SELECT * FROM operator_users"))
    database.get_engine(api_url).dispose()
    get_settings.cache_clear()


def assert_sso_lookup(
    client: TestClient, email: str, user_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    status = client.get("/api/v1/auth/sso/status")
    assert status.status_code == 200, status.text
    assert cast(dict[str, object], status.json()) == {"enabled": True}
    config = client.get(
        "/api/v1/auth/sso/client-config",
        headers={"X-Internal-Auth": "worker-cutover-test-internal-key"},
    )
    assert config.status_code == 200, config.text
    assert cast(dict[str, object], config.json()) == {
        "enabled": True,
        "issuer": "https://fixture.example.test",
        "client_id": "worker-cutover-client",
        "client_secret": "fixture-client-secret",
    }
    claims: dict[str, object] = {
        "iss": "https://fixture.example.test",
        "aud": "worker-cutover-client",
        "sub": f"fixture-subject-{uuid4().hex}",
        "email": email,
        "email_verified": True,
    }

    def verify_id_token(id_token: str, *, issuer: str, audience: str) -> dict[str, object]:
        assert id_token == token
        assert issuer == claims["iss"]
        assert audience == claims["aud"]
        return claims

    token = jwt.encode(claims, "fixture-idp-signing-key-padded-to-32-bytes", algorithm="HS256")
    monkeypatch.setattr(security, "verify_oidc_id_token", verify_id_token)
    sso = client.post("/api/v1/auth/sso", json={"id_token": token})
    assert sso.status_code == 200, sso.text
    access = cast(str, cast(dict[str, object], sso.json())["access_token"])
    issued = cast(
        dict[str, object],
        jwt.decode(
            access,
            "worker-cutover-test-signing-secret-0000000000",
            algorithms=["HS256"],
            audience="aequoros-api",
        ),
    )
    assert issued["sub"] == user_id
    assert issued["org"] == ORG_1


@pytest.mark.parametrize("explicit", [False, True])
def test_restricted_credentials_cannot_replace_legacy_cross_tenant_connections(
    role_engines: dict[str, Engine],
    legacy_engines: dict[str, Engine],
    monkeypatch: pytest.MonkeyPatch,
    explicit: bool,
) -> None:
    restricted = role_engines["aqs_worker"].url.render_as_string(hide_password=False)
    legacy = legacy_engines["preauth"].url.render_as_string(hide_password=False)
    monkeypatch.setenv(
        "DATABASE_URL", role_engines["aqs_api"].url.render_as_string(hide_password=False)
    )
    monkeypatch.setenv("WORKER_DATABASE_URL", legacy if explicit else restricted)
    monkeypatch.setenv("PREAUTH_DATABASE_URL", restricted if explicit else "")
    monkeypatch.setenv("OPERATOR_DATABASE_URL", restricted if explicit else "")
    get_settings.cache_clear()
    get_operator_settings.cache_clear()
    app = FastAPI()
    app.include_router(auth_api.router, prefix="/api/v1")
    with TestClient(app) as client:
        response = client.get("/api/v1/auth/sso/status")
        assert response.status_code == 503
        assert "PREAUTH_DATABASE_URL" in str(cast(dict[str, object], response.json())["detail"])
    with pytest.raises(HTTPException, match="OPERATOR_DATABASE_URL") as error:
        get_operator_sessionmaker()
    assert error.value.status_code == 503
    for application in ("aequoros-preauth", "aequoros-operator"):
        database.get_engine(restricted, application).dispose()
    get_settings.cache_clear()


def test_worker_records_bi_export_queries_without_unrestricted_partition_access(
    administrator: Engine,
    role_engines: dict[str, Engine],
    role_banks: dict[str, str],
) -> None:
    identifier = uuid4()
    with Session(administrator) as admin:
        admin.add(
            User(
                id=identifier,
                organization_id=ORG_1,
                email=f"bi-query-{identifier.hex}@example.test",
                role="analyst",
            )
        )
        admin.commit()
    with scoped(role_engines["aqs_worker"], ORG_1, role_banks[ORG_1]) as session:
        session.info["worker_access"] = True
        row = query_log.record(
            session,
            query_log.QueryRecord(
                organization_id=ORG_1,
                bank_id=role_banks[ORG_1],
                principal_user_id=identifier,
                surface="export",
                query_hash="a" * 64,
                decision="allowed",
                catalogue_version="fixture",
            ),
        )
        session.commit()
        assert row.organization_id == ORG_1
        assert row.principal_user_id == identifier
