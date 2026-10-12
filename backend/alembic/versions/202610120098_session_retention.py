"""Retain inspector audit evidence and index refresh-token cleanup.

Revision ID: 202610120098
Revises: 202610110097
"""

import sqlalchemy as sa

from alembic import op

revision = "202610120098"
down_revision = "202610110097"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_refresh_tokens_org_expires", "refresh_tokens", ["organization_id", "expires_at"]
    )
    op.create_index(
        "ix_refresh_tokens_org_revoked",
        "refresh_tokens",
        ["organization_id", "revoked_at"],
        postgresql_where=sa.text("revoked_at IS NOT NULL"),
    )
    if op.get_bind().dialect.name != "postgresql":
        return
    # No approved audit deletion period exists yet (#413, counsel #246).
    # Preserve lifecycle UPDATEs, but refuse direct/cascading DELETE and TRUNCATE.
    op.execute("""
        CREATE TRIGGER operator_inspector_sessions_retained BEFORE DELETE
          ON operator_inspector_sessions FOR EACH ROW
          EXECUTE FUNCTION aequoros_append_only_guard();
        CREATE TRIGGER operator_inspector_sessions_no_truncate BEFORE TRUNCATE
          ON operator_inspector_sessions FOR EACH STATEMENT
          EXECUTE FUNCTION aequoros_append_only_guard();
    """)


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("""
            DROP TRIGGER operator_inspector_sessions_no_truncate ON operator_inspector_sessions;
            DROP TRIGGER operator_inspector_sessions_retained ON operator_inspector_sessions;
        """)
    op.drop_index("ix_refresh_tokens_org_revoked", table_name="refresh_tokens")
    op.drop_index("ix_refresh_tokens_org_expires", table_name="refresh_tokens")
