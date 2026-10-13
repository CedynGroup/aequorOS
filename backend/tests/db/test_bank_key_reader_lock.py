from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from queue import Queue
from threading import Event, get_ident
from time import monotonic, sleep
from typing import cast

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.key_management import registry
from app.core.key_management.models import BankEncryptionKey, ObjectKeyEnvelope
from app.core.key_management.types import KeyReference, KeyStatus
from tests.storage.test_bank_encryption import (
    KEY,
    NEXT_KEY,
    SLUG,
    BankStorage,
    bank_storage,
)
from tests.support.helpers import ORG_1

__all__ = ["bank_storage"]


def test_rotation_waits_for_reader_unwrap_before_source_retirement(
    bank_storage: BankStorage,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bind = db_session.get_bind()
    if bind.dialect.name != "postgresql":
        pytest.skip("Requires disposable PostgreSQL and independent connections.")
    store = registry.DatabaseEnvelopeStore(
        lambda: Session(bind), lambda _name: bank_storage.provider
    )
    context = {"purpose": "synthetic-reader-race"}
    original = store.prepare(SLUG, context)
    entered, release = Event(), Event()
    writer_pid: Queue[int] = Queue()
    unwrap = bank_storage.provider.unwrap
    reader_thread: int | None = None

    def paused_unwrap(key: KeyReference, wrapped: bytes, context: dict[str, str]) -> bytes:
        if key == KEY and get_ident() == reader_thread and not release.is_set():
            entered.set()
            assert release.wait(10)
        return unwrap(key, wrapped, context)

    def read_object() -> registry.ObjectKey:
        nonlocal reader_thread
        reader_thread = get_ident()
        return store.open(SLUG, original.envelope_id, context)

    def rotate_then_retire() -> None:
        with Session(bind) as db, db.begin():
            pid = cast(object, db.scalar(text("SELECT pg_backend_pid()")))
            assert isinstance(pid, int)
            writer_pid.put(pid)
            row = registry.scoped_key(db, bank_id="BK-SAMP0001", organization_id=ORG_1)
            _ = db.execute(
                text("SELECT set_config('app.organization_id', :org, true)"), {"org": ORG_1}
            )
            registry.rotate(db, row, NEXT_KEY, lambda _name: bank_storage.provider)
        bank_storage.provider.set_status(KEY, KeyStatus.DISABLED)

    monkeypatch.setattr(bank_storage.provider, "unwrap", paused_unwrap)
    with ThreadPoolExecutor(max_workers=2) as executor:
        reader = executor.submit(read_object)
        try:
            assert entered.wait(10)
            writer = executor.submit(rotate_then_retire)
            pid = writer_pid.get(timeout=10)
            deadline = monotonic() + 10
            blocked = False
            while monotonic() < deadline and not writer.done():
                with Session(bind) as observer:
                    blocked = (
                        observer.scalar(
                            text(
                                "SELECT wait_event_type = 'Lock' FROM pg_stat_activity "
                                "WHERE pid = :pid"
                            ),
                            {"pid": pid},
                        )
                        is True
                    )
                if blocked:
                    break
                sleep(0.01)
            assert blocked and not writer.done()
        finally:
            release.set()
        assert reader.result(timeout=10).plaintext == original.plaintext
        writer.result(timeout=10)
    assert store.open(SLUG, original.envelope_id, context).plaintext == original.plaintext


def test_locked_rotation_refreshes_preloaded_reference_and_envelopes(
    bank_storage: BankStorage,
    db_session: Session,
) -> None:
    bind = db_session.get_bind()
    if bind.dialect.name != "postgresql":
        pytest.skip("Requires disposable PostgreSQL and independent connections.")
    store = registry.DatabaseEnvelopeStore(
        lambda: Session(bind), lambda _name: bank_storage.provider
    )
    context = {"purpose": "synthetic-identity-map"}
    original = store.prepare(SLUG, context)
    with Session(bind) as stale:
        _ = stale.execute(
            text("SELECT set_config('app.organization_id', :org, true)"), {"org": ORG_1}
        )
        old_row = stale.scalar(select(BankEncryptionKey))
        old_envelope = stale.scalar(select(ObjectKeyEnvelope))
        assert old_row is not None and old_envelope is not None
        wrapper = old_envelope.wrapped_key
        with Session(bind) as writer, writer.begin():
            _ = writer.execute(
                text("SELECT set_config('app.organization_id', :org, true)"), {"org": ORG_1}
            )
            row = registry.scoped_key(writer, bank_id="BK-SAMP0001", organization_id=ORG_1)
            registry.rotate(writer, row, NEXT_KEY, lambda _name: bank_storage.provider)
        refreshed = registry.scoped_key(stale, bank_id="BK-SAMP0001", organization_id=ORG_1)
        assert refreshed is old_row and refreshed.key_id == NEXT_KEY.key_id
        third = KeyReference(KEY.provider, KEY.key_id + "-third", KEY.region, KEY.owner_account)
        bank_storage.provider.add_key(third)
        assert registry.rotate(stale, refreshed, third, lambda _name: bank_storage.provider) == 1
        stale.commit()
        assert old_envelope.wrapped_key != wrapper
    assert store.open(SLUG, original.envelope_id, context).plaintext == original.plaintext
