"""risk review workflow

Revision ID: 202605290001
Revises: 202605250002
Create Date: 2026-05-29 00:01:00.000000
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "202605290001"
down_revision = "202605250002"
branch_labels = None
depends_on = None

TENANT_ID_EXPR = "nullif(current_setting('app.organization_id', true), '')::uuid"


def jsonb_default() -> sa.TextClause:
    return sa.text("'{}'::jsonb")


def upgrade() -> None:
    op.add_column(
        "risk_cases",
        sa.Column("assigned_to_user_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column("risk_cases", sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("risk_cases", sa.Column("risk_score", sa.Integer(), nullable=True))
    op.add_column("risk_cases", sa.Column("risk_level", sa.String(length=40), nullable=True))
    op.add_column("risk_cases", sa.Column("scored_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("risk_cases", sa.Column("scoring_version", sa.String(length=80), nullable=True))
    op.add_column("risk_cases", sa.Column("decision", sa.String(length=40), nullable=True))
    op.add_column("risk_cases", sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True))
    op.create_check_constraint(
        "ck_risk_cases_status",
        "risk_cases",
        "status IN ('draft', 'active', 'in_review', 'completed', 'archived')",
    )
    op.create_check_constraint(
        "ck_risk_cases_decision",
        "risk_cases",
        "decision IS NULL OR decision IN ('approved', 'rejected', 'needs_more_info', 'escalated')",
    )
    op.create_check_constraint(
        "ck_risk_cases_risk_level",
        "risk_cases",
        "risk_level IS NULL OR risk_level IN ('low', 'medium', 'high', 'critical')",
    )
    op.create_check_constraint(
        "ck_risk_cases_risk_score",
        "risk_cases",
        "risk_score IS NULL OR (risk_score >= 0 AND risk_score <= 100)",
    )
    op.create_foreign_key(
        "fk_risk_cases_assigned_to_user_id_users",
        "risk_cases",
        "users",
        ["assigned_to_user_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_risk_cases_organization_id_assigned_to",
        "risk_cases",
        ["organization_id", "assigned_to_user_id"],
    )

    op.create_table(
        "risk_case_decisions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("decision", sa.String(length=40), nullable=False),
        sa.Column("previous_decision", sa.String(length=40), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("decided_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "decision IN ('approved', 'rejected', 'needs_more_info', 'escalated')",
            name="ck_risk_case_decisions_decision",
        ),
        sa.ForeignKeyConstraint(["case_id"], ["risk_cases.id"]),
        sa.ForeignKeyConstraint(["decided_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_risk_case_decisions_organization_id_case_id",
        "risk_case_decisions",
        ["organization_id", "case_id"],
    )

    op.add_column("risk_findings", sa.Column("rule_id", sa.String(length=120), nullable=True))
    op.add_column("risk_findings", sa.Column("rule_version", sa.String(length=80), nullable=True))
    op.add_column("risk_findings", sa.Column("score_impact", sa.Integer(), nullable=True))
    op.add_column(
        "risk_findings",
        sa.Column(
            "source",
            sa.String(length=40),
            nullable=False,
            server_default=sa.text("'manual'"),
        ),
    )
    op.add_column(
        "risk_findings",
        sa.Column(
            "details",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=jsonb_default(),
        ),
    )
    op.create_check_constraint(
        "ck_risk_findings_status",
        "risk_findings",
        (
            "status IN ('open', 'accepted', 'acknowledged', 'dismissed', "
            "'needs_review', 'resolved', 'superseded')"
        ),
    )
    op.create_check_constraint(
        "ck_risk_findings_severity",
        "risk_findings",
        "severity IN ('low', 'medium', 'high', 'critical')",
    )
    op.create_check_constraint(
        "ck_risk_findings_source",
        "risk_findings",
        "source IN ('deterministic_rule', 'manual', 'imported')",
    )

    op.create_table(
        "risk_scores",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("assessment_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("score", sa.Integer(), nullable=False),
        sa.Column("risk_level", sa.String(length=40), nullable=False),
        sa.Column("scoring_version", sa.String(length=80), nullable=False),
        sa.Column("input_hash", sa.Text(), nullable=False),
        sa.Column(
            "input_snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=jsonb_default(),
        ),
        sa.Column(
            "rule_results",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("score >= 0 AND score <= 100", name="ck_risk_scores_score"),
        sa.CheckConstraint(
            "risk_level IN ('low', 'medium', 'high', 'critical')",
            name="ck_risk_scores_risk_level",
        ),
        sa.ForeignKeyConstraint(["assessment_id"], ["risk_assessments.id"]),
        sa.ForeignKeyConstraint(["case_id"], ["risk_cases.id"]),
        sa.ForeignKeyConstraint(["run_id"], ["risk_assessment_runs.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_risk_scores_organization_id_case_id",
        "risk_scores",
        ["organization_id", "case_id"],
    )

    op.execute("ALTER TABLE risk_case_decisions ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE risk_case_decisions FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY risk_case_decisions_tenant_isolation
        ON risk_case_decisions
        USING (organization_id = {TENANT_ID_EXPR})
        WITH CHECK (organization_id = {TENANT_ID_EXPR})
        """
    )
    op.execute("ALTER TABLE risk_scores ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE risk_scores FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY risk_scores_tenant_isolation
        ON risk_scores
        USING (organization_id = {TENANT_ID_EXPR})
        WITH CHECK (organization_id = {TENANT_ID_EXPR})
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS risk_scores_tenant_isolation ON risk_scores")
    op.execute("ALTER TABLE risk_scores NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE risk_scores DISABLE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS risk_case_decisions_tenant_isolation ON risk_case_decisions")
    op.execute("ALTER TABLE risk_case_decisions NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE risk_case_decisions DISABLE ROW LEVEL SECURITY")

    op.drop_index("ix_risk_scores_organization_id_case_id", table_name="risk_scores")
    op.drop_table("risk_scores")

    op.drop_constraint("ck_risk_findings_source", "risk_findings", type_="check")
    op.drop_constraint("ck_risk_findings_severity", "risk_findings", type_="check")
    op.drop_constraint("ck_risk_findings_status", "risk_findings", type_="check")
    op.drop_column("risk_findings", "details")
    op.drop_column("risk_findings", "source")
    op.drop_column("risk_findings", "score_impact")
    op.drop_column("risk_findings", "rule_version")
    op.drop_column("risk_findings", "rule_id")

    op.drop_index(
        "ix_risk_case_decisions_organization_id_case_id",
        table_name="risk_case_decisions",
    )
    op.drop_table("risk_case_decisions")

    op.drop_index("ix_risk_cases_organization_id_assigned_to", table_name="risk_cases")
    op.drop_constraint("fk_risk_cases_assigned_to_user_id_users", "risk_cases", type_="foreignkey")
    op.drop_constraint("ck_risk_cases_risk_score", "risk_cases", type_="check")
    op.drop_constraint("ck_risk_cases_risk_level", "risk_cases", type_="check")
    op.drop_constraint("ck_risk_cases_decision", "risk_cases", type_="check")
    op.drop_constraint("ck_risk_cases_status", "risk_cases", type_="check")
    op.drop_column("risk_cases", "decided_at")
    op.drop_column("risk_cases", "decision")
    op.drop_column("risk_cases", "scoring_version")
    op.drop_column("risk_cases", "scored_at")
    op.drop_column("risk_cases", "risk_level")
    op.drop_column("risk_cases", "risk_score")
    op.drop_column("risk_cases", "assigned_at")
    op.drop_column("risk_cases", "assigned_to_user_id")
