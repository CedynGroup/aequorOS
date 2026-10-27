"""Record immutable bank data-key versions and their exact wrapping references.

Revision ID: 202610220102
Revises: 202610220101
"""

import hashlib
import hmac
from collections.abc import Mapping
from typing import cast
from uuid import UUID, uuid4

import sqlalchemy as sa
from pydantic import TypeAdapter
from sqlalchemy.dialects import postgresql
from sqlalchemy.engine import Connection

from alembic import op
from app.core.key_management import fields, registry, sdk
from app.core.key_management.types import KeyIntegrityError, KeyReference
from app.db.base import utc_now
from app.db.session import force_rls_suspended

revision = "202610220102"
down_revision = "202610220101"
branch_labels = None
depends_on = None


def backfill_references(connection: Connection) -> None:
    metadata = sa.MetaData()
    envelopes = sa.Table("object_key_envelopes", metadata, autoload_with=connection)
    keys = sa.Table("bank_encryption_keys", metadata, autoload_with=connection)
    _ = connection.execute(
        envelopes.update()
        .where(
            envelopes.c.bank_id == keys.c.bank_id,
            envelopes.c.organization_id == keys.c.organization_id,
        )
        .values(
            **{name: keys.c[name] for name in ("provider", "key_id", "region", "owner_account")}
        )
    )


def _reference(row: Mapping[str, object]) -> KeyReference:
    return KeyReference(
        cast(str, row["provider"]),
        cast(str, row["key_id"]),
        cast(str, row["region"]),
        cast(str, row["owner_account"]),
    )


def _secret(
    connection: Connection,
    envelopes: sa.Table,
    key: Mapping[str, object],
    version: UUID,
    context: dict[str, str],
) -> bytes:
    envelope = cast(
        Mapping[str, object],
        connection.execute(
            sa.select(envelopes).where(
                envelopes.c.id == version,
                envelopes.c.bank_id == key["bank_id"],
                envelopes.c.organization_id == key["organization_id"],
            )
        )
        .mappings()
        .one(),
    )
    version_digest = registry.context_digest(
        registry.version_context(cast(str, key["organization_id"]), cast(str, key["bank_id"]))
    )
    digest = cast(str, envelope["context_digest"])
    if digest not in {version_digest, registry.context_digest(context)}:
        raise KeyIntegrityError("The data key does not belong to this bank and record.")
    plaintext = registry.provider_for(cast(str, envelope["provider"])).unwrap(
        _reference(envelope),
        cast(bytes, envelope["wrapped_key"]),
        registry.wrapping_context(cast(str, key["bank_id"]), digest),
    )
    if digest == version_digest:
        plaintext = hmac.new(
            plaintext, ("bank-payload:" + registry.context_digest(context)).encode(), hashlib.sha256
        ).digest()
    return plaintext


def _reindex(
    connection: Connection, envelopes: sa.Table, key: Mapping[str, object], index: UUID
) -> None:
    organization, bank = cast(str, key["organization_id"]), cast(str, key["bank_id"])
    secret = _secret(
        connection,
        envelopes,
        key,
        index,
        {
            "purpose": "bank-sensitive-index-v1",
            "organization_id": organization,
            "bank_id": bank,
        },
    )
    for name, protected in (
        (
            "canonical_counterparties",
            {
                "name": fields.ProtectedText(),
                "source_reference": fields.ProtectedText(),
                "group_reference": fields.ProtectedText(),
                "external_identifiers": fields.ProtectedJSON(),
            },
        ),
        ("related_parties", {"full_name": fields.ProtectedText()}),
    ):
        table = sa.Table(
            name,
            sa.MetaData(),
            sa.Column("id", sa.Uuid(), primary_key=True),
            autoload_with=connection,
        )
        predicates = (table.c.organization_id == organization, table.c.bank_id == bank)
        if name == "canonical_counterparties":
            _ = connection.execute(
                table.update().where(*predicates).values(source_reference_blind_index=None)
            )
        for result in connection.execute(sa.select(table).where(*predicates)).mappings().all():
            row = cast(Mapping[str, object], result)
            values = dict(row)
            for column, kind in protected.items():
                value = kind.process_result_value(row[column], connection.dialect)
                if isinstance(value, fields.SealedField):
                    data = _secret(
                        connection,
                        envelopes,
                        key,
                        value.envelope_id,
                        {
                            "purpose": "bank-sensitive-fields-v1",
                            "organization_id": organization,
                            "bank_id": bank,
                        },
                    )
                    value = TypeAdapter(object).validate_json(
                        sdk.decrypt(
                            value.message,
                            sdk.raw_keyring(data, name=fields.FORMAT),
                            {
                                "purpose": "bank-sensitive-record-v1",
                                "organization_id": organization,
                                "bank_id": bank,
                                "table": name,
                                "record_id": str(row["id"]),
                                "field": column,
                            },
                        )
                    )
                values[column] = value
            indexes = fields.index_values(name, values, organization, bank, secret)
            _ = connection.execute(
                table.update().where(*predicates, table.c.id == row["id"]).values(**indexes)
            )


