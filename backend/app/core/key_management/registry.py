from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import UUID, uuid4

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.key_management.aws import AwsKmsKeyProvider
from app.core.key_management.models import BankEncryptionKey, ObjectKeyEnvelope, RetainedBankKey
from app.core.key_management.settings import get_key_settings
from app.core.key_management.types import (
    KeyDescription,
    KeyIntegrityError,
    KeyProvider,
    KeyReference,
    KeyStatus,
    KeyUnavailableError,
)
from app.db.base import utc_now

type ProviderFactory = Callable[[str], KeyProvider]


def provider_for(provider: str) -> KeyProvider:
    if provider != "aws_kms":
        raise KeyUnavailableError("The bank encryption key provider is not supported.")
    return AwsKmsKeyProvider()


def reference(row: BankEncryptionKey) -> KeyReference:
    return KeyReference(row.provider, row.key_id, row.region, row.owner_account)


def require_active(description: KeyDescription) -> KeyReference:
    if description.status != KeyStatus.ACTIVE:
        raise KeyUnavailableError("The bank encryption key is unavailable.")
    return description.reference


def register(  # noqa: PLR0913 - explicit ownership and bank identity at the registration boundary
    db: Session,
    *,
    bank_id: str,
    organization_id: str,
    storage_slug: str,
    key: KeyReference,
    providers: ProviderFactory = provider_for,
) -> BankEncryptionKey:
    _lock_keys(db, key)
    _require_new_encryption_key(db, key)
    resolved = require_active(providers(key.provider).describe(key))
    context = {"bank_id": bank_id, "purpose": "onboarding-key-probe"}
    provider = providers(resolved.provider)
    probe = provider.generate_data_key(resolved, context)
    if provider.unwrap(resolved, probe.wrapped, context) != probe.plaintext:
        raise KeyIntegrityError("The bank key failed its wrap/unwrap probe.")
    row = BankEncryptionKey(
        bank_id=bank_id,
        organization_id=organization_id,
        storage_slug=storage_slug,
        provider=resolved.provider,
        key_id=resolved.key_id,
        region=resolved.region,
        owner_account=resolved.owner_account,
        status=KeyStatus.ACTIVE.value,
        checked_at=utc_now(),
    )
    db.add(row)
    db.flush()
    return row


