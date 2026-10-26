from __future__ import annotations

import io
import os
from collections.abc import Iterator
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from alembic import command
from app.core.key_management import registry, sdk
from app.core.key_management.local import LocalKeyProvider
from app.core.key_management.models import BankEncryptionKey, ObjectKeyEnvelope
from app.core.key_management.schemas import BankKeyConnect
from app.core.key_management.types import DataKey, KeyStatus
from app.core.tenancy import TenantContext
from app.db.base import utc_now
from app.identity.public import Bank
from app.identity.service import bank_encryption
from app.models.organization import Organization
from app.storage.client import StorageAccessError, StorageLocation
from app.storage.encryption import ENCRYPTION_FORMAT, ObjectEncryption, object_context
from tests.db import test_postgres_migrations as migrations
from tests.storage.contract import metadata_for
from tests.storage.test_bank_encryption import KEY, NEXT_KEY, SLUG
from tests.support.helpers import ORG_1

pytestmark = pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="PostgreSQL required")
BANK = "BK-SAMP0001"
CTX = TenantContext(organization_id=ORG_1)


@pytest.fixture
def legacy_schema(monkeypatch: pytest.MonkeyPatch) -> Iterator[migrations.MigratedPostgresSchema]:
    yield from migrations.migrate_isolated_postgres_schema(
        monkeypatch, downgrade=False, revision="202610220101"
    )


def test_empty_key_version_migration_round_trip(
    legacy_schema: migrations.MigratedPostgresSchema,
) -> None:
    config = migrations.alembic_config_for_app()
    command.upgrade(config, "202610220102")
    command.downgrade(config, "202610220101")
    command.upgrade(config, "head")


def _seed_legacy_envelopes(
    db: Session, provider: LocalKeyProvider, contexts: tuple[dict[str, str], ...]
) -> list[tuple[UUID, DataKey]]:
    envelopes: list[tuple[UUID, DataKey]] = []
    db.info["organization_id"] = ORG_1
    db.add(Organization(id=ORG_1, name="Synthetic migration"))
    db.flush()
    db.add(
        Bank(
            id=BANK,
            organization_id=ORG_1,
            name="Synthetic",
            short_name="Synthetic",
            currency="GHS",
            jurisdiction_code="GH",
            institution_type="universal_bank",
            license_type="universal_bank",
            storage_slug=SLUG,
        )
    )
    db.flush()
    db.execute(
        text("""
        INSERT INTO bank_encryption_keys
        (id, bank_id, organization_id, storage_slug, provider, key_id, region, owner_account,
         status, checked_at, created_at, updated_at)
        VALUES (:id, :bank, :org, :slug, :provider, :key, :region, :owner, 'active', :now, :now, :now)
    """),
        dict(
            id=uuid4(),
            bank=BANK,
            org=ORG_1,
            slug=SLUG,
            provider=KEY.provider,
            key=KEY.key_id,
            region=KEY.region,
            owner=KEY.owner_account,
            now=utc_now(),
        ),
    )
    for legacy_context in contexts:
        identifier = uuid4()
        digest = registry.context_digest(legacy_context)
        data = provider.generate_data_key(KEY, registry.wrapping_context(BANK, digest))
        db.execute(
            text("""
            INSERT INTO object_key_envelopes
            (id, bank_id, organization_id, context_digest, wrapped_key, created_at)
            VALUES (:id, :bank, :org, :digest, :wrapped, :now)
        """),
            dict(
                id=identifier,
                bank=BANK,
                org=ORG_1,
                digest=digest,
                wrapped=data.wrapped,
                now=utc_now(),
            ),
        )
        envelopes.append((identifier, data))
    db.commit()
    return envelopes


def test_populated_legacy_envelopes_upgrade_and_initialize_without_payload_rewrite(
    legacy_schema: migrations.MigratedPostgresSchema, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = legacy_schema.app_engine
    provider = LocalKeyProvider()
    provider.add_key(KEY)
    provider.add_key(NEXT_KEY)
    monkeypatch.setattr(registry, "provider_for", lambda _name: provider)
    location = StorageLocation(SLUG, "raw", "legacy/synthetic.csv")
    content = b"synthetic migrated payload"
    metadata = metadata_for(SLUG, "raw", content)
    context = object_context(location, metadata.checksum_sha256)
    index_context = {
        "purpose": "bank-sensitive-index-v1",
        "organization_id": ORG_1,
        "bank_id": BANK,
    }
    with Session(engine) as db:
        envelopes = _seed_legacy_envelopes(db, provider, (context, index_context))
    with sdk.transform_file(io.BytesIO(content), envelopes[0][1].plaintext, context) as body:
        ciphertext = body.read()
    config = migrations.alembic_config_for_app()
    command.upgrade(config, "202610220102")
    with pytest.raises(RuntimeError, match="prohibits schema downgrade"):
        command.downgrade(config, "202610220101")
    command.upgrade(config, "head")
    with Session(engine) as db:
        db.info["organization_id"] = ORG_1
        for identifier, data in envelopes:
            envelope = db.get(ObjectKeyEnvelope, identifier)
            assert envelope is not None
            assert registry.reference(envelope) == KEY
            assert envelope.wrapped_key == data.wrapped
        payload = BankKeyConnect(
            key_id=KEY.key_id, region=KEY.region, owner_account=KEY.owner_account
        )
        result = bank_encryption.test_connection(db, CTX, BANK, payload)
        assert result.passed
        row = db.scalar(select(BankEncryptionKey).where(BankEncryptionKey.bank_id == BANK))
        assert row is not None and row.current_version_id is not None
        assert row.index_version_id == envelopes[1][0]
        assert (
            registry.open_envelope(
                db, row, row.index_version_id, index_context, lambda _name: provider
            ).plaintext
            == envelopes[1][1].plaintext
        )
        current = row.current_version_id
        registry.rotate(db, row, NEXT_KEY, lambda _name: provider)
        db.rollback()
        restored_key = db.get(BankEncryptionKey, row.id)
        assert restored_key is not None and restored_key.current_version_id == current
    encryption = ObjectEncryption(
        registry.DatabaseEnvelopeStore(lambda: Session(engine), lambda _name: provider)
    )
    headers = {"encryption-format": ENCRYPTION_FORMAT, "key-envelope-id": str(envelopes[0][0])}
    with encryption.decrypt(location, io.BytesIO(ciphertext), metadata, headers) as restored:
        assert restored.read() == content
    with encryption.encrypt(location, io.BytesIO(content), metadata)[0] as written:
        assert written.read() != ciphertext
    provider.set_status(KEY, KeyStatus.UNAVAILABLE)
    with pytest.raises(StorageAccessError):
        encryption.decrypt(location, io.BytesIO(ciphertext), metadata, headers)
