"""Bank-scoped cryptographic maintenance without changing evidence guards."""

from __future__ import annotations

import base64
import json
from collections.abc import Iterator, Mapping
from typing import cast
from uuid import uuid4

from pydantic import TypeAdapter
from sqlalchemy import ColumnElement, String, Table, func, or_, select, text
from sqlalchemy import cast as sql_cast
from sqlalchemy.orm import Session

from app.core.key_management import fields, registry, sdk
from app.core.key_management.models import BankEncryptionKey, ObjectKeyEnvelope
from app.core.key_management.types import KeyIntegrityError
from app.db.base import Base

# These payloads are evidence. Other protected columns on the same row may move.
IMMUTABLE_FIELDS = {"bi_dashboard_versions": {"spec"}, "regulatory_packages": {"snapshot"}}


def lock_bank(db: Session, organization: str, bank: str, *, exclusive: bool = False) -> None:
    """Drain admitted units for a cutover; ordinary units share the same lock."""
    registry.lock_bank_version(db, organization, bank, read=not exclusive)


def protected_tables() -> list[Table]:
    return sorted(
        (
            t
            for t in Base.metadata.tables.values()
            if any(isinstance(c.type, fields.ProtectedField) for c in t.c)
        ),
        key=lambda t: t.name,
    )


def scope(table: Table, organization: str, bank: str) -> list[ColumnElement[bool]]:
    predicates: list[ColumnElement[bool]] = [table.c.organization_id == organization]
    column = next(c for c in table.c if isinstance(c.type, fields.ProtectedField))
    kind = cast(fields.ProtectedField, column.type)
    if kind.parent:
        parent_name, parent_id = kind.parent
        parent = Base.metadata.tables[parent_name]
        predicates.append(
            table.c[parent_id].in_(
                select(parent.c.id).where(
                    parent.c.organization_id == organization,
                    parent.c.bank_id == bank,
                )
            )
        )
    else:
        predicates.append(table.c.bank_id == bank)
    return predicates


def plaintext(
    db: Session,
    key: BankEncryptionKey,
    table: Table,
    row: Mapping[str, object],
    column: str,
) -> object:
    value = row[column]
    if not isinstance(value, fields.SealedField):
        return value
    context = {
        "purpose": "bank-sensitive-record-v1",
        "organization_id": key.organization_id,
        "bank_id": key.bank_id,
        "table": table.name,
        "record_id": str(row["id"]),
        "field": column,
    }
    secret = registry.open_envelope(
        db,
        key,
        value.envelope_id,
        {
            "purpose": "bank-sensitive-fields-v1",
            "organization_id": key.organization_id,
            "bank_id": key.bank_id,
        },
        registry.provider_for,
    ).plaintext
    return TypeAdapter(object).validate_json(
        sdk.decrypt(
            value.message,
            sdk.raw_keyring(secret, name=fields.FORMAT),
            context,
        )
    )


def reindex_bank(db: Session, key: BankEncryptionKey) -> None:
    """Convert every generation, including withdrawn identities, atomically."""
    secret = fields.index_key(db, key)
    for name in ("canonical_counterparties", "related_parties"):
        table = Base.metadata.tables.get(name)
        if table is None:
            continue
        if name == "canonical_counterparties":
            # Raw references and HMAC tokens share one SQL column. Vacate the
            # old namespace inside the exclusive cutover before inserting new
            # tokens, so a raw reference equal to another token cannot cause a
            # transient unique-index conflict.
            values: dict[str, object] = {"source_reference_blind_index": None}
            if "updated_at" in table.c:
                values["updated_at"] = table.c.updated_at
            _ = db.execute(
                table.update()
                .where(
                    *scope(table, key.organization_id, key.bank_id),
                )
                .values(**values)
            )
        for row in db.execute(
            select(table).where(*scope(table, key.organization_id, key.bank_id))
        ).mappings():
            values = {
                c.name: plaintext(db, key, table, cast(Mapping[str, object], row), c.name)
                for c in table.c
                if isinstance(c.type, fields.ProtectedField)
            }
            if name == "canonical_counterparties":
                values["attributes"] = row["attributes"]
            indexes = fields.index_values(name, values, key.organization_id, key.bank_id, secret)
            if "updated_at" in table.c:
                indexes["updated_at"] = cast(object, row["updated_at"])
            _ = db.execute(
                table.update()
                .where(
                    table.c.id == cast(object, row["id"]),
                    *scope(table, key.organization_id, key.bank_id),
                )
                .values(**indexes)
            )


def move_index_key(db: Session, key: BankEncryptionKey) -> None:
    envelope = db.get(ObjectKeyEnvelope, key.index_version_id)
    if envelope is None:
        raise KeyIntegrityError("The bank index key is unavailable.")
    if registry.reference(envelope) == registry.reference(key):
        return
    context = {
        "purpose": "bank-sensitive-index-v1",
        "organization_id": key.organization_id,
        "bank_id": key.bank_id,
    }
    secret = registry.open_envelope(db, key, envelope.id, context, registry.provider_for).plaintext
    digest = registry.context_digest(context)
    wrapping = registry.wrapping_context(key.bank_id, digest)
    provider = registry.provider_for(key.provider)
    wrapped = provider.wrap(registry.reference(key), secret, wrapping)
    if provider.unwrap(registry.reference(key), wrapped, wrapping) != secret:
        raise KeyIntegrityError("The bank index key failed its wrap/unwrap probe.")
    replacement = ObjectKeyEnvelope(
        id=uuid4(),
        bank_id=key.bank_id,
        organization_id=key.organization_id,
        context_digest=digest,
        wrapped_key=wrapped,
        provider=key.provider,
        key_id=key.key_id,
        region=key.region,
        owner_account=key.owner_account,
    )
    db.add(replacement)
    db.flush()
    key.index_version_id = replacement.id


