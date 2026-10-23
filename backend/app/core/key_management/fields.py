"""Bank-scoped ORM field boundary, using the existing provider/envelope hierarchy.

For connected banks, raw SQL sees SDK ciphertext. ORM entity loads authenticate the
row's scope before releasing values. Scalar ciphertext projections remain opaque: callers
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

from app.core.ids import new_uuid4, new_uuid7
from app.core.key_management import registry, sdk
from app.core.key_management.models import BankEncryptionKey
from app.core.key_management.presentation import (
    LOCKED_JSON,
    LOCKED_TEXT,
    SESSION_STATE,
    PresentationState,
    contains_placeholder,
)
from app.core.key_management.settings import get_key_settings
from app.core.key_management.types import (
    FIELD_FORMAT,
    KeyIntegrityError,
    KeyUnavailableError,
    ProtectedSupportAccessError,
)
from app.db.base import Base, UuidV4PrimaryKeyMixin

FORMAT = FIELD_FORMAT
_RESTORE = "bank_field_restore"
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
        empty_list: bool = False,
        parent: tuple[str, str] | None = None,
    ) -> None:
        super().__init__()
        self.empty_object = empty_object
        self.empty_list = empty_list
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
    return {"purpose": "bank-sensitive-fields-v1", "organization_id": organization, "bank_id": bank}


def _index_context(row: BankEncryptionKey) -> dict[str, str]:
    return {
        "purpose": "bank-sensitive-index-v1",
        "organization_id": row.organization_id,
        "bank_id": row.bank_id,
    }


def _index_key(db: Session, row: BankEncryptionKey) -> bytes:
    if row.index_version_id is None:
        raise KeyUnavailableError("The bank index key is unavailable.")
    return registry.open_envelope(
        db, row, row.index_version_id, _index_context(row), registry.provider_for
    ).plaintext


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
        # Optional banks retain ordinary field storage before key connection.
        if normalization == "name":
            return hashlib.sha256(normalized.encode()).hexdigest()
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
    """Compute a scoped exact-match token using the unit's authenticated index key."""
    with db.no_autoflush:
        row = registry.bank_key(db, organization_id, bank_id)
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
                if state.dict.get(col.attribute) is None:
                    if col.kind.empty_object:
                        setattr(instance, col.attribute, {})
                    elif col.kind.empty_list:
                        setattr(instance, col.attribute, [])
            if state.dict.get("id") is None:
                attributes.set_attribute(
                    instance,
                    "id",
                    new_uuid4() if isinstance(instance, UuidV4PrimaryKeyMixin) else new_uuid7(),
                )
            organization, bank = _scope(db, state, columns)
            scope = (organization, bank) if bank is not None else None
            if scope is not None and scope not in bank_rows:
                bank_rows[scope] = registry.bank_key(db, organization, scope[1])
            row = bank_rows[scope] if scope is not None else None
            if bank is not None and row is None and get_key_settings().bank_key_required:
                raise KeyUnavailableError("The bank encryption key is not connected.")
            changed = [
                col
                for col in columns
                if state.pending or state.attrs[col.attribute].history.has_changes()
            ]
            values = {col.attribute: cast(object, state.dict.get(col.attribute)) for col in changed}
            if any(contains_placeholder(value) for value in values.values()):
                raise KeyUnavailableError("Locked placeholders cannot be saved as protected data.")
            restores.append(_Restore(instance, values))
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
            for col in changed:
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
            continue
        for key, value in restore.values.items():
            attributes.set_committed_value(restore.instance, key, value)


@event.listens_for(Session, "after_flush_postexec")
def restore_after_flush(db: Session, _flush_context: object) -> None:
    _restore(db)


@event.listens_for(Session, "after_soft_rollback")
def restore_after_failure(db: Session, _previous: object) -> None:
    _restore(db, failed=True)


