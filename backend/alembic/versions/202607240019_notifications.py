"""In-app notification feed (plan W3).

One row per recipient; ``recipient_user_id`` NULL = org-wide. ``type`` is a
namespaced code (``reporting.*``) that doubles as the deterministic dedupe key
for scheduled deadline notifications, hence its audit-event-sized width.

Revision ID: 202607240019
Revises: 202607240018
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "202607240019"
down_revision = "202607240018"
branch_labels = None
depends_on = None

_SEVERITIES = ("info", "warning", "critical")


def _values(options: tuple[str, ...]) -> str:
    return ", ".join(f"'{option}'" for option in options)


def upgrade() -> None:
    op.create_table(
        "notifications",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("recipient_user_id", sa.Uuid(), nullable=True),
        sa.Column("type", sa.String(length=120), nullable=False),
        sa.Column("severity", sa.String(length=12), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("entity_type", sa.String(length=40), nullable=True),
        sa.Column("entity_id", sa.Uuid(), nullable=True),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            f"severity IN ({_values(_SEVERITIES)})",
            name="ck_notifications_severity",
        ),
        sa.ForeignKeyConstraint(
            ["recipient_user_id", "organization_id"],
            ["users.id", "users.organization_id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "organization_id", name="uq_notifications_id_org"),
    )
    op.create_index(
        "ix_notifications_org_recipient_read",
        "notifications",
        ["organization_id", "recipient_user_id", "read_at"],
    )
    op.create_index(
        "ix_notifications_org_created",
        "notifications",
        ["organization_id", "created_at"],
    )
    # Tenant isolation, same posture as every reporting table.
    op.execute("ALTER TABLE notifications ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE notifications FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON notifications "
        "USING (organization_id = current_setting('app.organization_id')::uuid) "
        "WITH CHECK (organization_id = current_setting('app.organization_id')::uuid)"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON notifications")
    op.drop_index("ix_notifications_org_created", table_name="notifications")
    op.drop_index("ix_notifications_org_recipient_read", table_name="notifications")
    op.drop_table("notifications")
