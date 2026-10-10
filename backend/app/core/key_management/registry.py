from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol
from uuid import UUID, uuid4

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.key_management.aws import AwsKmsKeyProvider
from app.core.key_management.models import BankEncryptionKey, ObjectKeyEnvelope
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
    """Caller holds the bank row lock and commits the wrappers and config together.

    Each envelope authenticates its context digest rather than its object
    locator, allowing re-wrap without fetching object bytes or filenames.
    Writers acquire the same lock. Readers continue using the committed old
    wrappers until the transaction atomically switches every wrapper.
    """
    source = reference(row)
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
        select(ObjectKeyEnvelope).where(
            ObjectKeyEnvelope.bank_id == row.bank_id,
            ObjectKeyEnvelope.organization_id == row.organization_id,
        )
    ):
        envelope.wrapped_key = provider.rotate(
            source,
            target,
            envelope.wrapped_key,
            wrapping_context(row.bank_id, envelope.context_digest),
        )
        count += 1
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
    def _bank(db: Session, slug: str, *, lock: bool = False) -> BankEncryptionKey:
        query = select(BankEncryptionKey).where(BankEncryptionKey.storage_slug == slug)
        if lock:
            query = query.with_for_update()
        row = db.scalar(query)
        if row is None:
            raise KeyUnavailableError("The bank encryption key is not connected.")
        # A previous outage observation must not permanently disable access
        # after the bank restores its grant. Every operation probes the provider.
        return row

    def prepare(self, slug: str, context: dict[str, str]) -> ObjectKey:
        with self._sessions() as db, db.begin():
            row = self._bank(db, slug, lock=True)
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
        with self._sessions() as db:
            bank = self._bank(db, slug)
            _scope_envelopes(db, bank.organization_id)
            pair = (
                db.execute(
                    select(BankEncryptionKey, ObjectKeyEnvelope)
                    .join(ObjectKeyEnvelope, ObjectKeyEnvelope.bank_id == BankEncryptionKey.bank_id)
                    .where(
                        BankEncryptionKey.storage_slug == slug,
                        ObjectKeyEnvelope.id == envelope_id,
                        ObjectKeyEnvelope.organization_id == bank.organization_id,
                    )
                    # The pre-tenant lookup already loaded this identity. A
                    # concurrent rotation may commit before this SELECT; both
                    # the reference and wrapper must come from its snapshot.
                    .execution_options(populate_existing=True)
                )
                .tuples()
                .first()
            )
            if pair is None:
                raise KeyIntegrityError("The object key does not belong to this bank and object.")
            row, envelope = pair
            if envelope.context_digest != context_digest(context):
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
