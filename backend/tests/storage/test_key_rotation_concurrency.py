from __future__ import annotations

from collections.abc import Iterable, Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Event, Lock
from typing import cast

import pytest
from sqlalchemy import Connection, Engine, event, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, SessionTransaction, UOWTransaction

from app.core.key_management import registry
from app.core.key_management.models import ObjectKeyEnvelope
from app.core.key_management.types import KeyUnavailableError
from tests.storage.test_bank_encryption import KEY, NEXT_KEY, SLUG, BankStorage, bank_storage
from tests.support.helpers import ORG_1

__all__ = ["bank_storage"]
pytestmark = pytest.mark.committing_db


def test_rotation_does_not_wait_for_an_active_reader(
    bank_storage: BankStorage, db_session: Session
) -> None:
    database = db_session.get_bind()
    if not isinstance(database, Engine) or database.dialect.name != "postgresql":
        pytest.skip("PostgreSQL independent connections are required.")
    store = registry.DatabaseEnvelopeStore(
        lambda: Session(database), lambda _name: bank_storage.provider
    )
    context = {"purpose": "synthetic-reader"}
    original = store.prepare(SLUG, context)
    reader = store.for_session(db_session)
    assert reader.open(SLUG, original.envelope_id, context) == original
    db_session.flush()

    def rotate() -> None:
        with Session(database) as writer, writer.begin():
            _ = writer.execute(text("SET LOCAL lock_timeout='1s'"))
            row = registry.scoped_key(writer, bank_id="BK-SAMP0001", organization_id=ORG_1)
            registry.rotate(writer, row, NEXT_KEY, lambda _name: bank_storage.provider)

    with ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(rotate).result(timeout=5)
    assert reader.prepare(SLUG, context).envelope_id == original.envelope_id
    db_session.commit()
    assert reader.prepare(SLUG, context).envelope_id != original.envelope_id
    assert store.open(SLUG, original.envelope_id, context) == original


def test_readers_do_not_wait_for_uncommitted_rotation(
    bank_storage: BankStorage, db_session: Session
) -> None:
    database = db_session.get_bind()
    if not isinstance(database, Engine) or database.dialect.name != "postgresql":
        pytest.skip("PostgreSQL independent connections are required.")
    store = registry.DatabaseEnvelopeStore(
        lambda: Session(database), lambda _name: bank_storage.provider
    )
    context = {"purpose": "synthetic-rotation"}
    original = store.prepare(SLUG, context)
    entered, release = Event(), Event()

    def rotate() -> None:
        with Session(database) as writer, writer.begin():
            row = registry.scoped_key(writer, bank_id="BK-SAMP0001", organization_id=ORG_1)
            registry.rotate(writer, row, NEXT_KEY, lambda _name: bank_storage.provider)
            entered.set()
            assert release.wait(10)

    with ThreadPoolExecutor(max_workers=1) as pool:
        rotating = pool.submit(rotate)
        try:
            assert entered.wait(5)
            assert store.open(SLUG, original.envelope_id, context) == original
            assert store.prepare(SLUG, context).envelope_id == original.envelope_id
        finally:
            release.set()
        rotating.result(timeout=5)
    current = store.prepare(SLUG, context)
    assert current.envelope_id != original.envelope_id and current.key_id == NEXT_KEY.key_id
    with Session(database) as db:
        row = registry.scoped_key(db, bank_id="BK-SAMP0001", organization_id=ORG_1)
        with pytest.raises(
            KeyUnavailableError, match="indefinite|retained encryption versions|retention expires"
        ):
            registry.authorize_retirement(db, row, KEY)


@pytest.fixture(autouse=True)
def completed_usage(db_session: Session, monkeypatch: pytest.MonkeyPatch) -> Iterator[Lock]:
    recording = Lock()
    monkeypatch.setattr(registry, "_usage_recording", recording)
    yield recording
    # End caller work before restoring the lock used by asynchronous usage writers.
    db_session.rollback()
    assert recording.acquire(timeout=5)
    recording.release()


