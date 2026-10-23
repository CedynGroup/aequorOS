from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Callable, Generator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from threading import Lock, Thread
from typing import Protocol, cast
from uuid import UUID, uuid4

from sqlalchemy import Engine, event, func, or_, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, SessionTransaction

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

_usage_recording = Lock()


def provider_for(provider: str) -> KeyProvider:
    if provider != "aws_kms":
        raise KeyUnavailableError("The bank encryption key provider is not supported.")
    return AwsKmsKeyProvider()


def reference(row: BankEncryptionKey | ObjectKeyEnvelope) -> KeyReference:
    return KeyReference(row.provider, row.key_id, row.region, row.owner_account)


def require_active(description: KeyDescription) -> KeyReference:
    if description.status == KeyStatus.DISABLED:
        raise KeyUnavailableError(
            "The bank encryption key is disabled or pending deletion. "
            "Enable the key in AWS KMS, then retry."
        )
    if description.status != KeyStatus.ACTIVE:
        raise KeyUnavailableError(
            "The bank encryption key is unavailable. Check its ARN, account and region, then retry."
        )
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
    resolved = require_active(providers(key.provider).describe(key))
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
    _new_version(db, row, resolved, providers)
    _unit(db).banks.pop((organization_id, bank_id), None)
    return row


def bank_key(db: Session, organization: str, bank: str) -> BankEncryptionKey | None:
    scope = (organization, bank)
    unit = _unit(db)
    if scope not in unit.banks:
        unit.banks[scope] = db.scalar(
            select(BankEncryptionKey)
            .where(
                BankEncryptionKey.organization_id == organization,
                BankEncryptionKey.bank_id == bank,
            )
            .execution_options(populate_existing=True)
        )
    return unit.banks[scope]


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
    if destination.owner_account != source.owner_account:
        raise KeyUnavailableError("Rotation must retain the bank's key ownership account.")
    target = require_active(providers(destination.provider).describe(destination))
    if source.provider != target.provider:
        raise KeyUnavailableError("Changing key providers requires an explicit migration.")
    _new_version(db, row, target, providers)
    rotated_at = utc_now()
    retention = get_key_settings().backup_retention_days
    decrypt_until = rotated_at + timedelta(days=retention) if retention is not None else None
    held = db.scalar(
        select(RetainedBankKey)
        .where(
            RetainedBankKey.bank_id == row.bank_id,
            RetainedBankKey.organization_id == row.organization_id,
            RetainedBankKey.provider == source.provider,
            RetainedBankKey.key_id == source.key_id,
        )
        .execution_options(populate_existing=True)
    )
    if held is None:
        db.add(
            RetainedBankKey(
                bank_id=row.bank_id,
                organization_id=row.organization_id,
                provider=source.provider,
                key_id=source.key_id,
                region=source.region,
                owner_account=source.owner_account,
                rotated_at=rotated_at,
                decrypt_until=decrypt_until,
            )
        )
    else:
        held.rotated_at = rotated_at
        held.decrypt_until = (
            max(_utc(held.decrypt_until), decrypt_until)
            if held.decrypt_until is not None and decrypt_until is not None
            else None
        )
    row.rotation_period_days = None
    row.rotation_confirmed_at = None
    row.last_rotated_at = rotated_at
    row.key_id = target.key_id
    row.region = target.region
    row.status = KeyStatus.ACTIVE.value
    row.checked_at = utc_now()
    db.flush()
    _unit(db).current.pop((row.organization_id, row.bank_id), None)
    return 0


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
    def for_session(self, db: Session) -> EnvelopeStore: ...

    def prepare(self, slug: str, context: dict[str, str]) -> ObjectKey: ...

    def open(self, slug: str, envelope_id: UUID, context: dict[str, str]) -> ObjectKey: ...


@dataclass(repr=False)
class _Unit:
    banks: dict[tuple[str, str], BankEncryptionKey | None] = field(default_factory=dict)
    current: dict[tuple[str, str], UUID] = field(default_factory=dict)
    envelopes: dict[UUID, ObjectKeyEnvelope] = field(default_factory=dict)
    material: dict[UUID, bytes] = field(default_factory=dict)
    durable: set[UUID] = field(default_factory=set)
    usage: dict[tuple[str, str, UUID], datetime] = field(default_factory=dict)


def _unit(db: Session) -> _Unit:
    unit = cast(object, db.info.get("bank_key_unit"))
    if not isinstance(unit, _Unit):
        unit = _Unit()
        db.info["bank_key_unit"] = unit
    return unit


