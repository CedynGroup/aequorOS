"""Corporate profile & related-party register (plan W4).

Mirrors the master data BoG's ORASS portal holds about each institution
(Reporting Institution Profile + Related Parties, LRT Portal User Guide):

- ``institution_profiles`` — 1:1 bank corporate profile (type, structure,
  authorisation/incorporation, TIN, ORASS institution code, exchange
  membership, local/foreign ownership split, parent country).
- ``related_parties`` + ``related_party_roles`` — individuals/legal entities
  with their capacities (shareholder, director, officer roles, external
  auditor, UBO, outsourced provider, liquidator) incl. director allowances
  and ICAG registration.
- ``shareholdings`` — share class/rights/counts/percentages per shareholder,
  optionally linked to an individual ultimate beneficial owner.
- ``outlets``, ``bank_products``, ``bank_licenses``, ``bank_name_history``.

All tables are tenant-scoped with RLS enabled + forced and composite FKs to
``banks(id, organization_id)``.

Revision ID: 202607240020
Revises: 202607240019
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "202607240020"
down_revision = "202607240019"
branch_labels = None
depends_on = None

_PARTY_TYPES = ("individual", "legal_entity")
_PARTY_STATUSES = ("active", "inactive")
_RELATED_PARTY_ROLES = (
    "shareholder",
    "director",
    "board_chairman",
    "board_secretary",
    "chief_executive_officer",
    "chief_finance_officer",
    "chief_risk_officer",
    "chief_compliance_officer",
    "head_internal_audit",
    "other_key_management",
    "external_auditor",
    "ultimate_beneficial_owner",
    "outsourced_company",
    "outsourced_individual",
    "liquidator",
)
_SHAREHOLDER_RIGHTS = ("voting", "non_voting")
_OUTLET_TYPES = ("head_office", "branch", "agency")
_OUTLET_STATUSES = ("active", "closed")
_PRODUCT_STATUSES = ("proposed", "approved", "withdrawn")
_LICENSE_STATUSES = ("active", "revoked", "expired")

NEW_TABLES = (
    "institution_profiles",
    "related_parties",
    "related_party_roles",
    "shareholdings",
    "outlets",
    "bank_products",
    "bank_licenses",
    "bank_name_history",
)


def _values(options: tuple[str, ...]) -> str:
    return ", ".join(f"'{option}'" for option in options)


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "institution_profiles",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("bank_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("institution_type", sa.String(length=60), nullable=False),
        sa.Column("legal_entity_structure", sa.String(length=60), nullable=False),
        sa.Column("authorisation_date", sa.Date(), nullable=True),
        sa.Column("approved_capital", sa.Numeric(20, 2), nullable=True),
        sa.Column("incorporation_date", sa.Date(), nullable=True),
        sa.Column("tin", sa.String(length=40), nullable=True),
        sa.Column("registration_number", sa.String(length=40), nullable=True),
        sa.Column("orass_institution_code", sa.String(length=40), nullable=True),
        sa.Column(
            "traded_on_exchange",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("exchange_name", sa.String(length=80), nullable=True),
        sa.Column("isin", sa.String(length=20), nullable=True),
        sa.Column("ownership_local_pct", sa.Numeric(5, 2), nullable=True),
        sa.Column("ownership_foreign_pct", sa.Numeric(5, 2), nullable=True),
        sa.Column("parent_country_code", sa.String(length=8), nullable=True),
        *_timestamps(),
        sa.CheckConstraint(
            "ownership_local_pct IS NULL OR "
            "(ownership_local_pct >= 0 AND ownership_local_pct <= 100)",
            name="ck_institution_profiles_ownership_local_pct",
        ),
        sa.CheckConstraint(
            "ownership_foreign_pct IS NULL OR "
            "(ownership_foreign_pct >= 0 AND ownership_foreign_pct <= 100)",
            name="ck_institution_profiles_ownership_foreign_pct",
        ),
        sa.ForeignKeyConstraint(
            ["bank_id", "organization_id"],
            ["banks.id", "banks.organization_id"],
        ),
        sa.ForeignKeyConstraint(["parent_country_code"], ["jurisdictions.code"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "organization_id", name="uq_institution_profiles_id_org"),
        sa.UniqueConstraint("organization_id", "bank_id", name="uq_institution_profiles_org_bank"),
    )

    op.create_table(
        "related_parties",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("bank_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("party_type", sa.String(length=16), nullable=False),
        sa.Column("full_name", sa.String(length=200), nullable=False),
        sa.Column(
            "contact",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "regulated_elsewhere",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("regulated_jurisdiction", sa.String(length=80), nullable=True),
        sa.Column("status", sa.String(length=12), nullable=False),
        *_timestamps(),
        sa.CheckConstraint(
            f"party_type IN ({_values(_PARTY_TYPES)})",
            name="ck_related_parties_party_type",
        ),
        sa.CheckConstraint(
            f"status IN ({_values(_PARTY_STATUSES)})",
            name="ck_related_parties_status",
        ),
        sa.ForeignKeyConstraint(
            ["bank_id", "organization_id"],
            ["banks.id", "banks.organization_id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "organization_id", name="uq_related_parties_id_org"),
    )
    op.create_index(
        "ix_related_parties_org_bank", "related_parties", ["organization_id", "bank_id"]
    )

    op.create_table(
        "related_party_roles",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("party_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("role", sa.String(length=40), nullable=False),
        sa.Column("other_responsibilities", sa.Text(), nullable=True),
        sa.Column("appointed_on", sa.Date(), nullable=True),
        sa.Column("term_of_appointment", sa.String(length=80), nullable=True),
        sa.Column("sitting_allowance", sa.Numeric(14, 2), nullable=True),
        sa.Column("travel_allowance", sa.Numeric(14, 2), nullable=True),
        sa.Column("annual_fees", sa.Numeric(14, 2), nullable=True),
        sa.Column("icag_registration", sa.String(length=60), nullable=True),
        *_timestamps(),
        sa.CheckConstraint(
            f"role IN ({_values(_RELATED_PARTY_ROLES)})",
            name="ck_related_party_roles_role",
        ),
        sa.ForeignKeyConstraint(
            ["party_id", "organization_id"],
            ["related_parties.id", "related_parties.organization_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "organization_id", name="uq_related_party_roles_id_org"),
    )
    op.create_index(
        "ix_related_party_roles_org_party",
        "related_party_roles",
        ["organization_id", "party_id"],
    )

    op.create_table(
        "shareholdings",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("party_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("share_type", sa.String(length=60), nullable=False),
        sa.Column("share_subtype", sa.String(length=60), nullable=True),
        sa.Column("shareholder_rights", sa.String(length=12), nullable=False),
        sa.Column("number_of_shares", sa.Numeric(20, 2), nullable=False),
        sa.Column("pct_shareholding", sa.Numeric(7, 4), nullable=False),
        sa.Column("ubo_party_id", postgresql.UUID(as_uuid=True), nullable=True),
        *_timestamps(),
        sa.CheckConstraint(
            f"shareholder_rights IN ({_values(_SHAREHOLDER_RIGHTS)})",
            name="ck_shareholdings_shareholder_rights",
        ),
        sa.CheckConstraint(
            "pct_shareholding >= 0 AND pct_shareholding <= 100",
            name="ck_shareholdings_pct_shareholding",
        ),
        sa.ForeignKeyConstraint(
            ["party_id", "organization_id"],
            ["related_parties.id", "related_parties.organization_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["ubo_party_id", "organization_id"],
            ["related_parties.id", "related_parties.organization_id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "organization_id", name="uq_shareholdings_id_org"),
    )
    op.create_index("ix_shareholdings_org_party", "shareholdings", ["organization_id", "party_id"])

    op.create_table(
        "outlets",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("bank_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("outlet_type", sa.String(length=16), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("outlet_number", sa.String(length=40), nullable=True),
        sa.Column(
            "address",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column("opened_on", sa.Date(), nullable=True),
        sa.Column("closed_on", sa.Date(), nullable=True),
        sa.Column("relocated_from", sa.String(length=200), nullable=True),
        *_timestamps(),
        sa.CheckConstraint(
            f"outlet_type IN ({_values(_OUTLET_TYPES)})",
            name="ck_outlets_outlet_type",
        ),
        sa.CheckConstraint(
            f"status IN ({_values(_OUTLET_STATUSES)})",
            name="ck_outlets_status",
        ),
        sa.ForeignKeyConstraint(
            ["bank_id", "organization_id"],
            ["banks.id", "banks.organization_id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "organization_id", name="uq_outlets_id_org"),
    )
    op.create_index("ix_outlets_org_bank", "outlets", ["organization_id", "bank_id"])

    op.create_table(
        "bank_products",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("bank_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("product_type", sa.String(length=80), nullable=False),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column("approval_reference", sa.String(length=80), nullable=True),
        *_timestamps(),
        sa.CheckConstraint(
            f"status IN ({_values(_PRODUCT_STATUSES)})",
            name="ck_bank_products_status",
        ),
        sa.ForeignKeyConstraint(
            ["bank_id", "organization_id"],
            ["banks.id", "banks.organization_id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "organization_id", name="uq_bank_products_id_org"),
    )
    op.create_index("ix_bank_products_org_bank", "bank_products", ["organization_id", "bank_id"])

    op.create_table(
        "bank_licenses",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("bank_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("license_name", sa.String(length=120), nullable=False),
        sa.Column("license_class", sa.String(length=60), nullable=True),
        sa.Column("issued_on", sa.Date(), nullable=True),
        sa.Column("status", sa.String(length=12), nullable=False),
        *_timestamps(),
        sa.CheckConstraint(
            f"status IN ({_values(_LICENSE_STATUSES)})",
            name="ck_bank_licenses_status",
        ),
        sa.ForeignKeyConstraint(
            ["bank_id", "organization_id"],
            ["banks.id", "banks.organization_id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "organization_id", name="uq_bank_licenses_id_org"),
    )
    op.create_index("ix_bank_licenses_org_bank", "bank_licenses", ["organization_id", "bank_id"])

    op.create_table(
        "bank_name_history",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("bank_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("previous_name", sa.String(length=200), nullable=False),
        sa.Column("changed_on", sa.Date(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["bank_id", "organization_id"],
            ["banks.id", "banks.organization_id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "organization_id", name="uq_bank_name_history_id_org"),
    )
    op.create_index(
        "ix_bank_name_history_org_bank", "bank_name_history", ["organization_id", "bank_id"]
    )

    # Tenant isolation, same posture as every reporting table.
    for table in NEW_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} "
            "USING (organization_id = current_setting('app.organization_id')::uuid) "
            "WITH CHECK (organization_id = current_setting('app.organization_id')::uuid)"
        )


def downgrade() -> None:
    for table in reversed(NEW_TABLES):
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")

    op.drop_index("ix_bank_name_history_org_bank", table_name="bank_name_history")
    op.drop_table("bank_name_history")
    op.drop_index("ix_bank_licenses_org_bank", table_name="bank_licenses")
    op.drop_table("bank_licenses")
    op.drop_index("ix_bank_products_org_bank", table_name="bank_products")
    op.drop_table("bank_products")
    op.drop_index("ix_outlets_org_bank", table_name="outlets")
    op.drop_table("outlets")
    op.drop_index("ix_shareholdings_org_party", table_name="shareholdings")
    op.drop_table("shareholdings")
    op.drop_index("ix_related_party_roles_org_party", table_name="related_party_roles")
    op.drop_table("related_party_roles")
    op.drop_index("ix_related_parties_org_bank", table_name="related_parties")
    op.drop_table("related_parties")
    op.drop_table("institution_profiles")
