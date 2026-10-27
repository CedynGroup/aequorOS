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
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from itertools import batched
from typing import Literal, cast
from uuid import UUID

from pydantic import TypeAdapter
from sqlalchemy import JSON, Table, Text, event, inspect, select
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import InstanceState, Mapper, Session, attributes, object_session, undefer
from sqlalchemy.types import TypeDecorator

from app.core.ids import new_uuid4, new_uuid7
from app.core.key_management import registry, sdk
from app.core.key_management.concentration_identity import counterparty_group_key
from app.core.key_management.models import BankEncryptionKey
from app.core.key_management.presentation import (
    LOCKED_JSON,
    LOCKED_TEXT,
    SESSION_STATE,
    SESSION_SUPPORT,
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
        protected_members: frozenset[str] | None = None,
    ) -> None:
        super().__init__()
        self.empty_object = empty_object
        self.empty_list = empty_list
        self.parent = parent
        self.protected_members = protected_members

    def _encode(self, value: object) -> object:
        if value is None:
            return None
        if isinstance(value, _OptionalPlaintext):
            return value.value
        if not isinstance(value, SealedField):
            raise KeyUnavailableError("Sensitive fields require a scoped ORM write.")
        return [str(value.envelope_id), base64.b64encode(value.message).decode("ascii")]

    def decode(self, value: object) -> SealedField:
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
            return self.decode(payload)
        return value


class ProtectedJSON(ProtectedField):
    cache_ok = True
    impl = JSON

    def process_bind_param(self, value: object, dialect: Dialect) -> object:
        encoded = self._encode(value)
        return {FORMAT: encoded} if isinstance(value, SealedField) else encoded

    def process_result_value(self, value: object, dialect: Dialect) -> object:
        if isinstance(value, dict) and FORMAT in value:
            return self.decode(cast(dict[str, object], value)[FORMAT])
        return cast(object, value)


def _validate_value(kind: ProtectedField, value: object) -> object:
    if isinstance(kind, ProtectedText) and not isinstance(value, str):
        raise KeyIntegrityError("Encrypted text field has an invalid value.")
    if isinstance(kind, ProtectedJSON):
        valid_shape = isinstance(value, list) if kind.empty_list else isinstance(value, dict)
        if not valid_shape:
            raise KeyIntegrityError("Encrypted JSON field has an invalid value.")
    return value


