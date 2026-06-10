"""financial cash flows

Revision ID: 202606080001
Revises: 202606010001
Create Date: 2026-06-08 00:01:00.000000
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "202606080001"
down_revision = "202606010001"
branch_labels = None
depends_on = None

TENANT_ID_EXPR = "nullif(current_setting('app.organization_id', true), '')::uuid"

OLD_RECORD_TABLES = (
    "'financial_institutions', 'financial_accounts', "
    "'financial_reporting_periods', 'financial_balances', "
    "'financial_obligations'"
)
NEW_RECORD_TABLES = (
    "'financial_institutions', 'financial_accounts', "
    "'financial_reporting_periods', 'financial_balances', "
    "'financial_cash_flows', 'financial_obligations'"
)


def jsonb_default() -> sa.TextClause:
    return sa.text("'{}'::jsonb")


def upgrade() -> None:
    op.create_table(
        "financial_cash_flows",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("dedupe_key", sa.String(length=96), nullable=False),
        sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("reporting_period_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("cash_flow_date", sa.Date(), nullable=True),
        sa.Column("amount", sa.Numeric(20, 4), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=True),
        sa.Column("direction", sa.String(length=40), nullable=False),
        sa.Column("category", sa.String(length=120), nullable=False),
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
            ["account_id", "organization_id", "case_id"],
            [
                "financial_accounts.id",
                "financial_accounts.organization_id",
                "financial_accounts.case_id",
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
            "currency IS NULL OR (length(currency) = 3 AND upper(currency) = currency)",
            name="ck_financial_cash_flows_currency",
        ),
        sa.CheckConstraint(
            "direction IN ('inflow', 'outflow')",
            name="ck_financial_cash_flows_direction",
        ),
        sa.CheckConstraint(
            "amount > 0",
            name="ck_financial_cash_flows_amount_positive",
        ),
        sa.CheckConstraint(
            "length(trim(category)) > 0",
            name="ck_financial_cash_flows_category",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_financial_cash_flows_case_id",
        "financial_cash_flows",
        ["case_id"],
    )
    op.create_index(
        "uq_financial_cash_flows_dedupe_key",
        "financial_cash_flows",
        ["dedupe_key", "organization_id", "case_id"],
        unique=True,
    )
    op.execute("ALTER TABLE financial_cash_flows ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE financial_cash_flows FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY financial_cash_flows_tenant_isolation
        ON financial_cash_flows
        USING (organization_id = {TENANT_ID_EXPR})
        WITH CHECK (organization_id = {TENANT_ID_EXPR})
        """
    )
    replace_shared_record_table_constraints(NEW_RECORD_TABLES)


def downgrade() -> None:
    op.execute(
        "DELETE FROM financial_record_source_links WHERE record_table = 'financial_cash_flows'"
    )
    op.execute(
        "DELETE FROM financial_manual_edit_history WHERE record_table = 'financial_cash_flows'"
    )
    op.execute(
        "DELETE FROM financial_validation_issues WHERE record_table = 'financial_cash_flows'"
    )
    replace_shared_record_table_constraints(OLD_RECORD_TABLES)
    op.execute(
        "DROP POLICY IF EXISTS financial_cash_flows_tenant_isolation ON financial_cash_flows"
    )
    op.execute("ALTER TABLE financial_cash_flows NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE financial_cash_flows DISABLE ROW LEVEL SECURITY")
    op.drop_table("financial_cash_flows")


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
