"""regulatory reporting: basis dimension, DBK daily family, deadline overrides

Revision ID: 202607240023
Revises: 202607240022

W6 remainder (docs/submission_pipeline_plan.md §W6 items 1, 6, 7):

- Item 6 — adds the SOLO|CONSOLIDATED ``basis`` dimension to
  ``regulatory_packages`` (server_default 'solo' backfills existing rows) and
  rebuilds the partial ``uq_regulatory_packages_current`` index to include
  ``basis`` so solo and consolidated returns are independent current-version
  chains for the same (return_code, reporting_date).
- Item 1 — widens the frequency CHECK to admit ``daily`` and the return-family
  CHECK to admit ``dbk`` for the DBK daily return family.
- Item 7 — creates ``regulatory_reporting_settings`` (one row per org+bank,
  RLS-forced) holding the per-bank ``{return_code: day_of_month}`` deadline
  override map.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "202607240023"
down_revision = "202607240022"
branch_labels = None
depends_on = None

FREQUENCIES_ORIGINAL = "'monthly', 'quarterly', 'semiannual', 'annual'"
FREQUENCIES_WIDENED = "'monthly', 'quarterly', 'semiannual', 'annual', 'daily'"
FAMILIES_ORIGINAL = (
    "'liquidity', 'capital', 'irrbb', 'fx', 'icaap_stress', 'corporate', 'large_exposures'"
)
FAMILIES_WIDENED = (
    "'liquidity', 'capital', 'irrbb', 'fx', 'icaap_stress', 'corporate', "
    "'large_exposures', 'dbk'"
)

SETTINGS_TABLE = "regulatory_reporting_settings"


def upgrade() -> None:
    # --- Item 6: basis dimension -----------------------------------------
    op.add_column(
        "regulatory_packages",
        sa.Column(
            "basis",
            sa.String(length=12),
            nullable=False,
            server_default="solo",
        ),
    )
    op.create_check_constraint(
        "ck_regulatory_packages_basis",
        "regulatory_packages",
        "basis IN ('solo', 'consolidated')",
    )
    # Rebuild the one-current-version index to key on basis as well.
    op.drop_index("uq_regulatory_packages_current", table_name="regulatory_packages")
    op.create_index(
        "uq_regulatory_packages_current",
        "regulatory_packages",
        ["organization_id", "bank_id", "return_code", "reporting_date", "basis"],
        unique=True,
        postgresql_where=sa.text("status != 'superseded'"),
        sqlite_where=sa.text("status != 'superseded'"),
    )

    # --- Item 1: DBK daily family (widen CHECK constraints) --------------
    op.drop_constraint("ck_regulatory_packages_frequency", "regulatory_packages", type_="check")
    op.create_check_constraint(
        "ck_regulatory_packages_frequency",
        "regulatory_packages",
        f"frequency IN ({FREQUENCIES_WIDENED})",
    )
    op.drop_constraint(
        "ck_regulatory_packages_return_family", "regulatory_packages", type_="check"
    )
    op.create_check_constraint(
        "ck_regulatory_packages_return_family",
        "regulatory_packages",
        f"return_family IN ({FAMILIES_WIDENED})",
    )

    # --- Item 7: per-bank deadline overrides ----------------------------
    op.create_table(
        SETTINGS_TABLE,
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("bank_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "deadline_overrides",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["bank_id", "organization_id"],
            ["banks.id", "banks.organization_id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "id", "organization_id", name="uq_regulatory_reporting_settings_id_org"
        ),
        sa.UniqueConstraint(
            "organization_id",
            "bank_id",
            name="uq_regulatory_reporting_settings_scope",
        ),
    )
    op.create_index(
        "ix_regulatory_reporting_settings_org_bank",
        SETTINGS_TABLE,
        ["organization_id", "bank_id"],
    )
    # Tenant isolation, same posture as every reporting table.
    op.execute(f"ALTER TABLE {SETTINGS_TABLE} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {SETTINGS_TABLE} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON {SETTINGS_TABLE} "
        "USING (organization_id = current_setting('app.organization_id')::uuid) "
        "WITH CHECK (organization_id = current_setting('app.organization_id')::uuid)"
    )

    # GAP-5: SMTP-mirror outbox stamp on notifications.
    op.add_column(
        "notifications",
        sa.Column("emailed_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("notifications", "emailed_at")
    op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {SETTINGS_TABLE}")
    op.drop_index("ix_regulatory_reporting_settings_org_bank", table_name=SETTINGS_TABLE)
    op.drop_table(SETTINGS_TABLE)

    # Reversal deletes daily / dbk rows before restoring the narrower CHECKs so
    # the constraint reinstatement never fails on live data.
    op.execute("DELETE FROM regulatory_packages WHERE frequency = 'daily'")
    op.execute("DELETE FROM regulatory_packages WHERE return_family = 'dbk'")
    op.drop_constraint(
        "ck_regulatory_packages_return_family", "regulatory_packages", type_="check"
    )
    op.create_check_constraint(
        "ck_regulatory_packages_return_family",
        "regulatory_packages",
        f"return_family IN ({FAMILIES_ORIGINAL})",
    )
    op.drop_constraint("ck_regulatory_packages_frequency", "regulatory_packages", type_="check")
    op.create_check_constraint(
        "ck_regulatory_packages_frequency",
        "regulatory_packages",
        f"frequency IN ({FREQUENCIES_ORIGINAL})",
    )

    op.drop_index("uq_regulatory_packages_current", table_name="regulatory_packages")
    op.create_index(
        "uq_regulatory_packages_current",
        "regulatory_packages",
        ["organization_id", "bank_id", "return_code", "reporting_date"],
        unique=True,
        postgresql_where=sa.text("status != 'superseded'"),
        sqlite_where=sa.text("status != 'superseded'"),
    )
    op.drop_constraint("ck_regulatory_packages_basis", "regulatory_packages", type_="check")
    op.drop_column("regulatory_packages", "basis")
