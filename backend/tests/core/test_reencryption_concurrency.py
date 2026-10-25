from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier, Event, local
from time import monotonic, sleep
from typing import cast
from uuid import UUID

import pytest
from sqlalchemy import Connection, Engine, event, select, text
from sqlalchemy.orm import Session, SessionTransaction

from app import worker
from app.core.key_management import maintenance, reencryption, registry
from app.core.key_management.fields import SealedField, blind_index
from app.core.key_management.local import LocalKeyProvider
from app.core.tenancy import TenantContext
from app.db.base import utc_now
from app.identity.public import Bank
from app.models.canonical import CanonicalCounterparty
from app.models.job import Job
from app.services import canonical_withdrawal, job_queue
from app.services.canonical_withdrawal import WithdrawalError
from app.storage.client import (
    EncryptionCursor,
    EncryptionInventoryItem,
    EncryptionPage,
    ReencryptionIsolation,
    StorageLocation,
)
from tests.core.test_bank_field_encryption import (
    BANK_A,
    BANK_B,
    CTX,
    IDENTIFIER,
    NEXT,
    connect,
    counterparty,
    provider,
)
from tests.core.test_bank_reencryption import EmptyStorage
from tests.support.helpers import ORG_1
from tests.support.reencryption import drain

__all__ = ["provider"]
pytestmark = pytest.mark.committing_db


def engine(db: Session) -> Engine:
    bind = db.get_bind()
    if not isinstance(bind, Engine) or bind.dialect.name != "postgresql":
        pytest.skip("PostgreSQL independent transactions are required.")
    return bind


@pytest.mark.parametrize("rollback_publication", [False, True])
def test_duplicate_worker_waits_for_publication_commit_or_rollback(
    db_session: Session,
    provider: LocalKeyProvider,
    monkeypatch: pytest.MonkeyPatch,
    rollback_publication: bool,
) -> None:
    database = engine(db_session)
    party_id = counterparty(db_session, include_package=False).id
    connect(db_session, provider)
    key = registry.scoped_key(db_session, organization_id=ORG_1, bank_id=BANK_A)
    target = key.current_version_id
    assert target is not None
    item = EncryptionInventoryItem(
        StorageLocation(BANK_A.lower(), "temp", "synthetic-large.csv"),
        "null",
        None,
        False,
        500 * 1024 * 1024,
    )
    job = reencryption.enqueue(db_session, ORG_1, BANK_A)
    job.progress = reencryption.Progress(
        target_version=target, phase="large_object", large_object=item
    ).model_dump(mode="json")
    job.payload = {"reencryption_bytes": item.size_bytes}
    job_id = job.id
    db_session.commit()
    now = utc_now()
    monkeypatch.setattr(worker, "utc_now", lambda: now)
    runner = local()
    copied_a, copied_b, publish_a, publish_b, locked_a, release_a = (Event() for _ in range(6))
    backend_b: list[int] = []

    class PublicationCrash(BaseException):
        pass

    class Storage(EmptyStorage):
        version: UUID | None = None

        def encryption_page(
            self, institution_slug: str, cursor: EncryptionCursor, limit: int
        ) -> EncryptionPage:
            return EncryptionPage(
                [
                    EncryptionInventoryItem(
                        item.location, "null", self.version, False, item.size_bytes
                    )
                ],
                None,
            )

        def reencrypt_isolated(
            self, item: EncryptionInventoryItem, isolation: ReencryptionIsolation
        ) -> bool:
            isolation.release()
            name = cast(str, runner.name)
            (copied_a if name == "A" else copied_b).set()
            assert (publish_a if name == "A" else publish_b).wait(15)
            with isolation.publication(target) as allowed:
                if name == "A":
                    assert allowed
                    locked_a.set()
                    assert release_a.wait(15)
                    if rollback_publication:
                        raise PublicationCrash()
                if allowed:
                    self.version = target
            return allowed

    storage = Storage()
    monkeypatch.setattr(reencryption, "get_storage_client", lambda: storage)

    def track_backend(
        _session: Session, _transaction: SessionTransaction, connection: Connection
    ) -> None:
        # Publication may check out a different connection after the copy commits.
        backend_b.append(cast(int, connection.scalar(text("SELECT pg_backend_pid()"))))

    def session(organization_id: str | None = None) -> Session:
        db = Session(database)
        if organization_id is not None:
            db.info["organization_id"] = organization_id
            if getattr(runner, "name", "") == "B":
                event.listen(db, "after_begin", track_backend)
        return db

    monkeypatch.setattr(worker, "_new_session", session)

    def execute(name: str) -> bool:
        runner.name = name
        return worker.run_once((reencryption.JOB_TYPE,), worker_id=name)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(execute, "A")
        second = None
        try:
            assert copied_a.wait(10)
            now += timedelta(days=1)
            # The production reaper opens a fresh session after the claim commits.
            db_session.expire_all()
            assert job_queue.reclaim_stale(db_session, now, stale_after=timedelta(seconds=900)) == 1
            second = pool.submit(execute, "B")
            assert copied_b.wait(10)
            publish_a.set()
            assert locked_a.wait(10)
            publish_b.set()
            deadline = monotonic() + 10
            blocked = False
            while monotonic() < deadline:
                blocked = bool(
                    db_session.scalar(
                        text("SELECT cardinality(pg_blocking_pids(:pid))"), {"pid": backend_b[-1]}
                    )
                )
                if blocked:
                    break
                sleep(0.01)
            assert blocked and not second.done()
        finally:
            publish_a.set()
            publish_b.set()
            release_a.set()
        if rollback_publication:
            with pytest.raises(PublicationCrash):
                first.result(timeout=15)
        else:
            assert first.result(timeout=15)
        assert second is not None and second.result(timeout=15)
    db_session.expire_all()
    parent = db_session.get(Job, job_id)
    assert parent is not None and parent.status == "succeeded"
    progress = reencryption.Progress.model_validate(parent.progress)
    assert progress.next_job_id is not None
    db_session.commit()
    runner.name = "C"
    for _ in range(200):
        if not worker.run_once((reencryption.JOB_TYPE,), worker_id="C"):
            break
    db_session.expire_all()
    latest = db_session.scalar(
        select(Job).where(Job.bank_id == BANK_A).order_by(Job.queued_at.desc()).limit(1)
    )
    assert (
        latest is not None and reencryption.Progress.model_validate(latest.progress).phase == "done"
    )
    value = db_session.scalar(
        select(CanonicalCounterparty.name).where(CanonicalCounterparty.id == party_id)
    )
    assert isinstance(value, SealedField) and value.envelope_id == target


