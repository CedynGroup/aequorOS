"""case financial workspace

Revision ID: 202606010001
Revises: 202605290001
Create Date: 2026-06-01 00:01:00.000000
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "202606010001"
down_revision = "202605290001"
branch_labels = None
depends_on = None

TENANT_ID_EXPR = "nullif(current_setting('app.organization_id', true), '')::uuid"

FINANCIAL_TABLES = [
    "financial_institutions",
    "financial_accounts",
    "financial_reporting_periods",
    "financial_balances",
    "financial_obligations",
    "financial_source_rows",
    "financial_record_source_links",
    "financial_manual_edit_history",
    "financial_validation_issues",
]


def jsonb_default() -> sa.TextClause:
    return sa.text("'{}'::jsonb")


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_risk_cases_id_organization_id",
        "risk_cases",
        ["id", "organization_id"],
    )
    op.create_unique_constraint(
        "uq_documents_id_organization_id_case_id",
        "documents",
        ["id", "organization_id", "case_id"],
    )
    op.create_unique_constraint(
        "uq_users_id_organization_id",
        "users",
        ["id", "organization_id"],
    )

    op.create_table(
        "financial_institutions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("dedupe_key", sa.String(length=96), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("institution_type", sa.String(length=120), nullable=True),
        sa.Column("reference_code", sa.String(length=120), nullable=True),
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
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "id",
            "organization_id",
            "case_id",
            name="uq_financial_institutions_id_organization_id_case_id",
        ),
    )
    op.create_index(
        "ix_financial_institutions_case_id",
        "financial_institutions",
        ["case_id"],
    )
    op.create_index(
        "uq_financial_institutions_dedupe_key",
        "financial_institutions",
        ["dedupe_key", "organization_id", "case_id"],
        unique=True,
    )
    op.create_table(
        "financial_accounts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("dedupe_key", sa.String(length=96), nullable=False),
        sa.Column("institution_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("account_number", sa.Text(), nullable=True),
        sa.Column("account_name", sa.Text(), nullable=False),
        sa.Column("account_type", sa.String(length=120), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=True),
        sa.Column("status", sa.String(length=40), nullable=True),
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
            ["institution_id", "organization_id", "case_id"],
            [
                "financial_institutions.id",
                "financial_institutions.organization_id",
                "financial_institutions.case_id",
            ],
        ),
        sa.CheckConstraint(
            "currency IS NULL OR (length(currency) = 3 AND upper(currency) = currency)",
            name="ck_financial_accounts_currency",
        ),
        sa.CheckConstraint(
            "status IS NULL OR status IN ('active', 'inactive', 'closed', 'unknown')",
            name="ck_financial_accounts_status",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "id",
            "organization_id",
            "case_id",
            name="uq_financial_accounts_id_organization_id_case_id",
        ),
    )
    op.create_index(
        "ix_financial_accounts_case_id",
        "financial_accounts",
        ["case_id"],
    )
    op.create_index(
        "uq_financial_accounts_dedupe_key",
        "financial_accounts",
        ["dedupe_key", "organization_id", "case_id"],
        unique=True,
    )
    op.create_table(
        "financial_reporting_periods",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("dedupe_key", sa.String(length=96), nullable=False),
        sa.Column("period_type", sa.String(length=40), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=True),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("as_of_date", sa.Date(), nullable=True),
        sa.Column("label", sa.Text(), nullable=True),
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
        sa.CheckConstraint(
            "period_type IN ('as_of', 'day', 'month', 'quarter', 'year', 'custom')",
            name="ck_financial_reporting_periods_period_type",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "id",
            "organization_id",
            "case_id",
            name="uq_financial_reporting_periods_id_organization_id_case_id",
        ),
    )
    op.create_index(
        "ix_financial_reporting_periods_case_id",
        "financial_reporting_periods",
        ["case_id"],
    )
    op.create_index(
        "uq_financial_reporting_periods_dedupe_key",
        "financial_reporting_periods",
        ["dedupe_key", "organization_id", "case_id"],
        unique=True,
    )
    op.create_table(
        "financial_balances",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("dedupe_key", sa.String(length=96), nullable=False),
        sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("reporting_period_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("balance_type", sa.String(length=120), nullable=False),
        sa.Column("amount", sa.Numeric(20, 4), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=True),
        sa.Column("as_of_date", sa.Date(), nullable=True),
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
            name="ck_financial_balances_currency",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "id",
            "organization_id",
            "case_id",
            name="uq_financial_balances_id_organization_id_case_id",
        ),
    )
    op.create_index(
        "ix_financial_balances_case_id",
        "financial_balances",
        ["case_id"],
    )
    op.create_index(
        "uq_financial_balances_dedupe_key",
        "financial_balances",
        ["dedupe_key", "organization_id", "case_id"],
        unique=True,
    )
    op.create_table(
        "financial_obligations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("dedupe_key", sa.String(length=96), nullable=False),
        sa.Column("institution_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("reporting_period_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("obligation_type", sa.String(length=120), nullable=False),
        sa.Column("facility_type", sa.String(length=120), nullable=True),
        sa.Column("principal_amount", sa.Numeric(20, 4), nullable=True),
        sa.Column("outstanding_amount", sa.Numeric(20, 4), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=True),
        sa.Column("start_date", sa.Date(), nullable=True),
        sa.Column("maturity_date", sa.Date(), nullable=True),
        sa.Column("interest_rate", sa.Numeric(10, 6), nullable=True),
        sa.Column("status", sa.String(length=40), nullable=True),
        sa.Column(
            "details",
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
            ["institution_id", "organization_id", "case_id"],
            [
                "financial_institutions.id",
                "financial_institutions.organization_id",
                "financial_institutions.case_id",
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
            name="ck_financial_obligations_currency",
        ),
        sa.CheckConstraint(
            "status IS NULL OR status IN "
            "('active', 'inactive', 'closed', 'matured', 'defaulted', 'unknown')",
            name="ck_financial_obligations_status",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "id",
            "organization_id",
            "case_id",
            name="uq_financial_obligations_id_organization_id_case_id",
        ),
    )
    op.create_index(
        "ix_financial_obligations_case_id",
        "financial_obligations",
        ["case_id"],
    )
    op.create_index(
        "uq_financial_obligations_dedupe_key",
        "financial_obligations",
        ["dedupe_key", "organization_id", "case_id"],
        unique=True,
    )
    op.create_table(
        "financial_source_rows",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("document_extraction_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("row_index", sa.Integer(), nullable=True),
        sa.Column(
            "locator",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=jsonb_default(),
        ),
        sa.Column(
            "raw_payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=jsonb_default(),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "row_index IS NULL OR row_index >= 0",
            name="ck_financial_source_rows_row_index",
        ),
        sa.ForeignKeyConstraint(
            ["case_id", "organization_id"],
            ["risk_cases.id", "risk_cases.organization_id"],
        ),
        sa.ForeignKeyConstraint(
            ["document_id", "organization_id", "case_id"],
            ["documents.id", "documents.organization_id", "documents.case_id"],
        ),
        sa.ForeignKeyConstraint(
            ["document_extraction_id"],
            ["document_extractions.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "id",
            "organization_id",
            "case_id",
            name="uq_financial_source_rows_id_organization_id_case_id",
        ),
    )
    op.create_index(
        "ix_financial_source_rows_case_id",
        "financial_source_rows",
        ["case_id"],
    )
    op.create_index(
        "uq_financial_source_rows_extraction_row",
        "financial_source_rows",
        ["document_extraction_id", "row_index"],
        unique=True,
    )

    op.create_table(
        "financial_record_source_links",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("record_table", sa.String(length=120), nullable=False),
        sa.Column("record_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_row_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("field_name", sa.String(length=120), nullable=True),
        sa.Column("source_field", sa.String(length=120), nullable=True),
        sa.Column("confidence", sa.Numeric(5, 4), nullable=True),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=jsonb_default(),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="ck_financial_record_source_links_confidence",
        ),
        sa.CheckConstraint(
            "record_table IN "
            "('financial_institutions', 'financial_accounts', "
            "'financial_reporting_periods', 'financial_balances', "
            "'financial_obligations')",
            name="ck_financial_record_source_links_record_table",
        ),
        sa.ForeignKeyConstraint(
            ["case_id", "organization_id"],
            ["risk_cases.id", "risk_cases.organization_id"],
        ),
        sa.ForeignKeyConstraint(
            ["source_row_id", "organization_id", "case_id"],
            [
                "financial_source_rows.id",
                "financial_source_rows.organization_id",
                "financial_source_rows.case_id",
            ],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_financial_record_source_links_case_id",
        "financial_record_source_links",
        ["case_id"],
    )
    op.create_index(
        "uq_financial_record_source_links_field",
        "financial_record_source_links",
        [
            "source_row_id",
            "record_id",
            "record_table",
            "field_name",
            "source_field",
        ],
        unique=True,
    )

    op.create_table(
        "financial_manual_edit_history",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("record_table", sa.String(length=120), nullable=False),
        sa.Column("record_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("field_name", sa.String(length=120), nullable=False),
        sa.Column(
            "previous_value",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "new_value",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("edited_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["case_id", "organization_id"],
            ["risk_cases.id", "risk_cases.organization_id"],
        ),
        sa.ForeignKeyConstraint(
            ["edited_by", "organization_id"],
            ["users.id", "users.organization_id"],
        ),
        sa.CheckConstraint(
            "record_table IN "
            "('financial_institutions', 'financial_accounts', "
            "'financial_reporting_periods', 'financial_balances', "
            "'financial_obligations')",
            name="ck_financial_manual_edit_history_record_table",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_financial_manual_edit_history_case_id",
        "financial_manual_edit_history",
        ["case_id"],
    )

    op.create_table(
        "financial_validation_issues",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("record_table", sa.String(length=120), nullable=True),
        sa.Column("record_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("issue_key", sa.String(length=96), nullable=False),
        sa.Column("field_name", sa.String(length=120), nullable=False),
        sa.Column("severity", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("rule_id", sa.String(length=120), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column(
            "details",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=jsonb_default(),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "severity IN ('error', 'warning', 'info')",
            name="ck_financial_validation_issues_severity",
        ),
        sa.CheckConstraint(
            "status IN ('open', 'resolved', 'dismissed')",
            name="ck_financial_validation_issues_status",
        ),
        sa.CheckConstraint(
            "((record_table IS NULL AND record_id IS NULL) OR "
            "(record_table IS NOT NULL AND "
            "record_table IN ('financial_institutions', 'financial_accounts', "
            "'financial_reporting_periods', 'financial_balances', "
            "'financial_obligations') AND record_id IS NOT NULL))",
            name="ck_financial_validation_issues_record_reference",
        ),
        sa.ForeignKeyConstraint(
            ["case_id", "organization_id"],
            ["risk_cases.id", "risk_cases.organization_id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_financial_validation_issues_current_natural_key",
        "financial_validation_issues",
        ["organization_id", "case_id", "record_table", "record_id", "rule_id", "field_name"],
        unique=True,
    )
    for table in FINANCIAL_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"""
            CREATE POLICY {table}_tenant_isolation
            ON {table}
            USING (organization_id = {TENANT_ID_EXPR})
            WITH CHECK (organization_id = {TENANT_ID_EXPR})
            """
        )


def downgrade() -> None:
    for table in reversed(FINANCIAL_TABLES):
        op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")

    for table in reversed(FINANCIAL_TABLES):
        op.drop_table(table)

    op.drop_constraint("uq_users_id_organization_id", "users", type_="unique")
    op.drop_constraint(
        "uq_documents_id_organization_id_case_id",
        "documents",
        type_="unique",
    )
    op.drop_constraint("uq_risk_cases_id_organization_id", "risk_cases", type_="unique")
