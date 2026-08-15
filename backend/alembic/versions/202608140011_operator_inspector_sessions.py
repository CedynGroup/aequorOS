"""operator inspector sessions

Revision ID: 202608140011
Revises: 202608140010
Create Date: 2026-08-14 00:11:00.000000

Creates the GLOBAL ``operator_inspector_sessions`` table (staff_UI.md tenant
inspector): append-only, READ-ONLY session tracking for staff who view a
tenant's data through the operator console. Opening a session mints no tenant
token and no act-as-user claim — the row + its ``operator_audit_log`` entries +
the UI banner are the diligence control (WHO viewed WHICH tenant, WHEN, WHY,
under which mode). Deliberately NOT tenant-scoped and NOT RLS-forced (the
operator-control-plane precedent: staff records owned by the operator role).
"""

import sqlalchemy as sa

from alembic import op

revision = "202608140011"
down_revision = "202608140010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "operator_inspector_sessions",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("organization_id", sa.String(length=16), nullable=False),
        sa.Column("started_by", sa.String(length=320), nullable=False),
        sa.Column("mode", sa.String(length=16), nullable=False),
        sa.Column("reason", sa.String(), nullable=False),
        sa.Column(
            "read_only", sa.Boolean(), server_default=sa.text("true"), nullable=False
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_by", sa.String(length=320), nullable=True),
        sa.CheckConstraint(
            "mode IN ('consent', 'break_glass')",
            name="ck_operator_inspector_sessions_mode",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_operator_inspector_sessions_org_started",
        "operator_inspector_sessions",
        ["organization_id", "started_at"],
    )
    op.create_index(
        "ix_operator_inspector_sessions_started_at",
        "operator_inspector_sessions",
        ["started_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_operator_inspector_sessions_started_at",
        table_name="operator_inspector_sessions",
    )
    op.drop_index(
        "ix_operator_inspector_sessions_org_started",
        table_name="operator_inspector_sessions",
    )
    op.drop_table("operator_inspector_sessions")