@event.listens_for(Session, "after_transaction_end")
def discard_material(db: Session, transaction: SessionTransaction) -> None:
    if transaction.parent is None:
        unit = cast(object, db.info.pop("bank_key_unit", None))
        if not isinstance(unit, _Unit) or not unit.usage:
            return
        engine = db.get_bind()
        if not isinstance(engine, Engine) or not _usage_recording.acquire(blocking=False):
            return
        try:
            Thread(target=_record_completed_usage, args=(engine, unit.usage), daemon=True).start()
        except RuntimeError:
            _usage_recording.release()


def _record_completed_usage(engine: Engine, usage: dict[tuple[str, str, UUID], datetime]) -> None:
    try:
        for (organization, bank, version), observed_at in usage.items():
            _record_usage(engine, organization, bank, version, observed_at)
    finally:
        _usage_recording.release()


def _record_usage(
    engine: Engine, organization: str, bank: str, version: UUID, observed_at: datetime
) -> None:
    try:
        with Session(engine) as peer, peer.begin():
            peer.info["organization_id"] = organization
            if engine.dialect.name == "postgresql":
                _ = peer.execute(text("SET LOCAL lock_timeout = '1ms'"))
                _ = peer.execute(text("SET LOCAL statement_timeout = '100ms'"))
            envelope = peer.scalar(
                select(ObjectKeyEnvelope)
                .where(
                    ObjectKeyEnvelope.id == version,
                    ObjectKeyEnvelope.organization_id == organization,
                    ObjectKeyEnvelope.bank_id == bank,
                    or_(
                        ObjectKeyEnvelope.last_successful_use_at.is_(None),
                        ObjectKeyEnvelope.last_successful_use_at
                        < observed_at - timedelta(minutes=5),
                    ),
                )
                .with_for_update(skip_locked=True, key_share=True)
            )
            if envelope is not None:
                envelope.last_successful_use_at = observed_at
    except SQLAlchemyError:
        pass


def last_successful_use(db: Session, row: BankEncryptionKey) -> datetime | None:
    latest = db.scalar(
        select(func.max(ObjectKeyEnvelope.last_successful_use_at)).where(
            ObjectKeyEnvelope.organization_id == row.organization_id,
            ObjectKeyEnvelope.bank_id == row.bank_id,
        )
    )
    times = [time for time in (latest, row.last_successful_use_at) if time is not None]
    return max(times, key=_utc) if times else None


def version_context(organization: str, bank: str) -> dict[str, str]:
    return {"purpose": "bank-data-key-version-v1", "organization_id": organization, "bank_id": bank}


def _new_version(
    db: Session, row: BankEncryptionKey, key: KeyReference, providers: ProviderFactory
) -> ObjectKeyEnvelope:
    digest = context_digest(version_context(row.organization_id, row.bank_id))
    provider = providers(key.provider)
    context = wrapping_context(row.bank_id, digest)
    data = provider.generate_data_key(key, context)
    if provider.unwrap(key, data.wrapped, context) != data.plaintext:
        raise KeyIntegrityError("The bank key failed its wrap/unwrap probe.")
    version = ObjectKeyEnvelope(
        id=uuid4(),
        bank_id=row.bank_id,
        organization_id=row.organization_id,
        context_digest=digest,
        wrapped_key=data.wrapped,
        provider=key.provider,
        key_id=key.key_id,
        region=key.region,
        owner_account=key.owner_account,
    )
    db.add(version)
    db.flush()
    row.last_successful_use_at = utc_now()
    row.current_version_id = version.id
    if row.index_version_id is None:
        row.index_version_id = version.id
    unit = _unit(db)
    unit.envelopes[version.id] = version
    unit.material[version.id] = data.plaintext
    return version


def current_version(db: Session, row: BankEncryptionKey) -> UUID:
    unit = _unit(db)
    scope = (row.organization_id, row.bank_id)
    if scope not in unit.current:
        if row.current_version_id is None:
            raise KeyUnavailableError("The bank data-key version is unavailable.")
        unit.current[scope] = row.current_version_id
    return unit.current[scope]


def prepare_envelope(
    db: Session,
    row: BankEncryptionKey,
    context: dict[str, str],
    providers: ProviderFactory = provider_for,
) -> ObjectKey:
    return open_envelope(db, row, current_version(db, row), context, providers)