def rewrite_row(
    db: Session, key: BankEncryptionKey, table: Table, row: Mapping[str, object]
) -> int:
    version = registry.current_version(db, key)
    changes: dict[str, object] = {}
    for col in table.c:
        if not isinstance(col.type, fields.ProtectedField) or col.name in IMMUTABLE_FIELDS.get(
            table.name, set()
        ):
            continue
        value = row[col.name]
        kind = col.type
        if value is None or (
            isinstance(value, fields.SealedField)
            and value.envelope_id == version
            and kind.protected_members is None
        ):
            continue
        value = plaintext(db, key, table, row, col.name)
        context = {
            "purpose": "bank-sensitive-record-v1",
            "organization_id": key.organization_id,
            "bank_id": key.bank_id,
            "table": table.name,
            "record_id": str(row["id"]),
        }

        def seal(
            value: object, field_name: str, *, context: dict[str, str] = context
        ) -> fields.SealedField:
            secret = registry.prepare_envelope(
                db,
                key,
                {
                    "purpose": "bank-sensitive-fields-v1",
                    "organization_id": key.organization_id,
                    "bank_id": key.bank_id,
                },
                registry.provider_for,
            )
            return fields.SealedField(
                version,
                sdk.encrypt(
                    json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode(),
                    sdk.raw_keyring(secret.plaintext, name=fields.FORMAT),
                    {**context, "field": field_name},
                ),
            )

        if kind.protected_members is None:
            changes[col.name] = seal(value, col.name)
            continue
        changed = isinstance(row[col.name], fields.SealedField)

        def rewrite_leaf(
            item: object,
            path: str,
            *,
            column_name: str = col.name,
            kind: fields.ProtectedField = kind,
            context: dict[str, str] = context,
        ) -> object:
            nonlocal changed
            field_name = column_name + ":" + path
            if isinstance(item, dict) and fields.FORMAT in item:
                sealed = kind.decode(cast(dict[str, object], item)[fields.FORMAT])
                if sealed.envelope_id == version:
                    return cast(dict[str, object], item)
                secret = registry.open_envelope(
                    db,
                    key,
                    sealed.envelope_id,
                    {
                        "purpose": "bank-sensitive-fields-v1",
                        "organization_id": key.organization_id,
                        "bank_id": key.bank_id,
                    },
                    registry.provider_for,
                )
                item = TypeAdapter(object).validate_json(
                    sdk.decrypt(
                        sealed.message,
                        sdk.raw_keyring(secret.plaintext, name=fields.FORMAT),
                        {**context, "field": field_name},
                    )
                )
            sealed = seal(cast(object, item), field_name)
            changed = True
            return {
                fields.FORMAT: [
                    str(sealed.envelope_id),
                    base64.b64encode(sealed.message).decode("ascii"),
                ]
            }

        rewritten = fields.identity_values(value, kind.protected_members, rewrite_leaf)
        if changed:
            changes[col.name] = fields.OptionalPlaintext(rewritten)
    if changes:
        if "updated_at" in table.c:
            changes["updated_at"] = row["updated_at"]
        _ = db.execute(
            table.update()
            .where(
                table.c.id == row["id"],
                *scope(table, key.organization_id, key.bank_id),
            )
            .values(**changes)
        )
    return len(changes) - int("updated_at" in changes)


def bi_identity_updates(
    db: Session, key: BankEncryptionKey, *, limit: int = 100
) -> Iterator[tuple[Table, list[ColumnElement[bool]], dict[str, object]]]:
    for name in ("bi_dim_counterparty", "bi_fact_position_daily", "bi_fact_position_eom"):
        table = Base.metadata.tables[name]
        predicates = [
            table.c.organization_id == key.organization_id,
            table.c.bank_id == key.bank_id,
        ]
        if name == "bi_dim_counterparty":
            identity = func.replace(sql_cast(table.c.counterparty_id, String), "-", "")
            dirty = or_(
                func.replace(table.c.name, "-", "") != identity,
                func.replace(table.c.source_reference, "-", "") != identity,
                table.c.group_reference.is_not(None),
            )
            for result in db.execute(
                select(table).where(*predicates, dirty).limit(limit)
            ).mappings():
                row = cast(Mapping[str, object], result)
                alias = str(row["counterparty_id"])
                yield (
                    table,
                    [*predicates, table.c.counterparty_id == row["counterparty_id"]],
                    {"name": alias, "source_reference": alias, "group_reference": None},
                )
        else:
            predicates.append(table.c.counterparty_group.is_not(None))
            for row in db.execute(select(table).where(*predicates).limit(limit)).mappings():
                yield (
                    table,
                    [*predicates, *(col == row[col.name] for col in table.primary_key)],
                    {"counterparty_group": None},
                )


def retained_audits(db: Session, key: BankEncryptionKey) -> dict[str, int]:
    retained: dict[str, int] = {}
    # Audit rows predate bank ownership in some deployments. Count the entire
    # organization's history conservatively: no bank_id column exists on it.
    for name in ("audit_events", "operator_audit_log"):
        table = Base.metadata.tables.get(name)
        if table is None:
            continue
        column = "organization_id" if "organization_id" in table.c else "target_org"
        if column not in table.c:
            raise KeyIntegrityError("Audit retention scope is unavailable.")
        count = (
            cast(int, db.scalar(text("SELECT aequoros_retained_operator_audit_count()")))
            if name == "operator_audit_log" and db.get_bind().dialect.name == "postgresql"
            else db.scalar(
                select(func.count())
                .select_from(table)
                .where(table.c[column] == key.organization_id)
            )
        )
        if count:
            retained[name + " (organization scope)"] = count
    return retained