def test_usage_recording_cannot_delay_same_bank_operations_or_rotation(
    bank_storage: BankStorage, db_session: Session, completed_usage: Lock
) -> None:
    database = db_session.get_bind()
    if not isinstance(database, Engine) or database.dialect.name != "postgresql":
        pytest.skip("PostgreSQL independent connections are required.")
    store = registry.DatabaseEnvelopeStore(
        lambda: Session(database), lambda _name: bank_storage.provider
    )
    entered, release = Event(), Event()

    def pause_usage(peer: Session, _flush: UOWTransaction) -> None:
        dirty = cast(Iterable[object], peer.dirty)
        if any(isinstance(row, ObjectKeyEnvelope) for row in dirty):
            entered.set()
            assert release.wait(10)

    event.listen(Session, "after_flush", pause_usage)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(store.prepare, SLUG, {"purpose": "first"}).result(timeout=5)
            assert entered.wait(5)
            second = pool.submit(store.prepare, SLUG, {"purpose": "second"}).result(timeout=5)
            assert second.envelope_id == first.envelope_id
            assert (
                pool.submit(store.open, SLUG, first.envelope_id, {"purpose": "first"}).result(
                    timeout=5
                )
                == first
            )

            def rotate() -> None:
                with Session(database) as writer, writer.begin():
                    _ = writer.execute(text("SET LOCAL lock_timeout='100ms'"))
                    row = registry.scoped_key(writer, bank_id="BK-SAMP0001", organization_id=ORG_1)
                    registry.rotate(writer, row, NEXT_KEY, lambda _name: bank_storage.provider)

            pool.submit(rotate).result(timeout=5)
    finally:
        release.set()
        assert completed_usage.acquire(timeout=5)
        completed_usage.release()
        event.remove(Session, "after_flush", pause_usage)
    with Session(database) as peer:
        assert (
            peer.scalar(
                select(ObjectKeyEnvelope.last_successful_use_at).where(
                    ObjectKeyEnvelope.id == first.envelope_id
                )
            )
            is not None
        )
    assert store.prepare(SLUG, {"purpose": "rotated"}).key_id == NEXT_KEY.key_id


def test_usage_is_recorded_after_completion_and_throttled(
    bank_storage: BankStorage, db_session: Session, completed_usage: Lock
) -> None:
    database = db_session.get_bind()
    if not isinstance(database, Engine):
        pytest.skip("Independent connections are required.")
    context = {"purpose": "usage-completion"}
    store = registry.DatabaseEnvelopeStore(
        lambda: Session(database), lambda _name: bank_storage.provider
    )
    with Session(database) as caller:
        bound = store.for_session(caller)
        key = bound.prepare(SLUG, context)
        caller.flush()
        with Session(database) as peer:
            assert (
                peer.scalar(
                    select(ObjectKeyEnvelope.last_successful_use_at).where(
                        ObjectKeyEnvelope.id == key.envelope_id
                    )
                )
                is None
            )
        caller.commit()
    assert completed_usage.acquire(timeout=5)
    completed_usage.release()
    with Session(database) as peer:
        recorded = peer.scalar(
            select(ObjectKeyEnvelope.last_successful_use_at).where(
                ObjectKeyEnvelope.id == key.envelope_id
            )
        )
        assert recorded is not None
        bank = registry.bank_key(peer, ORG_1, "BK-SAMP0001")
        assert bank is not None
        assert registry.last_successful_use(peer, bank) == recorded
    assert store.open(SLUG, key.envelope_id, context) == key
    assert completed_usage.acquire(timeout=5)
    completed_usage.release()
    with Session(database) as peer, peer.begin():
        envelope = peer.get(ObjectKeyEnvelope, key.envelope_id)
        assert envelope is not None
        assert envelope.last_successful_use_at == recorded
        envelope.last_successful_use_at = datetime.now(UTC) - timedelta(minutes=6)
    assert store.open(SLUG, key.envelope_id, context) == key
    assert completed_usage.acquire(timeout=5)
    completed_usage.release()
    with Session(database) as peer:
        refreshed = peer.scalar(
            select(ObjectKeyEnvelope.last_successful_use_at).where(
                ObjectKeyEnvelope.id == key.envelope_id
            )
        )
        assert refreshed is not None and refreshed > recorded


def test_usage_database_failure_does_not_fail_encryption(
    bank_storage: BankStorage, db_session: Session, completed_usage: Lock
) -> None:
    row = registry.bank_key(db_session, ORG_1, "BK-SAMP0001")
    assert row is not None
    key = registry.prepare_envelope(
        db_session, row, {"purpose": "telemetry-outage"}, lambda _: bank_storage.provider
    )
    failed = Event()

    def refuse_usage(
        peer: Session, _transaction: SessionTransaction, _connection: Connection
    ) -> None:
        if peer is not db_session:
            failed.set()
            raise SQLAlchemyError("Synthetic telemetry database outage")

    event.listen(Session, "after_begin", refuse_usage)
    try:
        db_session.commit()
        assert failed.wait(5)
        assert completed_usage.acquire(timeout=5)
        completed_usage.release()
    finally:
        event.remove(Session, "after_begin", refuse_usage)
    assert len(key.plaintext) == 32
    with Session(db_session.get_bind()) as peer:
        assert (
            peer.scalar(
                select(ObjectKeyEnvelope.last_successful_use_at).where(
                    ObjectKeyEnvelope.id == key.envelope_id
                )
            )
            is None
        )
