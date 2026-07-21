"""Own-OIDC SSO: sso_connections table + generic 'oidc' auth provider.

AequorOS is now its own OIDC relying party (no Auth0 broker). Each organization
gets at most one ``sso_connections`` row (Phase 2 of docs/rbac.md lifts this to
many + home-realm discovery): issuer, client id, an AES-256-GCM-sealed client
secret (same envelope as the market-data credential vault), allowed email
domains, and an enabled flag. Standard tenant RLS (ENABLE + FORCE + policy).

``users.auth_provider`` loses the vendor-specific ``'auth0'`` for a generic
``'oidc'``: existing auth0-linked rows are rewritten (their ``sso_subject``
values are Auth0-shaped and will simply relink by email on first OIDC login),
and the CHECK constraint is recreated as ``('password', 'oidc')``.

Revision ID: 202607200015
Revises: 202607180014
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "202607200015"
down_revision = "202607180014"
branch_labels = None
depends_on = None

TENANT_ID_EXPR = "nullif(current_setting('app.organization_id', true), '')::uuid"

TABLE = "sso_connections"
_PROVIDER_CK = "ck_users_auth_provider"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("issuer", sa.String(length=512), nullable=False),
        sa.Column("client_id", sa.String(length=255), nullable=False),
        sa.Column("client_secret_ciphertext", sa.Text(), nullable=True),
        sa.Column("client_secret_fingerprint", sa.String(length=64), nullable=True),
        sa.Column(
            "allowed_email_domains", sa.JSON(), server_default=sa.text("'[]'"), nullable=False
        ),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("organization_id", name="uq_sso_connections_organization_id"),
    )

    op.execute(f"ALTER TABLE {TABLE} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {TABLE} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY {TABLE}_tenant_isolation
        ON {TABLE}
        USING (organization_id = {TENANT_ID_EXPR})
        WITH CHECK (organization_id = {TENANT_ID_EXPR})
        """
    )

    op.execute("UPDATE users SET auth_provider = 'oidc' WHERE auth_provider = 'auth0'")
    op.drop_constraint(_PROVIDER_CK, "users", type_="check")
    op.create_check_constraint(_PROVIDER_CK, "users", "auth_provider IN ('password', 'oidc')")


def downgrade() -> None:
    op.drop_constraint(_PROVIDER_CK, "users", type_="check")
    op.execute("UPDATE users SET auth_provider = 'auth0' WHERE auth_provider = 'oidc'")
    op.create_check_constraint(_PROVIDER_CK, "users", "auth_provider IN ('password', 'auth0')")

    op.execute(f"DROP POLICY IF EXISTS {TABLE}_tenant_isolation ON {TABLE}")
    op.execute(f"ALTER TABLE {TABLE} NO FORCE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {TABLE} DISABLE ROW LEVEL SECURITY")
    op.drop_table(TABLE)