def backfill_versions(connection: Connection) -> None:
    metadata = sa.MetaData()
    keys = sa.Table(
        "bank_encryption_keys",
        metadata,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("current_version_id", sa.Uuid()),
        sa.Column("index_version_id", sa.Uuid()),
        autoload_with=connection,
        extend_existing=True,
    )
    envelopes = sa.Table(
        "object_key_envelopes",
        metadata,
        sa.Column("id", sa.Uuid(), primary_key=True),
        autoload_with=connection,
        extend_existing=True,
    )
    for result in connection.execute(sa.select(keys)).mappings().all():
        key = cast(Mapping[str, object], result)
        organization, bank = cast(str, key["organization_id"]), cast(str, key["bank_id"])
        if connection.dialect.name == "postgresql":
            _ = connection.execute(
                sa.text("SELECT set_config('app.organization_id', :organization, true)"),
                {"organization": organization},
            )
        if key["current_version_id"] is not None:
            continue
        reference = registry.require_active(
            registry.provider_for(cast(str, key["provider"])).describe(_reference(key))
        )
        provider = registry.provider_for(reference.provider)
        digest = registry.context_digest(registry.version_context(organization, bank))
        context = registry.wrapping_context(bank, digest)
        data = provider.generate_data_key(reference, context)
        if provider.unwrap(reference, data.wrapped, context) != data.plaintext:
            raise KeyIntegrityError("The bank key failed its wrap/unwrap probe.")
        version = uuid4()
        _ = connection.execute(
            envelopes.insert().values(
                id=version,
                bank_id=bank,
                organization_id=organization,
                context_digest=digest,
                wrapped_key=data.wrapped,
                created_at=utc_now(),
                provider=reference.provider,
                key_id=reference.key_id,
                region=reference.region,
                owner_account=reference.owner_account,
            )
        )
        index = (
            connection.scalar(
                sa.select(envelopes.c.id)
                .where(
                    envelopes.c.organization_id == organization,
                    envelopes.c.bank_id == bank,
                    envelopes.c.context_digest
                    == registry.context_digest(
                        {
                            "purpose": "bank-sensitive-index-v1",
                            "organization_id": organization,
                            "bank_id": bank,
                        }
                    ),
                )
                .order_by(envelopes.c.created_at, envelopes.c.id)
                .limit(1)
            )
            or version
        )
        _ = connection.execute(
            keys.update()
            .where(keys.c.id == key["id"])
            .values(current_version_id=version, index_version_id=index)
        )
        _reindex(connection, envelopes, key, cast(UUID, index))
    if connection.dialect.name == "postgresql":
        _ = connection.execute(
            sa.text("SET CONSTRAINTS fk_bank_current_version, fk_bank_index_version IMMEDIATE")
        )
        _ = connection.execute(
            sa.text("SET CONSTRAINTS fk_bank_current_version, fk_bank_index_version DEFERRED")
        )


def upgrade() -> None:
    for name, size in (("provider", 32), ("key_id", 2048), ("region", 64), ("owner_account", 12)):
        op.add_column("object_key_envelopes", sa.Column(name, sa.String(size), nullable=True))
    connection = op.get_bind()
    with force_rls_suspended(connection, "object_key_envelopes"):
        backfill_references(connection)
    for name in ("provider", "key_id", "region", "owner_account"):
        op.alter_column("object_key_envelopes", name, nullable=False)
    op.create_unique_constraint(
        "uq_object_envelope_scope", "object_key_envelopes", ["id", "bank_id", "organization_id"]
    )
    for name in ("current_version_id", "index_version_id"):
        op.add_column(
            "bank_encryption_keys", sa.Column(name, postgresql.UUID(as_uuid=True), nullable=True)
        )
        op.create_foreign_key(
            "fk_bank_" + ("current" if name == "current_version_id" else "index") + "_version",
            "bank_encryption_keys",
            "object_key_envelopes",
            [name, "bank_id", "organization_id"],
            ["id", "bank_id", "organization_id"],
            deferrable=True,
            initially="DEFERRED",
        )
    backfill_versions(connection)


def downgrade() -> None:
    with force_rls_suspended(op.get_bind(), "object_key_envelopes"):
        if cast(
            bool,
            op.get_bind().scalar(sa.text("SELECT EXISTS (SELECT 1 FROM object_key_envelopes)")),
        ):
            raise RuntimeError("Bank-encrypted data prohibits schema downgrade.")
    for name in ("current", "index"):
        op.drop_constraint(f"fk_bank_{name}_version", "bank_encryption_keys", type_="foreignkey")
        op.drop_column("bank_encryption_keys", f"{name}_version_id")
    op.drop_constraint("uq_object_envelope_scope", "object_key_envelopes", type_="unique")
    for name in ("provider", "key_id", "region", "owner_account"):
        op.drop_column("object_key_envelopes", name)
