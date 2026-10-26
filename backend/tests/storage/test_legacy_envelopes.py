from __future__ import annotations

import io
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app.core.key_management import registry, sdk
from app.core.key_management.models import ObjectKeyEnvelope
from app.core.key_management.types import KeyStatus
from app.storage.client import StorageAccessError, StorageLocation, Tier
from app.storage.encryption import ENCRYPTION_FORMAT, ObjectEncryption, object_context
from tests.storage.contract import metadata_for
from tests.storage.test_bank_encryption import KEY, NEXT_KEY, SLUG, BankStorage, bank_storage
from tests.support.helpers import ORG_1

__all__ = ["bank_storage"]


@pytest.mark.parametrize("tier", ["raw", "canonical", "outputs", "temp"])
def test_legacy_object_keys_keep_original_derivation_through_rotation(
    bank_storage: BankStorage, db_session: Session, tier: Tier
) -> None:
    provider = bank_storage.provider
    location = StorageLocation(SLUG, tier, "legacy/synthetic.bin")
    content = b"synthetic legacy payload"
    metadata = metadata_for(SLUG, tier, content)
    context = object_context(location, metadata.checksum_sha256)
    digest = registry.context_digest(context)
    data = provider.generate_data_key(KEY, registry.wrapping_context("BK-SAMP0001", digest))
    envelope_id = uuid4()
    db_session.add(
        ObjectKeyEnvelope(
            id=envelope_id,
            bank_id="BK-SAMP0001",
            organization_id=ORG_1,
            context_digest=digest,
            wrapped_key=data.wrapped,
            provider=KEY.provider,
            key_id=KEY.key_id,
            region=KEY.region,
            owner_account=KEY.owner_account,
        )
    )
    db_session.commit()
    with sdk.transform_file(io.BytesIO(content), data.plaintext, context) as body:
        ciphertext = body.read()
    encryption = ObjectEncryption(
        registry.DatabaseEnvelopeStore(
            lambda: Session(db_session.get_bind(), join_transaction_mode="create_savepoint"),
            lambda _name: provider,
        )
    )
    headers = {"encryption-format": ENCRYPTION_FORMAT, "key-envelope-id": str(envelope_id)}
    for rotated in (False, True):
        if rotated:
            row = registry.scoped_key(db_session, bank_id="BK-SAMP0001", organization_id=ORG_1)
            registry.rotate(db_session, row, NEXT_KEY, lambda _name: provider)
            db_session.commit()
        with encryption.decrypt(location, io.BytesIO(ciphertext), metadata, headers) as restored:
            assert restored.read() == content
        wrong = StorageLocation(SLUG, tier, "legacy/other.bin")
        with pytest.raises(StorageAccessError):
            encryption.decrypt(wrong, io.BytesIO(ciphertext), metadata, headers)
        with pytest.raises(StorageAccessError):
            encryption.decrypt(
                StorageLocation("other-bank", tier, location.object_path),
                io.BytesIO(ciphertext),
                metadata,
                headers,
            )
        with pytest.raises(StorageAccessError):
            encryption.decrypt(location, io.BytesIO(ciphertext[:-1]), metadata, headers)
    new_body, new_id, new_key = encryption.encrypt(location, io.BytesIO(content), metadata)
    with new_body:
        new_ciphertext = new_body.read()
    assert new_id is not None and new_id != str(envelope_id) and new_key == NEXT_KEY.key_id
    new_headers = {"encryption-format": ENCRYPTION_FORMAT, "key-envelope-id": new_id}
    provider.set_status(KEY, KeyStatus.DISABLED)
    with encryption.decrypt(
        location, io.BytesIO(new_ciphertext), metadata, new_headers
    ) as restored:
        assert restored.read() == content
    with pytest.raises(StorageAccessError):
        encryption.decrypt(location, io.BytesIO(ciphertext), metadata, headers)
