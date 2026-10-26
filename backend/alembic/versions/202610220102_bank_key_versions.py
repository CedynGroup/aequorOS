"""Record immutable bank data-key versions and their exact wrapping references.

Revision ID: 202610220102
Revises: 202610220101
"""

from typing import cast

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op
from app.db.session import force_rls_suspended

revision = "202610220102"
down_revision = "202610220101"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for name, size in (("provider", 32), ("key_id", 2048), ("region", 64), ("owner_account", 12)):
        op.add_column("object_key_envelopes", sa.Column(name, sa.String(size), nullable=True))
    with force_rls_suspended(op.get_bind(), "object_key_envelopes"):
        op.execute(
            sa.text("""
            UPDATE object_key_envelopes AS e
            SET provider=k.provider, key_id=k.key_id, region=k.region, owner_account=k.owner_account
            FROM bank_encryption_keys AS k
            WHERE e.bank_id=k.bank_id AND e.organization_id=k.organization_id
        """)
        )
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
