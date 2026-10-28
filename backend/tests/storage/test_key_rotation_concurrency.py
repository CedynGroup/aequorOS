from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from app.core.key_management import registry
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
