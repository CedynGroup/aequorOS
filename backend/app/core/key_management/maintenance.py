"""Bank-scoped cryptographic maintenance without changing evidence guards."""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from typing import cast

from pydantic import TypeAdapter
from sqlalchemy import ColumnElement, Table, func, select, text
from sqlalchemy.orm import Session

from app.core.key_management import fields, registry, sdk
from app.core.key_management.models import BankEncryptionKey
from app.core.key_management.types import KeyIntegrityError
from app.db.base import Base

# These payloads are evidence. Other protected columns on the same row may move.
IMMUTABLE_FIELDS = {"bi_dashboard_versions": {"spec"}, "regulatory_packages": {"snapshot"}}


def lock_bank(db: Session, organization: str, bank: str, *, exclusive: bool = False) -> None:
    """Drain admitted units for a cutover; ordinary units share the same lock."""
    if db.get_bind().dialect.name == "postgresql":
        function = "pg_advisory_xact_lock" if exclusive else "pg_advisory_xact_lock_shared"
        _ = db.execute(
            text(f"SELECT {function}(hashtextextended(:scope, 0))"),
            {"scope": f"bank-encryption-maintenance:{organization}:{bank}"},
        )


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
        if value is None or isinstance(value, fields.SealedField) and value.envelope_id == version:
            continue
        value = plaintext(db, key, table, row, col.name)
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
        context = {
            "purpose": "bank-sensitive-record-v1",
            "organization_id": key.organization_id,
            "bank_id": key.bank_id,
            "table": table.name,
            "record_id": str(row["id"]),
            "field": col.name,
        }
        changes[col.name] = fields.SealedField(
            version,
            sdk.encrypt(
                json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode(),
                sdk.raw_keyring(secret.plaintext, name=fields.FORMAT),
                context,
            ),
        )
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
    db: Session, key: BankEncryptionKey
) -> Iterator[tuple[Table, list[ColumnElement[bool]], dict[str, object]]]:
    for name in ("bi_dim_counterparty", "bi_fact_position_daily", "bi_fact_position_eom"):
        table = Base.metadata.tables[name]
        predicates = [
            table.c.organization_id == key.organization_id,
            table.c.bank_id == key.bank_id,
        ]
        if name == "bi_dim_counterparty":
            for result in db.execute(select(table).where(*predicates)).mappings():
                row = cast(Mapping[str, object], result)
                alias = str(row["counterparty_id"])
                if (
                    row["name"] != alias
                    or row["source_reference"] != alias
                    or row["group_reference"] is not None
                ):
                    yield (
                        table,
                        [*predicates, table.c.counterparty_id == row["counterparty_id"]],
                        {"name": alias, "source_reference": alias, "group_reference": None},
                    )
        else:
            predicates.append(table.c.counterparty_group.is_not(None))
            if db.execute(select(table.c.bank_id).where(*predicates).limit(1)).first() is not None:
                yield table, predicates, {"counterparty_group": None}


def rewrite_bi_identities(db: Session, key: BankEncryptionKey) -> None:
    for table, predicates, values in bi_identity_updates(db, key):
        _ = db.execute(table.update().where(*predicates).values(**values))


def retained_fields(db: Session, key: BankEncryptionKey) -> dict[str, int]:
    retained: dict[str, int] = {}
    for name, columns in IMMUTABLE_FIELDS.items():
        table = Base.metadata.tables[name]
        count = sum(
            any(
                row[c] is not None
                and (
                    not isinstance(row[c], fields.SealedField)
                    or cast(fields.SealedField, row[c]).envelope_id != key.current_version_id
                )
                for c in columns
            )
            for row in db.execute(
                select(table).where(*scope(table, key.organization_id, key.bank_id))
            ).mappings()
        )
        if count:
            retained[name] = count
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
                select(func.count()).select_from(table).where(table.c[column] == key.organization_id)
            )
        )
        if count:
            retained[name + " (organization scope)"] = count
    return retained
