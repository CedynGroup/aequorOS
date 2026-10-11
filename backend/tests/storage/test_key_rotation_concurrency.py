from __future__ import annotations

import io
from concurrent.futures import ThreadPoolExecutor
from queue import Queue
from threading import Event
from time import monotonic, sleep
from typing import Protocol, cast

import pytest
from sqlalchemy import Connection, Engine, event, select, text
from sqlalchemy.orm import Session, SessionTransaction

from app.core.key_management import registry
from app.core.key_management.models import BankEncryptionKey, ObjectKeyEnvelope
from app.core.key_management.types import KeyReference, KeyStatus
from app.storage.client import StorageLocation
from tests.storage.contract import metadata_for
from tests.storage.test_bank_encryption import KEY, NEXT_KEY, SLUG, BankStorage, bank_storage
from tests.support.helpers import ORG_1

__all__ = ["bank_storage"]
pytestmark = pytest.mark.committing_db


class _Body(Protocol):
    def read(self) -> bytes: ...


def _wait_for_row_lock(database: Engine, pid: int) -> None:
    deadline = monotonic() + 10
    while monotonic() < deadline:
        with Session(database) as observer:
            blocked = cast(
                object,
                observer.scalar(
                    text("SELECT wait_event_type = 'Lock' FROM pg_stat_activity WHERE pid = :pid"),
                    {"pid": pid},
                ),
            )
        if blocked is True:
            return
        sleep(0.01)
    pytest.fail("The operation did not wait for the bank-key row lock.")


def _observed_session(database: Engine, waiting_pids: Queue[int]) -> Session:
    db = Session(database)

    def record_pid(
        _session: Session, _transaction: SessionTransaction, connection: Connection
    ) -> None:
        pid = cast(object, connection.scalar(text("SELECT pg_backend_pid()")))
        assert isinstance(pid, int)
        waiting_pids.put(pid)

    event.listen(db, "after_begin", record_pid)
    return db


def test_rotation_resumes_waiting_readers_and_new_writers_without_reencrypting_files(
    bank_storage: BankStorage,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = db_session.get_bind()
    if not isinstance(database, Engine) or database.dialect.name != "postgresql":
        pytest.skip("PostgreSQL row locks and independent connections are required.")
    location = StorageLocation(SLUG, "outputs", "synthetic/rotation.pdf")
    content = b"synthetic uploaded file"
    descriptor = bank_storage.storage.write(
        location, io.BytesIO(content), metadata_for(SLUG, "outputs", content)
    )
    before = bank_storage.s3.get_object(
        Bucket=location.bucket_name("dev"), Key=location.object_path
    )
    ciphertext = cast(_Body, before["Body"]).read()
    provider = bank_storage.provider
    store = registry.DatabaseEnvelopeStore(lambda: Session(database), lambda _name: provider)
    context = {"purpose": "synthetic-concurrent-object"}
    original = store.prepare(SLUG, context)
    entered, release = Event(), Event()
    waiting_pids: Queue[int] = Queue()
    rotate = provider.rotate

    def paused_rotation(
        source: KeyReference,
        destination: KeyReference,
        wrapped: bytes,
        context: dict[str, str],
    ) -> bytes:
        replacement = rotate(source, destination, wrapped, context)
        entered.set()
        if not release.wait(15):
            raise RuntimeError("The test did not release the rotation.")
        return replacement

    def rotation() -> int:
        with Session(database) as db, db.begin():
            _ = db.execute(
                text("SELECT set_config('app.organization_id', :org, true)"), {"org": ORG_1}
            )
            row = registry.scoped_key(db, bank_id="BK-SAMP0001", organization_id=ORG_1)
            return registry.rotate(db, row, NEXT_KEY, lambda _name: provider)

    monkeypatch.setattr(provider, "rotate", paused_rotation)
    waiting = registry.DatabaseEnvelopeStore(
        lambda: _observed_session(database, waiting_pids), lambda _name: provider
    )
    with ThreadPoolExecutor(max_workers=3) as pool:
        rotating = pool.submit(rotation)
        try:
            assert entered.wait(10)
            reading = pool.submit(waiting.open, SLUG, original.envelope_id, context)
            writing = pool.submit(waiting.prepare, SLUG, {"purpose": "synthetic-new-object"})
            for _ in range(2):
                _wait_for_row_lock(database, waiting_pids.get(timeout=10))
            assert not reading.done() and not writing.done()
        finally:
            release.set()
        assert rotating.result(timeout=10) == 2
        result = reading.result(timeout=10)
        assert result.plaintext == original.plaintext and result.key_id == NEXT_KEY.key_id
        assert writing.result(timeout=10).key_id == NEXT_KEY.key_id
    provider.set_status(KEY, KeyStatus.DISABLED)
    assert store.open(SLUG, original.envelope_id, context).plaintext == original.plaintext
    after = bank_storage.s3.get_object(Bucket=location.bucket_name("dev"), Key=location.object_path)
    assert cast(_Body, after["Body"]).read() == ciphertext
    assert after["VersionId"] == descriptor.version_id
    _, downloaded = bank_storage.storage.read(location, descriptor.version_id)
    with downloaded:
        assert downloaded.read() == content


def test_read_refreshes_preloaded_reference_and_wrapper_after_rotation(
    bank_storage: BankStorage,
    db_session: Session,
) -> None:
    database = db_session.get_bind()
    if not isinstance(database, Engine) or database.dialect.name != "postgresql":
        pytest.skip("PostgreSQL committed snapshots and independent connections are required.")
    provider = bank_storage.provider
    store = registry.DatabaseEnvelopeStore(lambda: Session(database), lambda _name: provider)
    context = {"purpose": "synthetic-stale-reader"}
    original = store.prepare(SLUG, context)
    stale = Session(database, expire_on_commit=False)
    try:
        _ = stale.execute(
            text("SELECT set_config('app.organization_id', :org, true)"), {"org": ORG_1}
        )
        old_reference = stale.scalar(
            select(BankEncryptionKey).where(BankEncryptionKey.bank_id == "BK-SAMP0001")
        )
        old_envelope = stale.scalar(
            select(ObjectKeyEnvelope).where(ObjectKeyEnvelope.id == original.envelope_id)
        )
        assert old_reference is not None and old_envelope is not None
        wrapped = old_envelope.wrapped_key
        stale.commit()
        with Session(database) as db, db.begin():
            _ = db.execute(
                text("SELECT set_config('app.organization_id', :org, true)"), {"org": ORG_1}
            )
            row = registry.scoped_key(db, bank_id="BK-SAMP0001", organization_id=ORG_1)
            assert registry.rotate(db, row, NEXT_KEY, lambda _name: provider) == 1
        assert old_reference.key_id == KEY.key_id and old_envelope.wrapped_key == wrapped
        provider.set_status(KEY, KeyStatus.DISABLED)
        reading = registry.DatabaseEnvelopeStore(lambda: stale, lambda _name: provider)
        result = reading.open(SLUG, original.envelope_id, context)
        assert result.key_id == NEXT_KEY.key_id and result.plaintext == original.plaintext
        assert old_reference.key_id == NEXT_KEY.key_id
        assert old_envelope.wrapped_key != wrapped
    finally:
        stale.close()
