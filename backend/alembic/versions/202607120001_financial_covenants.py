"""financial covenants

Revision ID: 202607120001
Revises: 202606080001
Create Date: 2026-07-12 00:01:00.000000
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "202607120001"
down_revision = "202606080001"
branch_labels = None
depends_on = None

TENANT_ID_EXPR = "nullif(current_setting('app.organization_id', true), '')::uuid"
OLD_RECORD_TABLES = (
    "'financial_institutions', 'financial_accounts', "
    "'financial_reporting_periods', 'financial_balances', "
    "'financial_cash_flows', 'financial_obligations'"
)
NEW_RECORD_TABLES = f"{OLD_RECORD_TABLES}, 'financial_covenants'"


def jsonb_default() -> sa.TextClause:
    return sa.text("'{}'::jsonb")


def upgrade() -> None:
    op.create_table(
        "financial_covenants",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("dedupe_key", sa.String(length=96), nullable=False),
        sa.Column("obligation_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("reporting_period_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("metric", sa.String(length=120), nullable=False),
        sa.Column("operator", sa.String(length=12), nullable=False),
        sa.Column("threshold", sa.Numeric(20, 6), nullable=False),
        sa.Column("actual_value", sa.Numeric(20, 6), nullable=True),
        sa.Column("compliance_status", sa.String(length=40), nullable=False),
        sa.Column(
            "source_record",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=jsonb_default(),
        ),
        sa.Column(
            "reporting_context",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=jsonb_default(),
        ),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=jsonb_default(),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["case_id", "organization_id"],
            ["risk_cases.id", "risk_cases.organization_id"],
        ),
        sa.ForeignKeyConstraint(
            ["obligation_id", "organization_id", "case_id"],
            [
                "financial_obligations.id",
                "financial_obligations.organization_id",
                "financial_obligations.case_id",
            ],
        ),
        sa.ForeignKeyConstraint(
            ["reporting_period_id", "organization_id", "case_id"],
            [
                "financial_reporting_periods.id",
                "financial_reporting_periods.organization_id",
                "financial_reporting_periods.case_id",
            ],
        ),
        sa.CheckConstraint(
            "operator IN ('lt', 'lte', 'eq', 'gte', 'gt')", name="ck_financial_covenants_operator"
        ),
        sa.CheckConstraint(
            "compliance_status IN ('compliant', 'non_compliant', 'unknown')",
            name="ck_financial_covenants_compliance_status",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "id",
            "organization_id",
            "case_id",
            name="uq_financial_covenants_id_organization_id_case_id",
        ),
    )
    op.create_index("ix_financial_covenants_case_id", "financial_covenants", ["case_id"])
    op.create_index(
        "ix_financial_covenants_obligation_id", "financial_covenants", ["obligation_id"]
    )
    op.create_index(
        "uq_financial_covenants_dedupe_key",
        "financial_covenants",
        ["dedupe_key", "organization_id", "case_id"],
        unique=True,
    )
    op.execute("ALTER TABLE financial_covenants ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE financial_covenants FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY financial_covenants_tenant_isolation
        ON financial_covenants
        USING (organization_id = {TENANT_ID_EXPR})
        WITH CHECK (organization_id = {TENANT_ID_EXPR})
        """
    )
    replace_shared_record_table_constraints(NEW_RECORD_TABLES)


def downgrade() -> None:
    op.execute(
        "DELETE FROM financial_record_source_links WHERE record_table = 'financial_covenants'"
    )
    op.execute(
        "DELETE FROM financial_manual_edit_history WHERE record_table = 'financial_covenants'"
    )
    op.execute("DELETE FROM financial_validation_issues WHERE record_table = 'financial_covenants'")
    replace_shared_record_table_constraints(OLD_RECORD_TABLES)
    op.execute("DROP POLICY IF EXISTS financial_covenants_tenant_isolation ON financial_covenants")
    op.execute("ALTER TABLE financial_covenants NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE financial_covenants DISABLE ROW LEVEL SECURITY")
    op.drop_table("financial_covenants")


def replace_shared_record_table_constraints(record_tables: str) -> None:
    op.drop_constraint(
        "ck_financial_record_source_links_record_table",
        "financial_record_source_links",
        type_="check",
    )
    op.create_check_constraint(
        "ck_financial_record_source_links_record_table",
        "financial_record_source_links",
        f"record_table IN ({record_tables})",
    )
    op.drop_constraint(
        "ck_financial_manual_edit_history_record_table",
        "financial_manual_edit_history",
        type_="check",
    )
    op.create_check_constraint(
        "ck_financial_manual_edit_history_record_table",
        "financial_manual_edit_history",
        f"record_table IN ({record_tables})",
    )
    op.drop_constraint(
        "ck_financial_validation_issues_record_reference",
        "financial_validation_issues",
        type_="check",
    )
    op.create_check_constraint(
        "ck_financial_validation_issues_record_reference",
        "financial_validation_issues",
        "((record_table IS NULL AND record_id IS NULL) OR "
        f"(record_table IS NOT NULL AND record_table IN ({record_tables}) "
        "AND record_id IS NOT NULL))",
    )
