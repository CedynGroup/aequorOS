"""enable tenant row level security

Revision ID: 202605250002
Revises: 202605250001
Create Date: 2026-05-25 00:02:00.000000
"""

from alembic import op

revision = "202605250002"
down_revision = "202605250001"
branch_labels = None
depends_on = None

TENANT_TABLES = [
    "users",
    "audit_events",
    "risk_cases",
    "stored_objects",
    "documents",
    "document_chunks",
    "document_extractions",
    "risk_assessments",
    "risk_assessment_runs",
    "risk_findings",
    "risk_finding_evidence",
    "jobs",
]

TENANT_ID_EXPR = "nullif(current_setting('app.organization_id', true), '')::uuid"


def upgrade() -> None:
    op.execute("ALTER TABLE organizations ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE organizations FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY organizations_tenant_isolation
        ON organizations
        USING (id = {TENANT_ID_EXPR})
        WITH CHECK (id = {TENANT_ID_EXPR})
        """
    )

    for table in TENANT_TABLES:
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
    for table in reversed(TENANT_TABLES):
        op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")

    op.execute("DROP POLICY IF EXISTS organizations_tenant_isolation ON organizations")
    op.execute("ALTER TABLE organizations NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE organizations DISABLE ROW LEVEL SECURITY")