def _identity_values(
    value: object,
    members: frozenset[str],
    transform: Callable[[object, str], object],
    path: tuple[str | int, ...] = (),
) -> object:
    if isinstance(value, list):
        return [
            _identity_values(item, members, transform, (*path, index))
            for index, item in enumerate(cast(list[object], value))
        ]
    if isinstance(value, dict):
        mapping = cast(dict[str, object], value)
        return {
            key: transform(item, json.dumps((*path, key), separators=(",", ":")))
            if key == "values"
            and isinstance(mapping.get("member"), str)
            and mapping["member"] in members
            else _identity_values(item, members, transform, (*path, key))
            for key, item in mapping.items()
        }
    return value


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
        if normalization == "name" or domain == "counterparty.group":
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
        group = counterparty_group_key(
            name,
            cast(str | None, state.dict.get("group_reference")),
            cast(dict[str, object], state.dict.get("attributes") or {}),
        )
        for column, domain, value, normalization in (
            ("name_blind_index", "counterparty.name", name, "name"),
            ("source_reference_blind_index", "counterparty.source_reference", source, "identifier"),
            ("concentration_group_blind_index", "counterparty.group", group, "identifier"),
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


def _pending_columns(state: InstanceState[object]) -> list[FieldColumn]:
    columns = field_columns(state)
    if not columns:
        return []
    if state.pending or any(state.attrs[col.attribute].history.has_changes() for col in columns):
        return columns
    if cast(Table, state.mapper.local_table).name == "canonical_counterparties":
        history = state.attrs["attributes"].history
        if history.has_changes():
            before = cast(dict[str, object], history.deleted[0]) if history.deleted else {}
            after = cast(dict[str, object], state.dict.get("attributes") or {})
            if any(
                before.get(key) != after.get(key) for key in ("group_reference", "group", "parent")
            ):
                return columns
    return []


def _load_pending_fields(db: Session, states: list[InstanceState[object]]) -> None:
    groups: dict[tuple[Mapper[object], str, str | None], list[UUID]] = {}
    for state in states:
        columns = field_columns(state)
        if not state.persistent or all(col.attribute in state.dict for col in columns):
            continue
        instance = state.obj()
        assert instance is not None and state.identity is not None
        parent = columns[0].kind.parent
        for attribute in ("organization_id", parent[1] if parent else "bank_id"):
            if attribute not in state.dict:
                _ = cast(object, attributes.get_attribute(instance, attribute))
        organization, bank = _scope(db, state, columns)
        groups.setdefault((state.mapper, organization, bank), []).append(
            cast(UUID, state.identity[0])
        )
    for (mapper, organization, bank), ids in groups.items():
        table = cast(Table, mapper.local_table)
        for batch in batched(ids, 500, strict=False):
            stmt = (
                select(mapper.class_)
                .options(undefer("*"))
                .where(table.c.organization_id == organization, table.c.id.in_(batch))
            )
            if "bank_id" in table.c:
                stmt = stmt.where(table.c.bank_id == bank)
            _ = db.scalars(stmt).all()


@event.listens_for(Session, "before_flush")
def encrypt_pending(  # noqa: PLR0912, PLR0915 - one atomic flush boundary
    db: Session, _flush_context: object, _instances: object
) -> None:
    restores: list[_Restore] = []
    db.info[_RESTORE] = restores
    bank_rows: dict[tuple[str, str], BankEncryptionKey | None] = {}
    write_keys: dict[tuple[str, str], registry.ObjectKey] = {}
    index_keys: dict[tuple[str, str], bytes] = {}
    with db.no_autoflush:
        pending = [
            (instance, state, columns)
            for instance in cast(list[object], list(db.new) + list(db.dirty))
            if (
                columns := _pending_columns(state := cast(InstanceState[object], inspect(instance)))
            )
        ]
        _load_pending_fields(db, [state for _, state, _ in pending])
        for instance, state, columns in pending:
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
            presentation = db.info.get(SESSION_STATE)
            reason = (
                presentation.banks.get(bank or "")
                if isinstance(presentation, PresentationState)
                else None
            )
            if db.info.get(SESSION_SUPPORT) or (
                isinstance(presentation, PresentationState) and presentation.support
            ):
                raise ProtectedSupportAccessError()
            if reason in {"revoked", "unavailable"} or any(
                contains_placeholder(state.dict.get(col.attribute)) for col in columns
            ):
                raise KeyUnavailableError(
                    "Locked placeholders cannot be saved as protected data.",
                    revoked=reason == "revoked",
                )
            restores.append(_Restore(instance, values))
            if row is None:
                if bank is not None:
                    _update_indexes(state, organization, bank, None)
                for key, value in values.items():
                    setattr(instance, key, _OptionalPlaintext(value) if value is not None else None)
                continue
            context = _context(state, organization, row.bank_id)
            assert scope is not None
            if cast(Table, state.mapper.local_table).name in {
                "canonical_counterparties",
                "related_parties",
            }:
                if scope not in index_keys:
                    index_keys[scope] = _index_key(db, row)
                _update_indexes(state, organization, row.bank_id, index_keys[scope])

            def seal(  # noqa: PLR0913
                value: object,
                field_name: str,
                *,
                scope: tuple[str, str] = scope,
                row: BankEncryptionKey = row,
                organization: str = organization,
                context: dict[str, str] = context,
            ) -> SealedField:
                if scope not in write_keys:
                    write_keys[scope] = registry.prepare_envelope(
                        db, row, _envelope_context(organization, row.bank_id), registry.provider_for
                    )
                key = write_keys[scope]
                message = sdk.encrypt(
                    json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode(),
                    sdk.raw_keyring(key.plaintext, name=FORMAT),
                    {**context, "field": field_name},
                )
                return SealedField(key.envelope_id, message)

            for col in changed:
                value = values[col.attribute]
                if value is None:
                    continue
                if col.kind.protected_members is None:
                    encrypted: object = seal(value, col.column)
                else:

                    def seal_values(item: object, path: str, col: FieldColumn = col) -> object:
                        sealed = seal(item, col.column + ":" + path)
                        return {
                            FORMAT: [
                                str(sealed.envelope_id),
                                base64.b64encode(sealed.message).decode("ascii"),
                            ]
                        }

                    encrypted = _OptionalPlaintext(
                        _identity_values(value, col.kind.protected_members, seal_values)
                    )
                setattr(instance, col.attribute, encrypted)


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
    state = cast(InstanceState[object], inspect(instance))
    if cast(Table, state.mapper.local_table).name.startswith("bi_"):
        if reason == "support":
            raise ProtectedSupportAccessError()
        raise KeyUnavailableError("Protected configuration is locked.", revoked=reason == "revoked")
    value: object = LOCKED_TEXT if isinstance(col.kind, ProtectedText) else {LOCKED_JSON: reason}
    attributes.set_committed_value(instance, col.attribute, value)


def _presentation_locked(
    instance: Base,
    col: FieldColumn,
    presentation: object,
    bank: str | None,
    *,
    missing_key: bool,
) -> bool:
    if not isinstance(presentation, PresentationState):
        return False
    support = presentation.support or presentation.banks.get(bank or "") == "support"
    if not support and not missing_key:
        return False
    reason = "support" if support else "unavailable"
    if bank is not None:
        presentation.record(bank, reason)
    _lock_field(instance, col, reason)
    return True


def observe_bank_key(db: Session, organization: str, bank: str) -> None:
    presentation = db.info.get(SESSION_STATE)
    if not isinstance(presentation, PresentationState) or bank in presentation.observed_banks:
        return
    if presentation.support or db.info.get(SESSION_SUPPORT):
        presentation.record(bank, "support")
        presentation.observed_banks.add(bank)
        return
    registry.lock_bank_version(db, organization, bank, read=True)
    row = registry.bank_key(db, organization, bank)
    if row is None and get_key_settings().bank_key_required:
        presentation.record(bank, "unavailable")
        presentation.observed_banks.add(bank)
        return
    if row is not None:
        versions = {row.current_version_id: _envelope_context(organization, bank)}
        versions.setdefault(row.index_version_id, _index_context(row))
        for version, context in versions.items():
            try:
                if version is None:
                    raise KeyUnavailableError("The bank data-key version is unavailable.")
                if reason := presentation.read_locks.get(version):
                    presentation.record(bank, reason)
                    continue
                registry.open_envelope(db, row, version, context, registry.provider_for)
            except KeyIntegrityError:
                raise
            except KeyUnavailableError as exc:
                reason = "revoked" if exc.revoked else "unavailable"
                presentation.record(bank, reason)
                if version is not None:
                    presentation.read_locks[version] = reason
    presentation.record(bank, "available")
    presentation.observed_banks.add(bank)


def _field_secret(  # noqa: PLR0913 - explicit field and bank boundaries
    db: Session,
    row: BankEncryptionKey,
    value: SealedField,
    presentation: object,
    instance: Base,
    col: FieldColumn,
) -> bytes | None:
    if isinstance(presentation, PresentationState) and (
        reason := presentation.read_locks.get(value.envelope_id)
    ):
        observe_bank_key(db, row.organization_id, row.bank_id)
        _lock_field(instance, col, reason)
        return None
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
        presentation.read_locks[value.envelope_id] = reason
        observe_bank_key(db, row.organization_id, row.bank_id)
        _lock_field(instance, col, reason)
        return None
    observe_bank_key(db, row.organization_id, row.bank_id)
    return secret


def _decrypt_entity(  # noqa: PLR0912 - one scoped ORM load/refresh boundary
    instance: Base, refreshed: set[str] | None = None
) -> None:
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
        presentation = db.info.get(SESSION_STATE)
        if db.info.get(SESSION_SUPPORT):
            if not isinstance(presentation, PresentationState):
                presentation = PresentationState(support=True)
            presentation.support = True
        missing_key = bank is not None and row is None and get_key_settings().bank_key_required
        if bank is not None and missing_key:
            if not isinstance(presentation, PresentationState):
                raise KeyUnavailableError("The bank encryption key is not connected.")
            presentation.record(bank, "support" if presentation.support else "unavailable")
        for col in present:
            value = cast(object, state.dict[col.attribute])
            if col.kind.protected_members is not None and not isinstance(value, SealedField):
                value = _validate_value(col.kind, value)

                def open_values(item: object, path: str, col: FieldColumn = col) -> object:
                    leaf = replace(col, column=col.column + ":" + path)
                    if _presentation_locked(
                        instance, leaf, presentation, bank, missing_key=missing_key
                    ):
                        return None
                    if not isinstance(item, dict) or FORMAT not in item:
                        return cast(object, item)
                    sealed = col.kind.decode(cast(dict[str, object], item)[FORMAT])
                    if row is None:
                        raise KeyUnavailableError("The bank encryption key is not connected.")
                    secret = _field_secret(db, row, sealed, presentation, instance, leaf)
                    if secret is None:
                        return None
                    plaintext = sdk.decrypt(
                        sealed.message,
                        sdk.raw_keyring(secret, name=FORMAT),
                        {**_context(state, organization, row.bank_id), "field": leaf.column},
                    )
                    return _JSON.validate_json(plaintext)

                attributes.set_committed_value(
                    instance,
                    col.attribute,
                    _identity_values(value, col.kind.protected_members, open_values),
                )
                continue
            if _presentation_locked(instance, col, presentation, bank, missing_key=missing_key):
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
                instance, col.attribute, _validate_value(col.kind, _JSON.validate_json(plaintext))
            )


@event.listens_for(Base, "load", propagate=True, restore_load_context=True)
def decrypt_loaded(instance: Base, _context: object) -> None:
    _decrypt_entity(instance)


@event.listens_for(Base, "refresh", propagate=True, restore_load_context=True)
def decrypt_refreshed(instance: Base, _context: object, attrs: object) -> None:
    refreshed = TypeAdapter(set[str]).validate_python(attrs) if attrs is not None else None
    _decrypt_entity(instance, refreshed)