def _lock_field(instance: Base, col: FieldColumn, reason: str) -> None:
    # Structured executable payloads must never become an empty query/filter.
    # Their readers receive a named refusal, rendered as a locked panel.
    state = cast(InstanceState[object], inspect(instance))
    if cast(Table, state.mapper.local_table).name.startswith("bi_"):
        if reason == "support":
            raise ProtectedSupportAccessError()
        raise KeyUnavailableError("Protected configuration is locked.", revoked=reason == "revoked")
    value: object = LOCKED_TEXT if isinstance(col.kind, ProtectedText) else {LOCKED_JSON: reason}
    attributes.set_committed_value(instance, col.attribute, value)


def _presentation_locked(
    instance: Base, col: FieldColumn, presentation: object, bank: str | None
) -> bool:
    if not isinstance(presentation, PresentationState):
        return False
    reason = "support" if presentation.support else presentation.banks.get(bank or "")
    if reason not in {"support", "revoked", "unavailable"}:
        return False
    if bank is not None:
        presentation.record(bank, reason)
    _lock_field(instance, col, reason)
    return True


def _field_secret(  # noqa: PLR0913 - explicit field and bank boundaries
    db: Session,
    row: BankEncryptionKey,
    value: SealedField,
    presentation: object,
    instance: Base,
    col: FieldColumn,
) -> bytes | None:
    try:
        secret = registry.open_envelope(
            db,
            row,
            value.envelope_id,
            _envelope_context(row.organization_id, row.bank_id),
            registry.provider_for,
        ).plaintext
    except KeyIntegrityError:
        raise
    except KeyUnavailableError as exc:
        if not isinstance(presentation, PresentationState):
            raise
        reason = "revoked" if exc.revoked else "unavailable"
        presentation.record(row.bank_id, reason)
        _lock_field(instance, col, reason)
        return None
    if isinstance(presentation, PresentationState):
        presentation.record(row.bank_id, "available")
    return secret


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
        row = registry.bank_key(db, organization, bank) if bank is not None else None
        if bank is not None and row is None and get_key_settings().bank_key_required:
            raise KeyUnavailableError("The bank encryption key is not connected.")
        presentation = db.info.get(SESSION_STATE)
        for col in present:
            value = cast(object, state.dict[col.attribute])
            if _presentation_locked(instance, col, presentation, bank):
                continue
            if not isinstance(value, SealedField):
                continue
            if row is None:
                raise KeyUnavailableError("The bank encryption key is not connected.")
            context = _context(state, organization, row.bank_id)
            secret = _field_secret(db, row, value, presentation, instance, col)
            if secret is None:
                continue
            plaintext = sdk.decrypt(
                value.message,
                sdk.raw_keyring(secret, name=FORMAT),
                {**context, "field": col.column},
            )
            attributes.set_committed_value(
                instance, col.attribute, _decode_plaintext(col, plaintext)
            )


def _decode_plaintext(col: FieldColumn, plaintext: bytes) -> object:
    decoded = _JSON.validate_json(plaintext)
    if isinstance(col.kind, ProtectedText) and not isinstance(decoded, str):
        raise KeyIntegrityError("Encrypted text field has an invalid value.")
    if isinstance(col.kind, ProtectedJSON):
        valid_shape = (
            isinstance(decoded, list) if col.kind.empty_list else isinstance(decoded, dict)
        )
        if not valid_shape:
            raise KeyIntegrityError("Encrypted JSON field has an invalid value.")
    return decoded


@event.listens_for(Base, "load", propagate=True, restore_load_context=True)
def decrypt_loaded(instance: Base, _context: object) -> None:
    _decrypt_entity(instance)


@event.listens_for(Base, "refresh", propagate=True, restore_load_context=True)
def decrypt_refreshed(instance: Base, _context: object, attrs: object) -> None:
    refreshed = TypeAdapter(set[str]).validate_python(attrs) if attrs is not None else None
    _decrypt_entity(instance, refreshed)
