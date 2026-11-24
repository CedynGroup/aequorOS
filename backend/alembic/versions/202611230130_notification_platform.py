"""Notification platform: catalogue events, per-recipient deliveries, preferences, policy.

Adds the structured-message columns to ``notifications`` and four tenant tables:
``notification_deliveries`` (outbox + delivery log), ``notification_preferences``
(one person's email choice per event), ``notification_policies`` (the
organisation's default or locked choice per event) and ``notification_settings``
(digest hour, time zone, reminder and escalation windows). See
backend/docs/notifications.md.

Revision ID: 202611230130
Revises: 202611230122
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "202611230130"
down_revision = "202611230122"
branch_labels = None
depends_on = None

_TENANT_ID_EXPR = "NULLIF(current_setting('app.organization_id', true), '')"
_EMAIL_MODES = "'immediate', 'digest', 'off'"


def _tenant_table(table: str, grants: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(f"GRANT {grants} ON {table} TO aqs_api, aqs_worker")
    op.execute(
        f"""
        CREATE POLICY {table}_tenant_isolation ON {table}
        FOR ALL
        USING ((organization_id)::text = {_TENANT_ID_EXPR})
        WITH CHECK ((organization_id)::text = {_TENANT_ID_EXPR})
        """
    )


def upgrade() -> None:
    op.add_column("notifications", sa.Column("event_key", sa.String(80), nullable=True))
    op.add_column(
        "notifications",
        sa.Column(
            "data",
            postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), "sqlite"),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
    )
    op.add_column("notifications", sa.Column("action_path", sa.String(400), nullable=True))

    op.create_table(
        "notification_deliveries",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("organization_id", sa.String(16), nullable=False),
        sa.Column("notification_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("recipient_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("channel", sa.String(16), nullable=False),
        sa.Column("address", sa.String(320), nullable=False),
        sa.Column("mode", sa.String(12), nullable=False),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_error", sa.String(500), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("digest_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("channel IN ('email')", name="ck_notification_deliveries_channel"),
        sa.CheckConstraint(
            "mode IN ('immediate', 'digest')", name="ck_notification_deliveries_mode"
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'sent', 'dead', 'suppressed')",
            name="ck_notification_deliveries_status",
        ),
        sa.CheckConstraint("attempts >= 0", name="ck_notification_deliveries_attempts"),
        sa.ForeignKeyConstraint(
            ["notification_id", "organization_id"],
            ["notifications.id", "notifications.organization_id"],
            name="fk_notification_deliveries_notification",
        ),
        sa.ForeignKeyConstraint(
            ["recipient_user_id", "organization_id"],
            ["users.id", "users.organization_id"],
            name="fk_notification_deliveries_recipient",
        ),
        sa.UniqueConstraint(
            "notification_id",
            "recipient_user_id",
            "channel",
            name="uq_notification_deliveries_once",
        ),
    )
    op.create_index(
        "ix_notification_deliveries_due",
        "notification_deliveries",
        ["organization_id", "next_attempt_at"],
        postgresql_where=sa.text("status = 'pending'"),
        sqlite_where=sa.text("status = 'pending'"),
    )
    op.create_index(
        "ix_notification_deliveries_org_created",
        "notification_deliveries",
        ["organization_id", "created_at"],
    )

    op.create_table(
        "notification_preferences",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("organization_id", sa.String(16), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_key", sa.String(80), nullable=False),
        sa.Column("email_mode", sa.String(12), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            f"email_mode IN ({_EMAIL_MODES})", name="ck_notification_preferences_email_mode"
        ),
        sa.ForeignKeyConstraint(
            ["user_id", "organization_id"],
            ["users.id", "users.organization_id"],
            name="fk_notification_preferences_user",
        ),
        sa.UniqueConstraint(
            "organization_id", "user_id", "event_key", name="uq_notification_preferences_event"
        ),
    )

    op.create_table(
        "notification_policies",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("organization_id", sa.String(16), nullable=False),
        sa.Column("event_key", sa.String(80), nullable=False),
        sa.Column("email_mode", sa.String(12), nullable=False),
        sa.Column("locked", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            f"email_mode IN ({_EMAIL_MODES})", name="ck_notification_policies_email_mode"
        ),
        sa.ForeignKeyConstraint(
            ["updated_by", "organization_id"],
            ["users.id", "users.organization_id"],
            name="fk_notification_policies_updated_by",
        ),
        sa.UniqueConstraint("organization_id", "event_key", name="uq_notification_policies_event"),
    )

    op.create_table(
        "notification_settings",
        sa.Column("organization_id", sa.String(16), primary_key=True),
        sa.Column("email_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("time_zone", sa.String(64), nullable=False, server_default="UTC"),
        sa.Column("digest_hour", sa.SmallInteger(), nullable=False, server_default="7"),
        sa.Column("reminders_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("reminder_after_hours", sa.Integer(), nullable=False, server_default="24"),
        sa.Column("escalate_after_hours", sa.Integer(), nullable=False, server_default="48"),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "digest_hour >= 0 AND digest_hour <= 23", name="ck_notification_settings_digest_hour"
        ),
        sa.CheckConstraint(
            "reminder_after_hours > 0 AND escalate_after_hours > reminder_after_hours",
            name="ck_notification_settings_escalation_window",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name="fk_notification_settings_organization",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by", "organization_id"],
            ["users.id", "users.organization_id"],
            name="fk_notification_settings_updated_by",
        ),
    )

    if op.get_bind().dialect.name == "postgresql":
        # Deliveries are a log: written, advanced and read, never deleted.
        _tenant_table("notification_deliveries", "SELECT, INSERT, UPDATE")
        # A preference reset to the default deletes its row.
        _tenant_table("notification_preferences", "SELECT, INSERT, UPDATE, DELETE")
        _tenant_table("notification_policies", "SELECT, INSERT, UPDATE, DELETE")
        _tenant_table("notification_settings", "SELECT, INSERT, UPDATE")


def downgrade() -> None:
    for table in (
        "notification_settings",
        "notification_policies",
        "notification_preferences",
        "notification_deliveries",
    ):
        if op.get_bind().dialect.name == "postgresql":
            op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table}")
        op.drop_table(table)
    op.drop_column("notifications", "action_path")
    op.drop_column("notifications", "data")
    op.drop_column("notifications", "event_key")