def scoped_key(db: Session, *, bank_id: str, organization_id: str) -> BankEncryptionKey:
    row = db.scalar(
        select(BankEncryptionKey)
        .where(
            BankEncryptionKey.bank_id == bank_id,
            BankEncryptionKey.organization_id == organization_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if row is None:
        raise KeyUnavailableError("The bank encryption key is not connected.")
    return row


def rotate(
    db: Session,
    row: BankEncryptionKey,
    destination: KeyReference,
    providers: ProviderFactory = provider_for,
) -> int:
    source = reference(row)
    _lock_keys(db, source, destination)
    if source == destination:
        raise KeyUnavailableError("Rotation requires a different key.")
    _require_new_encryption_key(db, destination)
    if destination.owner_account != source.owner_account:
        raise KeyUnavailableError("Rotation must retain the bank's key ownership account.")
    target = require_active(providers(destination.provider).describe(destination))
    if source.provider != target.provider:
        raise KeyUnavailableError("Changing key providers requires an explicit migration.")
    provider = providers(source.provider)
    probe_context = {"bank_id": row.bank_id, "purpose": "rotation-key-probe"}
    probe = provider.generate_data_key(target, probe_context)
    if provider.unwrap(target, probe.wrapped, probe_context) != probe.plaintext:
        raise KeyIntegrityError("The replacement bank key failed its wrap/unwrap probe.")
    count = 0
    for envelope in db.scalars(
        select(ObjectKeyEnvelope)
        .where(
            ObjectKeyEnvelope.bank_id == row.bank_id,
            ObjectKeyEnvelope.organization_id == row.organization_id,
        )
        .execution_options(populate_existing=True)
    ):
        envelope.wrapped_key = provider.rotate(
            source,
            target,
            envelope.wrapped_key,
            wrapping_context(row.bank_id, envelope.context_digest),
        )
        count += 1
    rotated_at = utc_now()
    retention = get_key_settings().backup_retention_days
    db.add(
        RetainedBankKey(
            bank_id=row.bank_id,
            organization_id=row.organization_id,
            provider=source.provider,
            key_id=source.key_id,
            region=source.region,
            owner_account=source.owner_account,
            rotated_at=rotated_at,
            decrypt_until=rotated_at + timedelta(days=retention) if retention is not None else None,
        )
    )
    row.key_id = target.key_id
    row.region = target.region
    row.status = KeyStatus.ACTIVE.value
    row.checked_at = utc_now()
    db.flush()
    return count


def context_digest(context: dict[str, str]) -> str:
    return hashlib.sha256(json.dumps(context, sort_keys=True).encode()).hexdigest()


def wrapping_context(bank_id: str, digest: str) -> dict[str, str]:
    return {"bank_id": bank_id, "context_digest": digest, "purpose": "object-data-key"}


@dataclass(frozen=True)
class ObjectKey:
    envelope_id: UUID
    key_id: str
    plaintext: bytes = field(repr=False)


class EnvelopeStore(Protocol):
    def prepare(self, slug: str, context: dict[str, str]) -> ObjectKey: ...

    def open(self, slug: str, envelope_id: UUID, context: dict[str, str]) -> ObjectKey: ...


class DatabaseEnvelopeStore:
    def __init__(
        self, sessions: Callable[[], Session], providers: ProviderFactory = provider_for
    ) -> None:
        self._sessions = sessions
        self._providers = providers

    @staticmethod
    def _bank(db: Session, slug: str, *, read: bool = False) -> BankEncryptionKey:
        row = db.scalar(
            select(BankEncryptionKey)
            .where(BankEncryptionKey.storage_slug == slug)
            .with_for_update(read=read)
            .execution_options(populate_existing=True)
        )
        if row is None:
            raise KeyUnavailableError("The bank encryption key is not connected.")
        return row

    def prepare(self, slug: str, context: dict[str, str]) -> ObjectKey:
        with self._sessions() as db, db.begin():
            row = self._bank(db, slug)
            _lock_keys(db, reference(row))
            _require_new_encryption_key(db, reference(row))
            _scope_envelopes(db, row.organization_id)
            digest = context_digest(context)
            data_key = self._providers(row.provider).generate_data_key(
                reference(row), wrapping_context(row.bank_id, digest)
            )
            envelope_id = uuid4()
            db.add(
                ObjectKeyEnvelope(
                    id=envelope_id,
                    bank_id=row.bank_id,
                    organization_id=row.organization_id,
                    context_digest=digest,
                    wrapped_key=data_key.wrapped,
                )
            )
            return ObjectKey(envelope_id, row.key_id, data_key.plaintext)

    def open(self, slug: str, envelope_id: UUID, context: dict[str, str]) -> ObjectKey:
        with self._sessions() as db, db.begin():
            row = self._bank(db, slug, read=True)
            _scope_envelopes(db, row.organization_id)
            envelope = db.scalar(
                select(ObjectKeyEnvelope)
                .where(
                    ObjectKeyEnvelope.bank_id == row.bank_id,
                    ObjectKeyEnvelope.organization_id == row.organization_id,
                    ObjectKeyEnvelope.id == envelope_id,
                )
                .execution_options(populate_existing=True)
            )
            if envelope is None or envelope.context_digest != context_digest(context):
                raise KeyIntegrityError("The object key does not belong to this bank and object.")
            plaintext = self._providers(row.provider).unwrap(
                reference(row),
                envelope.wrapped_key,
                wrapping_context(row.bank_id, envelope.context_digest),
            )
            return ObjectKey(envelope_id, row.key_id, plaintext)


def _scope_envelopes(db: Session, organization_id: str) -> None:
    db.info["organization_id"] = organization_id
    # Resolving the pre-tenant registry already began the transaction, so
    # after_begin ran before the organization was known.
    if db.get_bind().dialect.name == "postgresql":
        _ = db.execute(
            text("SELECT set_config('app.organization_id', :organization_id, true)"),
            {"organization_id": organization_id},
        )


def _lock_keys(db: Session, *keys: KeyReference) -> None:
    if db.get_bind().dialect.name == "postgresql":
        identities = sorted({(key.provider, key.key_id) for key in keys})
        for identity in identities:
            value = int.from_bytes(
                hashlib.sha256(json.dumps(identity).encode()).digest()[:8], signed=True
            )
            _ = db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": value})


def _require_new_encryption_key(db: Session, key: KeyReference) -> None:
    if (
        db.scalar(
            select(RetainedBankKey.id)
            .where(
                RetainedBankKey.provider == key.provider,
                RetainedBankKey.key_id == key.key_id,
            )
            .limit(1)
        )
        is not None
    ):
        raise KeyUnavailableError("A rotated-out key cannot be used for new encryption.")


def authorize_retirement(db: Session, row: BankEncryptionKey, key: KeyReference) -> None:
    _lock_keys(db, key)
    held = db.scalar(
        select(RetainedBankKey)
        .where(
            RetainedBankKey.bank_id == row.bank_id,
            RetainedBankKey.organization_id == row.organization_id,
            RetainedBankKey.provider == key.provider,
            RetainedBankKey.key_id == key.key_id,
            RetainedBankKey.region == key.region,
            RetainedBankKey.owner_account == key.owner_account,
        )
        .execution_options(populate_existing=True)
    )
    if held is None:
        raise KeyUnavailableError("The key is not a rotated-out key of this bank.")
    if (
        db.scalar(
            select(BankEncryptionKey.id)
            .where(
                BankEncryptionKey.provider == key.provider,
                BankEncryptionKey.key_id == key.key_id,
            )
            .limit(1)
        )
        is not None
    ):
        raise KeyUnavailableError("The key is still connected to a bank.")
    retention = get_key_settings().backup_retention_days
    for source in db.scalars(
        select(RetainedBankKey)
        .where(
            RetainedBankKey.provider == key.provider,
            RetainedBankKey.key_id == key.key_id,
        )
        .execution_options(populate_existing=True)
    ):
        if source.decrypt_until is None or retention is None:
            raise KeyUnavailableError(
                "Backup retention is indefinite; the key must remain decrypt-capable."
            )
        deadline = max(
            _utc(source.decrypt_until),
            _utc(source.rotated_at) + timedelta(days=retention),
        )
        if utc_now() < deadline:
            raise KeyUnavailableError(
                "The key must remain decrypt-capable until backup retention expires."
            )


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
