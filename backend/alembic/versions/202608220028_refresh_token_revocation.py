"""refresh_tokens — revocable, rotating refresh tokens (audit finding P0-5)

Revision ID: 202608220028
Revises: 202608220027

Refresh tokens were pure JWTs with a 14-day life and no server-side state: no
``jti``, no denylist, no rotation, no epoch. The only way to end a session was
``users.is_active = False``, so a password rotation after a suspected compromise
did not log the attacker out.

This adds ``refresh_tokens``, one RLS-forced tenant row per issued refresh token,
keyed by the ``jti`` the token now carries (``id`` IS the ``jti``). It stores a
SHA-256 digest of the token, never the token. ``family_id`` groups a login and
everything it rotated into, so reuse of a retired token can revoke the whole
lineage in one statement.

**Deliberate, user-visible consequence.** Refresh tokens minted before this
migration carry no ``jti`` and therefore have no row here; ``decode_token``
now REQUIRES ``jti`` on the refresh path, so they are refused. Every session
outstanding at deploy time re-authenticates once. That is the fail-closed
choice: the alternative — trusting a token with no revocation state — is the
vulnerability this migration exists to close.

The RLS policy form copies the post-epoch precedent (202608190020 onward): the
``organization_id`` OR- text is compared with no ``::uuid`` cast. Login and
refresh run on the cross-tenant system session (the BYPASSRLS worker role), the
same session ``users`` already requires. The hermetic test suite builds the
schema with ``Base.metadata.create_all`` and never runs this migration; it
exists for the primary/production database.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "202608220028"
down_revision = "202608220027"
branch_labels = None
depends_on = None

_TABLE = "refresh_tokens"
_TENANT_ID_EXPR = "NULLIF(current_setting('app.organization_id', true), '')"
_REASONS = "('logout', 'password_change', 'user_deactivated', 'reuse_detected', 'admin_revoked')"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.String(length=16), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("family_id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("rotated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("replaced_by_id", sa.Uuid(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_reason", sa.String(length=32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("token_hash", name="uq_refresh_tokens_token_hash"),
        sa.CheckConstraint(
            f"revoked_reason IS NULL OR revoked_reason IN {_REASONS}",
            name="ck_refresh_tokens_revoked_reason",
        ),
    )
    op.create_index("ix_refresh_tokens_family_id", _TABLE, ["family_id"])
    op.create_index("ix_refresh_tokens_user_id", _TABLE, ["user_id"])
    op.create_index("ix_refresh_tokens_organization_id", _TABLE, ["organization_id"])
    op.create_index("ix_refresh_tokens_expires_at", _TABLE, ["expires_at"])

    op.execute(f"ALTER TABLE {_TABLE} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {_TABLE} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY {_TABLE}_tenant_isolation ON {_TABLE}
        FOR ALL
        USING ((organization_id)::text = {_TENANT_ID_EXPR})
        WITH CHECK ((organization_id)::text = {_TENANT_ID_EXPR})
        """
    )


def downgrade() -> None:
    op.execute(f"DROP POLICY IF EXISTS {_TABLE}_tenant_isolation ON {_TABLE}")
    op.execute(f"ALTER TABLE {_TABLE} NO FORCE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {_TABLE} DISABLE ROW LEVEL SECURITY")
    op.drop_index("ix_refresh_tokens_expires_at", table_name=_TABLE)
    op.drop_index("ix_refresh_tokens_organization_id", table_name=_TABLE)
    op.drop_index("ix_refresh_tokens_user_id", table_name=_TABLE)
    op.drop_index("ix_refresh_tokens_family_id", table_name=_TABLE)
    op.drop_table(_TABLE)
