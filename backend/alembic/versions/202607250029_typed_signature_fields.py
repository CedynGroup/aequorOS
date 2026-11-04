"""typed signature fields: name / designation / signature / date per signer

Revision ID: 202607250029
Revises: 202607250028

A BoG attestation block asks each officer for four things — "Prepared by (name /
designation / signature / date)" — and the placement tables modelled one box per
signing role, so the form could not be completed as built. Both tables gain
``field_type`` and ``field_index``, and the uniqueness that was one row per
(scope, role) becomes one row per (scope, role, kind, ordinal).

Existing rows are all signature boxes, which is exactly what the server defaults
say, so the backfill is the default itself: every column is NOT NULL with a
server default of ``'signature'`` / ``1``. No data moves.

The two partial indexes on ``return_signature_placements`` are dropped and
recreated rather than altered — Postgres cannot add a column to an index in
place, and a unique index that still keyed on (scope, role) alone would refuse
the second box the whole change exists to allow.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "202607250029"
down_revision = "202607250028"
branch_labels = None
depends_on = None

_TABLES = ("return_signature_placements", "package_signature_placements")

_FIELD_TYPE_VALUES = "'signature', 'initials', 'name', 'title', 'date_signed'"


def upgrade() -> None:
    for table in _TABLES:
        op.add_column(
            table,
            sa.Column(
                "field_type",
                sa.String(length=16),
                nullable=False,
                server_default="signature",
            ),
        )
        op.add_column(
            table,
            sa.Column("field_index", sa.Integer(), nullable=False, server_default="1"),
        )
        op.create_check_constraint(
            f"ck_{table}_field_type", table, f"field_type IN ({_FIELD_TYPE_VALUES})"
        )
        op.create_check_constraint(f"ck_{table}_index", table, "field_index >= 1")

    # SQLite cannot drop a named unique constraint, and the hermetic suite builds
    # its schema from the models rather than by migrating, so the constraint
    # swap is Postgres-only. Both engines end up with the same shape.
    with op.batch_alter_table("package_signature_placements") as batch:
        batch.drop_constraint("uq_package_signature_placements_role", type_="unique")
        batch.create_unique_constraint(
            "uq_package_signature_placements_role",
            ["organization_id", "package_id", "signing_role", "field_type", "field_index"],
        )

    op.drop_index(
        "uq_return_signature_placements_bank", table_name="return_signature_placements"
    )
    op.drop_index(
        "uq_return_signature_placements_org", table_name="return_signature_placements"
    )
    op.create_index(
        "uq_return_signature_placements_bank",
        "return_signature_placements",
        [
            "organization_id",
            "bank_id",
            "return_code",
            "signing_role",
            "field_type",
            "field_index",
        ],
        unique=True,
        postgresql_where=sa.text("bank_id IS NOT NULL"),
        sqlite_where=sa.text("bank_id IS NOT NULL"),
    )
    op.create_index(
        "uq_return_signature_placements_org",
        "return_signature_placements",
        ["organization_id", "return_code", "signing_role", "field_type", "field_index"],
        unique=True,
        postgresql_where=sa.text("bank_id IS NULL"),
        sqlite_where=sa.text("bank_id IS NULL"),
    )


def downgrade() -> None:
    # A downgrade can only keep the signature boxes: every other kind of box is
    # a row the old schema has no column to describe, and silently collapsing
    # them onto the signature row would move a field on a document.
    for table in _TABLES:
        op.execute(sa.text(f"DELETE FROM {table} WHERE field_type <> 'signature'"))  # noqa: S608

    op.drop_index(
        "uq_return_signature_placements_bank", table_name="return_signature_placements"
    )
    op.drop_index(
        "uq_return_signature_placements_org", table_name="return_signature_placements"
    )
    op.create_index(
        "uq_return_signature_placements_bank",
        "return_signature_placements",
        ["organization_id", "bank_id", "return_code", "signing_role"],
        unique=True,
        postgresql_where=sa.text("bank_id IS NOT NULL"),
        sqlite_where=sa.text("bank_id IS NOT NULL"),
    )
    op.create_index(
        "uq_return_signature_placements_org",
        "return_signature_placements",
        ["organization_id", "return_code", "signing_role"],
        unique=True,
        postgresql_where=sa.text("bank_id IS NULL"),
        sqlite_where=sa.text("bank_id IS NULL"),
    )

    with op.batch_alter_table("package_signature_placements") as batch:
        batch.drop_constraint("uq_package_signature_placements_role", type_="unique")
        batch.create_unique_constraint(
            "uq_package_signature_placements_role",
            ["organization_id", "package_id", "signing_role"],
        )

    for table in _TABLES:
        op.drop_constraint(f"ck_{table}_index", table, type_="check")
        op.drop_constraint(f"ck_{table}_field_type", table, type_="check")
        op.drop_column(table, "field_index")
        op.drop_column(table, "field_type")
