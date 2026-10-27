"""Record immutable bank data-key versions and their exact wrapping references.

Revision ID: 202610220102
Revises: 202610220101
"""

from typing import cast

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session, load_only

from alembic import op
from app.core.key_management import registry
from app.core.key_management.models import BankEncryptionKey
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


def backfill_versions(connection: Connection) -> None:
    with Session(bind=connection) as db:
        for key in db.scalars(
            sa.select(BankEncryptionKey).options(
                load_only(
                    BankEncryptionKey.id,
                    BankEncryptionKey.bank_id,
                    BankEncryptionKey.organization_id,
                    BankEncryptionKey.storage_slug,
                    BankEncryptionKey.provider,
                    BankEncryptionKey.key_id,
                    BankEncryptionKey.region,
                    BankEncryptionKey.owner_account,
                    BankEncryptionKey.status,
                    BankEncryptionKey.current_version_id,
                    BankEncryptionKey.index_version_id,
                    BankEncryptionKey.checked_at,
                    BankEncryptionKey.created_at,
                    BankEncryptionKey.updated_at,
                    raiseload=True,
                )
            )
        ).all():
            db.info["organization_id"] = key.organization_id
            if connection.dialect.name == "postgresql":
                _ = db.execute(
                    sa.text("SELECT set_config('app.organization_id', :organization, true)"),
                    {"organization": key.organization_id},
                )
            registry.initialize_version(db, key, registry.provider_for)
        db.commit()


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
