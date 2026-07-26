"""signing workspace: field placement, adopted appearance, named recipients

Revision ID: 202607250028
Revises: 202607250027

The three tables the DocuSign-style ceremony needs, all ordinary tenant
configuration rather than evidence:

1. ``return_signature_placements`` / ``package_signature_placements`` — where
   each signing role's field sits, replacing the two hardcoded boxes in
   ``services/attestation/pdf_signing.py``. A template per return code
   (optionally per bank) plus a per-package override, resolved
   override → template → the built-in default.

2. ``signature_appearances`` — the drawn or typed mark an officer adopted. Only
   server-normalised PNG bytes are ever stored (re-rastered, metadata stripped).

3. ``package_signature_recipients`` — the named people routed to fill the
   policy's slots. Cycle-scoped, so a void leaves the withdrawn routing legible.

None of these are append-only, deliberately, and none are added to the
``202607250027`` guard lists. A placement is layout, an adopted mark is
presentation, and a recipient row transitions pending → signed: all three
legitimately change, and none of them is what a signature commits to. The
evidence tables that trigger-guard is protecting are untouched here.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "202607250028"
down_revision = "202607250027"
branch_labels = None
depends_on = None

_TENANT_TABLES = (
    "return_signature_placements",
    "package_signature_placements",
    "signature_appearances",
    "package_signature_recipients",
)

_ROLE_VALUES = "'preparer', 'approver', 'board', 'witness'"


def upgrade() -> None:
    op.create_table(
        "return_signature_placements",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.String(length=16), nullable=False),
        sa.Column("bank_id", sa.String(length=16), nullable=True),
        sa.Column("return_code", sa.String(length=40), nullable=False),
        sa.Column("signing_role", sa.String(length=16), nullable=False),
        sa.Column("page_index", sa.Integer(), nullable=False),
        sa.Column("x1", sa.Integer(), nullable=False),
        sa.Column("y1", sa.Integer(), nullable=False),
        sa.Column("x2", sa.Integer(), nullable=False),
        sa.Column("y2", sa.Integer(), nullable=False),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
        sa.Column("reason", sa.String(length=500), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            f"signing_role IN ({_ROLE_VALUES})", name="ck_return_signature_placements_role"
        ),
        sa.CheckConstraint("page_index >= 0", name="ck_return_signature_placements_page"),
        sa.CheckConstraint("x2 > x1 AND y2 > y1", name="ck_return_signature_placements_box"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "organization_id", name="uq_return_signature_placements_id_org"),
    )
    # Partial, because NULL bank_id means "every bank in the organization" and
    # Postgres treats NULLs as distinct — a plain four-column unique constraint
    # would admit two conflicting organization-wide rows for one return + role.
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

    op.create_table(
        "package_signature_placements",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.String(length=16), nullable=False),
        sa.Column("package_id", sa.Uuid(), nullable=False),
        sa.Column("signing_role", sa.String(length=16), nullable=False),
        sa.Column("page_index", sa.Integer(), nullable=False),
        sa.Column("x1", sa.Integer(), nullable=False),
        sa.Column("y1", sa.Integer(), nullable=False),
        sa.Column("x2", sa.Integer(), nullable=False),
        sa.Column("y2", sa.Integer(), nullable=False),
        sa.Column("placed_by", sa.Uuid(), nullable=True),
        sa.Column("reason", sa.String(length=500), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            f"signing_role IN ({_ROLE_VALUES})", name="ck_package_signature_placements_role"
        ),
        sa.CheckConstraint("page_index >= 0", name="ck_package_signature_placements_page"),
        sa.CheckConstraint("x2 > x1 AND y2 > y1", name="ck_package_signature_placements_box"),
        sa.ForeignKeyConstraint(
            ["package_id", "organization_id"],
            ["regulatory_packages.id", "regulatory_packages.organization_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id",
            "package_id",
            "signing_role",
            name="uq_package_signature_placements_role",
        ),
        sa.UniqueConstraint(
            "id", "organization_id", name="uq_package_signature_placements_id_org"
        ),
    )

    op.create_table(
        "signature_appearances",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.String(length=16), nullable=False),
        sa.Column("signer_id", sa.String(length=24), nullable=False),
        sa.Column("kind", sa.String(length=8), nullable=False),
        sa.Column("image_png", sa.LargeBinary(), nullable=True),
        sa.Column("image_width", sa.Integer(), nullable=True),
        sa.Column("image_height", sa.Integer(), nullable=True),
        sa.Column("typed_name", sa.String(length=120), nullable=True),
        sa.Column("typed_font", sa.String(length=40), nullable=True),
        sa.Column("adopted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("adopted_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("kind IN ('drawn', 'typed')", name="ck_signature_appearances_kind"),
        # The two kinds are mutually exclusive at the schema level: a row that
        # carried both an image and a typed name would leave the renderer to
        # guess which mark the officer actually adopted.
        sa.CheckConstraint(
            "(kind = 'drawn' AND image_png IS NOT NULL AND typed_name IS NULL) "
            "OR (kind = 'typed' AND typed_name IS NOT NULL AND image_png IS NULL)",
            name="ck_signature_appearances_payload",
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id", "signer_id", name="uq_signature_appearances_signer"
        ),
        sa.UniqueConstraint("id", "organization_id", name="uq_signature_appearances_id_org"),
    )

    op.create_table(
        "package_signature_recipients",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.String(length=16), nullable=False),
        sa.Column("package_id", sa.Uuid(), nullable=False),
        sa.Column("attestation_cycle", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("signing_role", sa.String(length=16), nullable=False),
        sa.Column("recipient_user_id", sa.Uuid(), nullable=False),
        sa.Column("recipient_signer_id", sa.String(length=24), nullable=False),
        sa.Column("recipient_display_name", sa.String(length=255), nullable=True),
        sa.Column("recipient_job_title", sa.String(length=120), nullable=True),
        sa.Column("routing_order", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("notified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("signed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("signature_id", sa.Uuid(), nullable=True),
        sa.Column("nominated_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            f"signing_role IN ({_ROLE_VALUES})", name="ck_package_signature_recipients_role"
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'signed')", name="ck_package_signature_recipients_status"
        ),
        sa.ForeignKeyConstraint(
            ["package_id", "organization_id"],
            ["regulatory_packages.id", "regulatory_packages.organization_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id",
            "package_id",
            "attestation_cycle",
            "signing_role",
            "recipient_user_id",
            name="uq_package_signature_recipients_nomination",
        ),
        sa.UniqueConstraint(
            "id", "organization_id", name="uq_package_signature_recipients_id_org"
        ),
    )
    op.create_index(
        "ix_package_signature_recipients_inbox",
        "package_signature_recipients",
        ["organization_id", "recipient_user_id", "status"],
    )

    if op.get_bind().dialect.name != "postgresql":
        return

    for table in _TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"""
            CREATE POLICY {table}_tenant_isolation ON {table}
            FOR ALL
            USING (
                (organization_id)::text
                = NULLIF(current_setting('app.organization_id', true), '')
            )
            WITH CHECK (
                (organization_id)::text
                = NULLIF(current_setting('app.organization_id', true), '')
            )
            """
        )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        for table in _TENANT_TABLES:
            op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table}")
    op.drop_index(
        "ix_package_signature_recipients_inbox", table_name="package_signature_recipients"
    )
    op.drop_table("package_signature_recipients")
    op.drop_table("signature_appearances")
    op.drop_table("package_signature_placements")
    op.drop_index(
        "uq_return_signature_placements_org", table_name="return_signature_placements"
    )
    op.drop_index(
        "uq_return_signature_placements_bank", table_name="return_signature_placements"
    )
    op.drop_table("return_signature_placements")
