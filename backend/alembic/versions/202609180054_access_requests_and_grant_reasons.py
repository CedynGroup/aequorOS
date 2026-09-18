"""Add structured grant reasons and route-derived access requests.

Revision ID: 202609180054
Revises: 202609160053
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "202609180054"
down_revision = "202609160053"
branch_labels = None
depends_on = None

_TENANT_ID_EXPR = "NULLIF(current_setting('app.organization_id', true), '')"


def upgrade() -> None:
    op.add_column(
        "authorization_bindings",
        sa.Column(
            "grant_reason_category",
            sa.String(length=32),
            nullable=False,
            server_default="other",
        ),
    )
    op.add_column(
        "authorization_bindings",
        sa.Column("grant_reference", sa.String(length=255), nullable=True),
    )
    op.create_check_constraint(
        "ck_authorization_bindings_grant_reason_category",
        "authorization_bindings",
        "grant_reason_category IN ('new_joiner', 'role_change', 'project_engagement', "
        "'temporary_cover', 'regulator_audit_request', 'incident_break_glass', 'other')",
    )

    op.create_table(
        "authorization_access_requests",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.String(length=16), nullable=False),
        sa.Column("requester_user_id", sa.Uuid(), nullable=False),
        sa.Column("institution_id", sa.String(length=16), nullable=False),
        sa.Column("route", sa.String(length=255), nullable=False),
        sa.Column("page_title", sa.String(length=255), nullable=False),
        sa.Column("module_scope", sa.String(length=32), nullable=False),
        sa.Column("sensitivity_scope", sa.String(length=32), nullable=False),
        sa.Column("permission", sa.String(length=32), nullable=False),
        sa.Column("reason_category", sa.String(length=32), nullable=False),
        sa.Column("reason_detail", sa.Text(), nullable=False),
        sa.Column("reference", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("binding_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "module_scope IN ('all', 'liq', 'cap', 'irrbb', 'fx', 'ftp', 'fcst', "
            "'beh', 'data', 'reg', 'risk', 'markets', 'account', 'audit')",
            name="ck_authorization_access_requests_module_scope",
        ),
        sa.CheckConstraint(
            "sensitivity_scope IN ('published', 'aggregated', 'confidential', 'restricted')",
            name="ck_authorization_access_requests_sensitivity_scope",
        ),
        sa.CheckConstraint(
            "permission IN ('view', 'create', 'edit', 'run', 'review', 'approve', "
            "'configure', 'export', 'validate', 'sign_off', 'submit', 'administer', 'ingest')",
            name="ck_authorization_access_requests_permission",
        ),
        sa.CheckConstraint(
            "reason_category IN ('new_joiner', 'role_change', 'project_engagement', "
            "'temporary_cover', 'regulator_audit_request', 'incident_break_glass', 'other')",
            name="ck_authorization_access_requests_reason_category",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'approved', 'rejected')",
            name="ck_authorization_access_requests_status",
        ),
        sa.CheckConstraint(
            "reason_category <> 'other' OR length(trim(reason_detail)) > 0",
            name="ck_authorization_access_requests_other_detail",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["requester_user_id", "organization_id"],
            ["users.id", "users.organization_id"],
            ondelete="CASCADE",
            name="fk_authorization_access_requests_requester_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["institution_id", "organization_id"],
            ["banks.id", "banks.organization_id"],
            ondelete="RESTRICT",
            name="fk_authorization_access_requests_institution_tenant",
        ),
        sa.ForeignKeyConstraint(["resolved_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["binding_id"], ["authorization_bindings.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_authorization_access_requests_org_status",
        "authorization_access_requests",
        ["organization_id", "status", "created_at"],
    )
    op.create_index(
        "uq_authorization_access_requests_pending_scope",
        "authorization_access_requests",
        [
            "organization_id",
            "requester_user_id",
            "route",
            "institution_id",
            "module_scope",
            "sensitivity_scope",
            "permission",
        ],
        unique=True,
        postgresql_where=sa.text("status = 'pending'"),
        sqlite_where=sa.text("status = 'pending'"),
    )
    op.execute("ALTER TABLE authorization_access_requests ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE authorization_access_requests FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY authorization_access_requests_tenant_isolation
        ON authorization_access_requests
        FOR ALL
        USING ((organization_id)::text = {_TENANT_ID_EXPR})
        WITH CHECK ((organization_id)::text = {_TENANT_ID_EXPR})
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP POLICY IF EXISTS authorization_access_requests_tenant_isolation "
        "ON authorization_access_requests"
    )
    op.execute("ALTER TABLE authorization_access_requests NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE authorization_access_requests DISABLE ROW LEVEL SECURITY")
    op.drop_index(
        "uq_authorization_access_requests_pending_scope",
        table_name="authorization_access_requests",
    )
    op.drop_index(
        "ix_authorization_access_requests_org_status",
        table_name="authorization_access_requests",
    )
    op.drop_table("authorization_access_requests")
    op.drop_constraint(
        "ck_authorization_bindings_grant_reason_category",
        "authorization_bindings",
        type_="check",
    )
    op.drop_column("authorization_bindings", "grant_reference")
    op.drop_column("authorization_bindings", "grant_reason_category")
