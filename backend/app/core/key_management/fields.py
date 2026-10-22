"""Bank-scoped ORM field boundary, using the existing provider/envelope hierarchy.

Raw SQL sees SDK ciphertext. ORM entity loads authenticate the row's scope before
releasing values. Scalar ciphertext projections deliberately remain opaque: callers
must select the scoped entity instead. Bulk writes cannot bypass this boundary.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import unicodedata
from dataclasses import dataclass, field
from typing import Literal, cast
from uuid import UUID

from pydantic import TypeAdapter
from sqlalchemy import JSON, Table, Text, event, inspect, select
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import InstanceState, Session, attributes, object_session
from sqlalchemy.types import TypeDecorator

from app.core.ids import new_uuid7
from app.core.key_management import registry, sdk
from app.core.key_management.models import BankEncryptionKey, ObjectKeyEnvelope
from app.core.key_management.settings import get_key_settings
from app.core.key_management.types import FIELD_FORMAT, KeyIntegrityError, KeyUnavailableError
from app.db.base import Base

FORMAT = FIELD_FORMAT
_RESTORE = "bank_field_restore"
_VERSIONS = "bank_field_versions"
_JSON = TypeAdapter(object)
type Normalization = Literal["name", "identifier"]


@dataclass(frozen=True, repr=False)
class SealedField:
    envelope_id: UUID
    message: bytes

    def __str__(self) -> str:
        raise KeyUnavailableError("Encrypted field projections require a scoped entity read.")


@dataclass(frozen=True)
class _OptionalPlaintext:
    value: object = field(repr=False)


class ProtectedField(TypeDecorator[object]):
    """Marker base; only the ORM event may produce a writable field value."""

    cache_ok = True
    impl = Text

    def __init__(
        self,
        *,
        empty_object: bool = False,
        parent: tuple[str, str] | None = None,
    ) -> None:
        super().__init__()
        self.empty_object = empty_object
        self.parent = parent

    def _encode(self, value: object) -> object:
        if value is None:
            return None
        if isinstance(value, _OptionalPlaintext):
            return value.value
        if not isinstance(value, SealedField):
            raise KeyUnavailableError("Sensitive fields require a scoped ORM write.")
        return [str(value.envelope_id), base64.b64encode(value.message).decode("ascii")]

    def _decode(self, value: object) -> SealedField:
        try:
            envelope, message = TypeAdapter(tuple[UUID, str]).validate_python(value)
            return SealedField(envelope, base64.b64decode(message, validate=True))
        except (ValueError, TypeError) as exc:
            raise KeyIntegrityError("Encrypted field format is invalid.") from exc


class ProtectedText(ProtectedField):
    cache_ok = True
    impl = Text

    def process_bind_param(self, value: object, dialect: Dialect) -> object:
        encoded = self._encode(value)
        if isinstance(value, SealedField):
            return FORMAT + ":" + json.dumps(encoded, separators=(",", ":"))
        return encoded

    def process_result_value(self, value: object, dialect: Dialect) -> object:
        if isinstance(value, str) and value.startswith(FORMAT + ":"):
            try:
                payload = _JSON.validate_json(value[len(FORMAT) + 1 :])
            except ValueError as exc:
                raise KeyIntegrityError("Encrypted field format is invalid.") from exc
            return self._decode(payload)
        return value


class ProtectedJSON(ProtectedField):
    cache_ok = True
    impl = JSON

    def process_bind_param(self, value: object, dialect: Dialect) -> object:
        encoded = self._encode(value)
        return {FORMAT: encoded} if isinstance(value, SealedField) else encoded

    def process_result_value(self, value: object, dialect: Dialect) -> object:
        if isinstance(value, dict) and FORMAT in value:
            return self._decode(cast(dict[str, object], value)[FORMAT])
        return cast(object, value)


@dataclass(frozen=True)
class FieldColumn:
    attribute: str
    column: str
    kind: ProtectedField


def field_columns(state: InstanceState[object]) -> list[FieldColumn]:
    return [
        FieldColumn(prop.key, prop.columns[0].name, prop.columns[0].type)
        for prop in state.mapper.column_attrs
        if isinstance(prop.columns[0].type, ProtectedField)
    ]


def _scope(
    db: Session, state: InstanceState[object], columns: list[FieldColumn]
) -> tuple[str, str | None]:
    organization = cast(object, state.dict.get("organization_id"))
    if not isinstance(organization, str):
        raise KeyIntegrityError("Sensitive field organization is missing.")
    bank = cast(object, state.dict.get("bank_id"))
    parent = columns[0].kind.parent
    if parent is not None:
        table, parent_id = parent
        parent_table = Base.metadata.tables[table]
        bank = db.scalar(
            select(parent_table.c.bank_id).where(
                parent_table.c.id == cast(object, state.dict.get(parent_id)),
                parent_table.c.organization_id == organization,
            )
        )
        if bank is None:
            raise KeyIntegrityError("Sensitive field parent does not belong to this organization.")
    if bank is not None and not isinstance(bank, str):
        raise KeyIntegrityError("Sensitive field bank is invalid.")
    return organization, bank


def bank_key(
    db: Session, organization: str, bank: str, *, writing: bool = False
) -> BankEncryptionKey | None:
    # Serialize optional-plaintext writes with key connection, including the
    # interval before any bank_encryption_keys row exists to lock.
    banks = Base.metadata.tables["banks"]
    _ = db.scalar(
        select(banks.c.id)
        .where(
            banks.c.id == bank,
            banks.c.organization_id == organization,
        )
        .with_for_update(read=True)
    )
    return db.scalar(
        select(BankEncryptionKey)
        .where(
            BankEncryptionKey.organization_id == organization,
            BankEncryptionKey.bank_id == bank,
        )
        .with_for_update(read=not writing)
        .execution_options(populate_existing=True)
    )


def _context(state: InstanceState[object], organization: str, bank: str) -> dict[str, str]:
    record_id = cast(object, state.dict.get("id"))
    if not isinstance(record_id, UUID):
        raise KeyIntegrityError("Sensitive field record identity is missing.")
    return {
        "purpose": "bank-sensitive-record-v1",
        "organization_id": organization,
        "bank_id": bank,
        "table": cast(Table, state.mapper.local_table).name,
        "record_id": str(record_id),
    }


def _envelope_context(organization: str, bank: str) -> dict[str, str]:
    # One operation-local wrapping secret may serve a bank's flush. The SDK
    # generates independent payload keys; every message additionally authenticates
    # the exact record and field through _context(). No secret survives the flush.
    return {"purpose": "bank-sensitive-fields-v1", "organization_id": organization, "bank_id": bank}


def _index_context(row: BankEncryptionKey) -> dict[str, str]:
    return {
        "purpose": "bank-sensitive-index-v1",
        "organization_id": row.organization_id,
        "bank_id": row.bank_id,
    }


def _index_key(db: Session, row: BankEncryptionKey) -> bytes:
    context = _index_context(row)
    digest = registry.context_digest(context)
    envelope = db.scalar(
        select(ObjectKeyEnvelope).where(
            ObjectKeyEnvelope.bank_id == row.bank_id,
            ObjectKeyEnvelope.organization_id == row.organization_id,
            ObjectKeyEnvelope.context_digest == digest,
        )
    )
    # before_flush may have appended this bank's first index envelope already.
    if envelope is None:
        envelope = next(
            (
                item
                for item in cast(list[object], list(db.new))
                if isinstance(item, ObjectKeyEnvelope)
                and item.bank_id == row.bank_id
                and item.organization_id == row.organization_id
                and item.context_digest == digest
            ),
            None,
        )
    if envelope is None:
        registry.lock_envelopes(db, row)
        envelope = db.scalar(
            select(ObjectKeyEnvelope).where(
                ObjectKeyEnvelope.bank_id == row.bank_id,
                ObjectKeyEnvelope.organization_id == row.organization_id,
                ObjectKeyEnvelope.context_digest == digest,
            )
        )
        if envelope is None:
            return registry.prepare_envelope(db, row, context, registry.provider_for).plaintext
    return registry.provider_for(row.provider).unwrap(
        registry.reference(row),
        envelope.wrapped_key,
        registry.wrapping_context(row.bank_id, digest),
    )


def normalize(value: str, normalization: Normalization) -> str:
    if normalization == "name":
        return " ".join(unicodedata.normalize("NFKC", value).casefold().split())
    return value


def _index(  # noqa: PLR0913 - scoped, domain-separated index contract
    secret: bytes | None,
    organization: str,
    bank: str,
    domain: str,
    value: str,
    normalization: Normalization,
) -> str:
    normalized = normalize(value, normalization)
    if secret is None:
        # Optional, unprotected banks retain their existing plaintext storage
        # contract. Connecting a key refuses those rows until backfill finishes.
        return normalized
    payload = json.dumps(
        [FORMAT, organization, bank, domain, normalized], ensure_ascii=False, separators=(",", ":")
    ).encode()
    return hmac.new(secret, payload, hashlib.sha256).hexdigest()


def blind_index(  # noqa: PLR0913 - explicit scope at the search boundary
    db: Session,
    *,
    organization_id: str,
    bank_id: str,
    domain: str,
    value: str,
    normalization: Normalization = "identifier",
) -> str:
    """Compute a scoped exact-match token; key access is required on every call."""
    with db.no_autoflush:
        row = bank_key(db, organization_id, bank_id)
        if row is None and get_key_settings().bank_key_required:
            raise KeyUnavailableError("The bank encryption key is not connected.")
        secret = _index_key(db, row) if row is not None else None
        return _index(secret, organization_id, bank_id, domain, value, normalization)


def _update_indexes(
    state: InstanceState[object], organization: str, bank: str, secret: bytes | None
) -> None:
    obj = state.obj()
    if obj is None:
        return
    table = cast(Table, state.mapper.local_table).name
    if table == "canonical_counterparties":
        name = cast(str, state.dict["name"])
        source = cast(str, state.dict["source_reference"])
        group = cast(str | None, state.dict.get("group_reference"))
        for column, domain, value, normalization in (
            ("name_blind_index", "counterparty.name", name, "name"),
            ("source_reference_blind_index", "counterparty.source_reference", source, "identifier"),
            ("concentration_group_blind_index", "counterparty.group", group or name, "identifier"),
        ):
            setattr(
                obj,
                column,
                _index(
                    secret, organization, bank, domain, value, cast(Normalization, normalization)
                ),
            )
        identifiers = cast(dict[str, object], state.dict.get("external_identifiers") or {})
        attributes.set_attribute(
            obj,
            "external_identifiers_blind_indexes",
            {
                namespace: _index(
                    secret,
                    organization,
                    bank,
                    "counterparty.identifier:" + namespace,
                    value,
                    "identifier",
                )
                for namespace, value in identifiers.items()
                if isinstance(value, str)
            },
        )
    elif table == "related_parties":
        name = cast(str, state.dict["full_name"])
        attributes.set_attribute(
            obj,
            "full_name_blind_index",
            _index(secret, organization, bank, "related_party.full_name", name, "name"),
        )


@dataclass
class _Restore:
    instance: object
    values: dict[str, object] = field(repr=False)
    encrypted: bool


@event.listens_for(Session, "before_flush")
def encrypt_pending(  # noqa: PLR0912 - one atomic flush boundary
    db: Session, _flush_context: object, _instances: object
) -> None:
    restores: list[_Restore] = []
    db.info[_RESTORE] = restores
    bank_rows: dict[tuple[str, str], BankEncryptionKey | None] = {}
    write_keys: dict[tuple[str, str], registry.ObjectKey] = {}
    index_keys: dict[tuple[str, str], bytes] = {}
    with db.no_autoflush:
        for instance in cast(list[object], list(db.new) + list(db.dirty)):
            state = cast(InstanceState[object], inspect(instance))
            columns = field_columns(state)
            if not columns or not (
                state.pending
                or any(state.attrs[col.attribute].history.has_changes() for col in columns)
            ):
                continue
            if state.persistent:
                for col in columns:
                    if col.attribute not in state.dict:
                        _ = cast(object, attributes.get_attribute(instance, col.attribute))
            for col in columns:
                if state.dict.get(col.attribute) is None and col.kind.empty_object:
                    setattr(instance, col.attribute, {})
            if state.dict.get("id") is None:
                attributes.set_attribute(instance, "id", new_uuid7())
            organization, bank = _scope(db, state, columns)
            scope = (organization, bank) if bank is not None else None
            if scope is not None and scope not in bank_rows:
                bank_rows[scope] = bank_key(db, organization, scope[1])
            row = bank_rows[scope] if scope is not None else None
            if bank is not None and row is None and get_key_settings().bank_key_required:
                raise KeyUnavailableError("The bank encryption key is not connected.")
            values = {col.attribute: cast(object, state.dict.get(col.attribute)) for col in columns}
            restores.append(_Restore(instance, values, row is not None))
            if row is None:
                if bank is not None:
                    _update_indexes(state, organization, bank, None)
                for key, value in values.items():
                    setattr(instance, key, _OptionalPlaintext(value) if value is not None else None)
                continue
            context = _context(state, organization, row.bank_id)
            assert scope is not None
            if scope not in write_keys:
                write_keys[scope] = registry.prepare_envelope(
                    db, row, _envelope_context(organization, row.bank_id), registry.provider_for
                )
            key = write_keys[scope]
            if cast(Table, state.mapper.local_table).name in {
                "canonical_counterparties",
                "related_parties",
            }:
                if scope not in index_keys:
                    index_keys[scope] = _index_key(db, row)
                _update_indexes(state, organization, row.bank_id, index_keys[scope])
            for col in columns:
                value = values[col.attribute]
                if value is not None:
                    message = sdk.encrypt(
                        json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode(),
                        sdk.raw_keyring(key.plaintext, name=FORMAT),
                        {**context, "field": col.column},
                    )
                    setattr(instance, col.attribute, SealedField(key.envelope_id, message))


def _restore(db: Session, *, failed: bool = False) -> None:
    restores = cast(list[_Restore], db.info.pop(_RESTORE, []))
    for restore in restores:
        state = cast(InstanceState[object], inspect(restore.instance))
        if failed and state.persistent:
            db.expire(restore.instance)
            state.info.pop(_VERSIONS, None)
            continue
        versions = cast(dict[str, bool], state.info.setdefault(_VERSIONS, {}))
        for key, value in restore.values.items():
            attributes.set_committed_value(restore.instance, key, value)
            versions[key] = restore.encrypted or value is None


@event.listens_for(Session, "after_flush_postexec")
def restore_after_flush(db: Session, _flush_context: object) -> None:
    _restore(db)


@event.listens_for(Session, "after_soft_rollback")
def restore_after_failure(db: Session, _previous: object) -> None:
    _restore(db, failed=True)


def _decrypt_entity(instance: Base, refreshed: set[str] | None = None) -> None:
    state = cast(InstanceState[object], inspect(instance))
    columns = field_columns(state)
    db = object_session(instance)
    if not columns or db is None:
        return
    # Refreshes can load only a subset; do not trigger deferred loads recursively.
    present = [
        col
        for col in columns
        if col.attribute in state.dict and (refreshed is None or col.attribute in refreshed)
    ]
    if not present:
        return
    with db.no_autoflush:
        parent = columns[0].kind.parent
        scope_attributes = ("organization_id", "id", parent[1] if parent else "bank_id")
        for attribute in scope_attributes:
            if attribute not in state.dict:
                _ = cast(object, attributes.get_attribute(instance, attribute))
        organization, bank = _scope(db, state, columns)
        row = bank_key(db, organization, bank) if bank is not None else None
        if bank is not None and row is None and get_key_settings().bank_key_required:
            raise KeyUnavailableError("The bank encryption key is not connected.")
        versions = cast(dict[str, bool], state.info.setdefault(_VERSIONS, {}))
        opened: dict[UUID, bytes] = {}
        for col in present:
            value = cast(object, state.dict[col.attribute])
            if not isinstance(value, SealedField):
                versions[col.attribute] = value is None
                if value is not None and row is not None and not db.info.get("bank_field_backfill"):
                    raise KeyUnavailableError("Legacy sensitive fields require bank-key backfill.")
                continue
            if row is None:
                raise KeyUnavailableError("The bank encryption key is not connected.")
            context = _context(state, organization, row.bank_id)
            secret = opened.get(value.envelope_id)
            if secret is None:
                secret = registry.open_envelope(
                    db,
                    row,
                    value.envelope_id,
                    _envelope_context(organization, row.bank_id),
                    registry.provider_for,
                ).plaintext
                opened[value.envelope_id] = secret
            plaintext = sdk.decrypt(
                value.message,
                sdk.raw_keyring(secret, name=FORMAT),
                {**context, "field": col.column},
            )
            decoded = _JSON.validate_json(plaintext)
            if isinstance(col.kind, ProtectedText) and not isinstance(decoded, str):
                raise KeyIntegrityError("Encrypted text field has an invalid value.")
            if isinstance(col.kind, ProtectedJSON) and not isinstance(decoded, dict):
                raise KeyIntegrityError("Encrypted JSON field has an invalid value.")
            attributes.set_committed_value(instance, col.attribute, decoded)
            versions[col.attribute] = True


@event.listens_for(Base, "load", propagate=True, restore_load_context=True)
def decrypt_loaded(instance: Base, _context: object) -> None:
    _decrypt_entity(instance)


@event.listens_for(Base, "refresh", propagate=True, restore_load_context=True)
def decrypt_refreshed(instance: Base, _context: object, attrs: object) -> None:
    refreshed = TypeAdapter(set[str]).validate_python(attrs) if attrs is not None else None
    _decrypt_entity(instance, refreshed)


def needs_backfill(instance: Base) -> bool:
    state = cast(InstanceState[object], inspect(instance))
    versions = cast(dict[str, bool], state.info.get(_VERSIONS, {}))
    return any(not versions.get(col.attribute, False) for col in field_columns(state))


def mark_for_backfill(instance: Base) -> None:
    state = cast(InstanceState[object], inspect(instance))
    for col in field_columns(state):
        if col.attribute in state.dict:
            attributes.flag_modified(instance, col.attribute)