def test_duplicate_concurrent_batch_publishes_one_continuation(
    db_session: Session,
    provider: LocalKeyProvider,
) -> None:
    database = db_session.get_bind()
    assert isinstance(database, Engine)
    party = counterparty(db_session, include_package=False)
    sibling = counterparty(db_session, bank_id=BANK_B, include_package=False)
    party_id, sibling_id = party.id, sibling.id
    connect(db_session, provider)
    key = registry.scoped_key(db_session, organization_id=ORG_1, bank_id=BANK_A)
    target = key.current_version_id
    job = reencryption.enqueue(db_session, ORG_1, BANK_A)
    job.status = "running"
    job.progress = reencryption.Progress(
        target_version=target,
        phase="fields",
        table=next(
            i
            for i, table in enumerate(maintenance.protected_tables())
            if table.name == "canonical_counterparties"
        ),
    ).model_dump(mode="json")
    job_id = job.id
    db_session.commit()
    ready = Barrier(2)

    def dispatch() -> None:
        with Session(database) as peer:
            peer.info["organization_id"] = ORG_1
            ready.wait(timeout=10)
            if database.dialect.name == "sqlite":
                _ = peer.connection().exec_driver_sql("BEGIN IMMEDIATE")
            claimed = peer.get(Job, job_id)
            assert claimed is not None
            reencryption.run(peer, claimed, EmptyStorage())
            peer.commit()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(dispatch) for _ in range(2)]
        for future in futures:
            future.result(timeout=30)
    db_session.expire_all()
    root = db_session.get(Job, job_id)
    assert root is not None
    progress = reencryption.Progress.model_validate(root.progress)
    assert progress.next_job_id is not None
    assert progress.fields_rewritten > 0
    jobs = list(db_session.scalars(select(Job).where(Job.bank_id == BANK_A)))
    assert len(jobs) == 2
    sealed = db_session.scalar(
        select(CanonicalCounterparty.name).where(CanonicalCounterparty.id == party_id)
    )
    assert isinstance(sealed, SealedField) and sealed.envelope_id == target
    assert (
        db_session.scalar(
            select(CanonicalCounterparty.name).where(CanonicalCounterparty.id == sibling_id)
        )
        == sibling.name
    )
    following = db_session.get(Job, progress.next_job_id)
    assert following is not None
    assert (
        reencryption.Progress.model_validate(following.progress).fields_rewritten
        == progress.fields_rewritten
    )
    job_queue.complete(db_session, root)
    last = drain(db_session, following, EmptyStorage())
    assert reencryption.Progress.model_validate(last.progress).summary == "complete"
    db_session.expunge_all()
    loaded = db_session.get(CanonicalCounterparty, party_id)
    assert loaded is not None and loaded.source_reference == IDENTIFIER