def open_envelope(
    db: Session,
    row: BankEncryptionKey,
    envelope_id: UUID,
    context: dict[str, str],
    providers: ProviderFactory = provider_for,
) -> ObjectKey:
    unit = _unit(db)
    envelope = unit.envelopes.get(envelope_id)
    if envelope is None:
        envelope = db.scalar(
            select(ObjectKeyEnvelope).where(
                ObjectKeyEnvelope.bank_id == row.bank_id,
                ObjectKeyEnvelope.organization_id == row.organization_id,
                ObjectKeyEnvelope.id == envelope_id,
            )
        )
        if envelope is None:
            raise KeyIntegrityError("The data key does not belong to this bank and record.")
        unit.envelopes[envelope_id] = envelope
    if (
        envelope.bank_id != row.bank_id
        or envelope.organization_id != row.organization_id
        or envelope.context_digest
        != context_digest(version_context(row.organization_id, row.bank_id))
    ):
        raise KeyIntegrityError("The data key does not belong to this bank and record.")
    plaintext = unit.material.get(envelope_id)
    if plaintext is None:
        plaintext = providers(envelope.provider).unwrap(
            reference(envelope),
            envelope.wrapped_key,
            wrapping_context(row.bank_id, envelope.context_digest),
        )
        unit.usage[(row.organization_id, row.bank_id, envelope_id)] = utc_now()
        unit.material[envelope_id] = plaintext
    plaintext = hmac.new(
        plaintext, ("bank-payload:" + context_digest(context)).encode(), hashlib.sha256
    ).digest()
    return ObjectKey(envelope_id, envelope.key_id, plaintext)


class DatabaseEnvelopeStore:
    def __init__(
        self,
        sessions: Callable[[], Session],
        providers: ProviderFactory = provider_for,
        *,
        session: Session | None = None,
    ) -> None:
        self._sessions = sessions
        self._providers = providers
        self._session = session

    def for_session(self, db: Session) -> DatabaseEnvelopeStore:
        return DatabaseEnvelopeStore(self._sessions, self._providers, session=db)

    @contextmanager
    def _transaction(self) -> Generator[Session]:
        if self._session is not None:
            with self._session.no_autoflush:
                yield self._session
        else:
            with self._sessions() as db, db.begin():
                yield db

    @staticmethod
    def _bank(db: Session, slug: str) -> BankEncryptionKey:
        row = db.scalar(select(BankEncryptionKey).where(BankEncryptionKey.storage_slug == slug))
        if row is None:
            raise KeyUnavailableError("The bank encryption key is not connected.")
        _scope_envelopes(db, row.organization_id)
        connected = bank_key(db, row.organization_id, row.bank_id)
        if connected is None or connected.storage_slug != slug:
            raise KeyUnavailableError("The bank encryption key is not connected.")
        return connected

    def prepare(self, slug: str, context: dict[str, str]) -> ObjectKey:
        with self._transaction() as db:
            row = self._bank(db, slug)
            key = prepare_envelope(db, row, context, self._providers)
            unit = _unit(db)
            if self._session is not None and key.envelope_id not in unit.durable:
                with self._sessions() as peer:
                    _scope_envelopes(peer, row.organization_id)
                    visible = peer.scalar(
                        select(ObjectKeyEnvelope.id).where(
                            ObjectKeyEnvelope.id == key.envelope_id,
                            ObjectKeyEnvelope.organization_id == row.organization_id,
                            ObjectKeyEnvelope.bank_id == row.bank_id,
                        )
                    )
                    if visible is None:
                        raise KeyUnavailableError(
                            "Object publication requires a committed bank key version."
                        )
                unit.durable.add(key.envelope_id)
            return key

    def open(self, slug: str, envelope_id: UUID, context: dict[str, str]) -> ObjectKey:
        with self._transaction() as db:
            row = self._bank(db, slug)
            return open_envelope(db, row, envelope_id, context, self._providers)


def _scope_envelopes(db: Session, organization_id: str) -> None:
    if db.info.get("organization_id") not in (None, organization_id):
        raise KeyIntegrityError("The storage key does not belong to this organization.")
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

    if (
        db.scalar(
            select(ObjectKeyEnvelope.id)
            .where(
                ObjectKeyEnvelope.provider == key.provider,
                ObjectKeyEnvelope.key_id == key.key_id,
            )
            .limit(1)
        )
        is not None
    ):
        raise KeyUnavailableError("The key is still required by retained encryption versions.")


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
