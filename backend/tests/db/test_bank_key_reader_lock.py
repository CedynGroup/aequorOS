from __future__ import annotations

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from app.core.key_management import registry
from app.core.key_management.models import ObjectKeyEnvelope
from tests.storage.test_bank_encryption import KEY, NEXT_KEY, SLUG, BankStorage, bank_storage
from tests.support.helpers import ORG_1

__all__ = ["bank_storage"]
pytestmark = pytest.mark.committing_db


def test_rotation_keeps_preloaded_envelope_immutable(
    bank_storage: BankStorage, db_session: Session
) -> None:
    database = db_session.get_bind()
    if not isinstance(database, Engine) or database.dialect.name != "postgresql":
        pytest.skip("PostgreSQL independent connections are required.")
    store = registry.DatabaseEnvelopeStore(
        lambda: Session(database), lambda _name: bank_storage.provider
    )
    context = {"purpose": "synthetic-identity-map"}
    original = store.prepare(SLUG, context)
    envelope = db_session.scalar(
        select(ObjectKeyEnvelope).where(ObjectKeyEnvelope.id == original.envelope_id)
    )
    assert envelope is not None
    wrapper = envelope.wrapped_key
    with Session(database) as writer, writer.begin():
        row = registry.scoped_key(writer, bank_id="BK-SAMP0001", organization_id=ORG_1)
        assert registry.rotate(writer, row, NEXT_KEY, lambda _name: bank_storage.provider) == 0
    db_session.refresh(envelope)
    assert envelope.wrapped_key == wrapper and envelope.key_id == KEY.key_id
    assert store.open(SLUG, original.envelope_id, context) == original
    assert store.prepare(SLUG, context).key_id == NEXT_KEY.key_id