def test_first_connection_waits_for_earlier_plaintext_writer_and_indexes_it(
    db_session: Session,
    provider: LocalKeyProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = engine(db_session)
    db_session.commit()
    row_id = counterparty(db_session, include_package=False).id
    entered = Event()
    real_lock = maintenance.lock_bank

    def observed_lock(
        db: Session, organization: str, bank: str, *, exclusive: bool = False
    ) -> None:
        if exclusive:
            entered.set()
        real_lock(db, organization, bank, exclusive=exclusive)

    monkeypatch.setattr(maintenance, "lock_bank", observed_lock)

    def connection() -> None:
        with Session(database) as peer:
            connect(peer, provider)

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(connection)
        try:
            assert entered.wait(5) and not future.done()
        finally:
            db_session.commit()
        future.result(timeout=20)
    db_session.expire_all()
    token = blind_index(
        db_session,
        organization_id=ORG_1,
        bank_id=BANK_A,
        domain="counterparty.source_reference",
        value=IDENTIFIER,
    )
    assert (
        db_session.scalar(
            select(CanonicalCounterparty.source_reference_blind_index).where(
                CanonicalCounterparty.id == row_id
            )
        )
        == token
    )
    assert token != IDENTIFIER


def test_job_drains_writer_admitted_before_rotation_and_migrates_its_commit(
    db_session: Session,
    provider: LocalKeyProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = engine(db_session)
    connect(db_session, provider)
    late = counterparty(db_session, include_package=False)
    late_id = late.id
    with Session(database) as peer:
        key = registry.scoped_key(peer, organization_id=ORG_1, bank_id=BANK_A)
        registry.rotate(peer, key, NEXT, lambda _: provider)
        job_id = reencryption.enqueue(peer, ORG_1, BANK_A).id
        peer.commit()
    entered = Event()
    real_lock = maintenance.lock_bank

    def observed_lock(
        db: Session, organization: str, bank: str, *, exclusive: bool = False
    ) -> None:
        if exclusive:
            entered.set()
        real_lock(db, organization, bank, exclusive=exclusive)

    monkeypatch.setattr(maintenance, "lock_bank", observed_lock)

    def migrate() -> None:
        with Session(database) as peer:
            job = peer.get(Job, job_id)
            assert job is not None
            job = drain(peer, job, EmptyStorage())

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(migrate)
        try:
            assert entered.wait(5) and not future.done()
        finally:
            db_session.commit()
        future.result(timeout=30)
    db_session.expire_all()
    key = registry.bank_key(db_session, ORG_1, BANK_A)
    assert key is not None
    value = db_session.scalar(
        select(CanonicalCounterparty.name).where(CanonicalCounterparty.id == late_id)
    )
    assert isinstance(value, SealedField) and value.envelope_id == key.current_version_id


def test_reversal_refuses_import_committed_after_its_conflict_snapshot(
    db_session: Session,
    provider: LocalKeyProvider,
) -> None:
    database = engine(db_session)
    cp = counterparty(db_session, include_package=False)
    bank = db_session.get(Bank, BANK_A)
    assert bank is not None
    request = canonical_withdrawal.request_withdrawal(
        db_session,
        TenantContext(organization_id=ORG_1, actor_user_id=None, roles=("admin",)),
        bank,
        entity="counterparty",
        source_system=cp.source_system,
        as_of_date=cp.as_of_date,
        reason="Synthetic withdrawal",
        requested_by="Maker",
    )
    canonical_withdrawal.approve_withdrawal(
        db_session, CTX, bank, request.id, approved_by="Checker"
    )
    request_id = request.id
    db_session.commit()
    connect(db_session, provider)
    checked, imported = Event(), Event()

    def reverse() -> None:
        with Session(database) as peer:
            other_bank = peer.get(Bank, BANK_A)
            assert other_bank is not None
            connection = peer.connection()

            def after_query(
                _conn: object,
                _cursor: object,
                statement: str,
                _parameters: object,
                _context: object,
                _many: bool,
            ) -> None:
                if "count(" in statement and "canonical_counterparties AS" in statement:
                    checked.set()
                    assert imported.wait(15)

            event.listen(connection, "after_cursor_execute", after_query)
            with pytest.raises(WithdrawalError, match="concurrently replaced"):
                canonical_withdrawal.reverse_withdrawal(
                    peer,
                    CTX,
                    other_bank,
                    request_id,
                    reversed_by="Officer",
                    reason="Synthetic reversal",
                )
            peer.rollback()

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(reverse)
        try:
            assert checked.wait(10)
            counterparty(db_session, include_package=False)
            db_session.commit()
        finally:
            imported.set()
        future.result(timeout=20)
    assert (
        len(
            list(
                db_session.scalars(
                    select(CanonicalCounterparty).where(
                        CanonicalCounterparty.bank_id == BANK_A,
                        CanonicalCounterparty.withdrawn_at.is_(None),
                    )
                )
            )
        )
        == 1
    )
