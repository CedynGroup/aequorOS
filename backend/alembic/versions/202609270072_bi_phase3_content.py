"""BI Phase 3: saved dashboards, calculated measures, alerts and subscriptions.

Revision ID: 202609270072
Revises: 202609270071

Eight tables, all tenant-scoped and all RLS-forced, for the self-service half of
BI: a reader saves a dashboard, writes a calculated measure and has it certified
by a second pair of eyes, sets a threshold alert, and subscribes people to a
delivery. Phase 1 and 2 built what the platform asserts; these are what a BANK
asserts on top of it, which is why every one of them carries an owner and why the
authorization decision is made per VIEWER at read time rather than stored.

Ordered so a parent precedes its children: dashboards, then their versions and
shares; measures; alerts, then their events; subscriptions, then their
deliveries.

``bi_dashboard_versions`` is APPEND-ONLY, the same three ways ``audit_events`` and
``bi_query_log`` are (``202607250027``, ``202609220066``): the shared row trigger,
UPDATE and DELETE revoked from every non-superuser role, and RESTRICTIVE policies
that deny both regardless of the tenant policy. A dashboard's history is the
evidence of what a reader was shown on a date, so rewriting it must be refused by
the database rather than avoided by the application. The spec names this table
append-only and this is what that means.

Why the shapes matter, in the three places a constraint is doing real work:

* ``bi_measures`` makes a self-approved or stale promotion UNSTORABLE, not merely
  rejected in a service: ``ck_bi_measures_promotion_separation`` refuses an
  approver who is also the proposer, and
  ``ck_bi_measures_certified_matches_expression`` refuses a certified row whose
  expression has moved since approval. Maker-checker that lives only in a service
  is one code path away from being bypassed; these are the floor beneath it.
* ``bi_alert_events`` forces a verdict to be complete
  (``ck_bi_alert_events_verdict_is_complete``): either it breached or cleared with
  BOTH an observed value and a threshold, or it was not evaluated and says why.
  There is no row that silently means "we looked and nothing happened", because a
  missing figure and a figure inside its limit are different statements.
* ``bi_subscription_deliveries`` accounts for the artifact
  (``ck_bi_subscription_deliveries_artifact_accounted``): an attachment must
  record its digest and size, and anything that is not an attachment must record
  neither. Confidential content is delivered as a sign-in link rather than a file,
  so the absence of a digest is the evidence that nothing left as an attachment.

There is no data step, so no ``force_rls_suspended``: all eight tables are new
and empty.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "202609270072"
down_revision = "202609270071"
branch_labels = None
depends_on = None

_TENANT_ID_EXPR = "NULLIF(current_setting('app.organization_id', true), '')"

#: Created in this order so a parent exists before the rows that reference it;
#: dropped in reverse. Every one is tenant-scoped and gets the standard policy.
TABLES: tuple[str, ...] = (
    "bi_dashboards",
    "bi_dashboard_versions",
    "bi_dashboard_shares",
    "bi_measures",
    "bi_alerts",
    "bi_alert_events",
    "bi_subscriptions",
    "bi_subscription_deliveries",
)

#: A dashboard's version history is evidence of what a reader was shown, so the
#: database refuses to rewrite it rather than the application avoiding it.
APPEND_ONLY_TABLES: tuple[str, ...] = ("bi_dashboard_versions",)

_GUARD_FUNCTION = "aequoros_append_only_guard"


def _enable_rls(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY {table}_tenant_isolation ON {table} FOR ALL "
        f"USING ((organization_id)::text = {_TENANT_ID_EXPR}) "
        f"WITH CHECK ((organization_id)::text = {_TENANT_ID_EXPR})"
    )


def _revoke(table: str, privileges: str) -> None:
    """Revoke from PUBLIC and from every present non-superuser role by name.

    No migration in this chain knows a role by name, so the loop enumerates
    ``pg_roles``. A role created AFTER this revision needs the same revocation
    alongside the grants it needs anyway.
    """
    op.execute(f"REVOKE {privileges} ON {table} FROM PUBLIC")
    op.execute(
        f"""
        DO $$
        DECLARE r record;
        BEGIN
            FOR r IN SELECT rolname FROM pg_roles
                     WHERE rolname NOT LIKE 'pg\\_%' AND NOT rolsuper
            LOOP
                EXECUTE format('REVOKE {privileges} ON {table} FROM %I', r.rolname);
            END LOOP;
        END $$
        """
    )


def _install_append_only(table: str) -> None:
    """The same three guards ``audit_events`` and ``bi_query_log`` carry.

    Belt, braces and a third: the row trigger refuses per row, the revocation
    refuses per statement, and the RESTRICTIVE policies refuse regardless of what
    the tenant policy allows. Any one of them alone can be worked around by a role
    with the wrong grant; together they cannot.
    """
    op.execute(
        f"CREATE TRIGGER {table}_append_only BEFORE UPDATE OR DELETE ON {table} "
        f"FOR EACH ROW EXECUTE FUNCTION {_GUARD_FUNCTION}()"
    )
    _revoke(table, "UPDATE, DELETE, TRUNCATE")
    op.execute(
        f"CREATE POLICY {table}_no_update ON {table} AS RESTRICTIVE FOR UPDATE TO PUBLIC "
        f"USING (false) WITH CHECK (false)"
    )
    op.execute(
        f"CREATE POLICY {table}_no_delete ON {table} AS RESTRICTIVE FOR DELETE TO PUBLIC "
        f"USING (false)"
    )


def upgrade() -> None:
    op.create_table(
        "bi_dashboards",
        sa.Column("owner_user_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(length=120), nullable=False),
        sa.Column(
            "description", sa.String(length=400), server_default=sa.text("''"), nullable=False
        ),
        sa.Column("visibility", sa.String(length=8), nullable=False),
        sa.Column("visibility_role", sa.String(length=32), nullable=True),
        sa.Column("badge", sa.String(length=20), nullable=False),
        sa.Column("current_version", sa.Integer(), nullable=False),
        sa.Column("source_pack", sa.String(length=64), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.String(length=16), nullable=False),
        sa.Column("bank_id", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "(visibility = 'role') = (visibility_role IS NOT NULL)",
            name="ck_bi_dashboards_visibility_role",
        ),
        sa.CheckConstraint(
            "badge IN ('bank_certified', 'personal')", name="ck_bi_dashboards_badge"
        ),
        sa.CheckConstraint(
            "visibility IN ('private', 'users', 'role', 'org')", name="ck_bi_dashboards_visibility"
        ),
        sa.CheckConstraint("current_version >= 1", name="ck_bi_dashboards_current_version"),
        sa.ForeignKeyConstraint(
            ["bank_id", "organization_id"],
            ["banks.id", "banks.organization_id"],
            name="fk_bi_dashboards_bank",
        ),
        sa.ForeignKeyConstraint(
            ["owner_user_id", "organization_id"],
            ["users.id", "users.organization_id"],
            name="fk_bi_dashboards_owner",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "organization_id", name="uq_bi_dashboards_id_organization"),
    )
    op.create_index(
        "ix_bi_dashboards_org_bank_owner",
        "bi_dashboards",
        ["organization_id", "bank_id", "owner_user_id"],
        unique=False,
    )
    op.create_index(
        "ix_bi_dashboards_org_bank_visibility",
        "bi_dashboards",
        ["organization_id", "bank_id", "visibility"],
        unique=False,
    )
    op.create_table(
        "bi_dashboard_versions",
        sa.Column("dashboard_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=120), nullable=False),
        sa.Column(
            "description", sa.String(length=400), server_default=sa.text("''"), nullable=False
        ),
        sa.Column("spec", sa.JSON(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("spec_digest", sa.String(length=64), nullable=False),
        sa.Column(
            "change_note", sa.String(length=280), server_default=sa.text("''"), nullable=False
        ),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.String(length=16), nullable=False),
        sa.Column("bank_id", sa.String(length=16), nullable=False),
        sa.CheckConstraint("version >= 1", name="ck_bi_dashboard_versions_version"),
        sa.ForeignKeyConstraint(
            ["bank_id", "organization_id"],
            ["banks.id", "banks.organization_id"],
            name="fk_bi_dashboard_versions_bank",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id", "organization_id"],
            ["users.id", "users.organization_id"],
            name="fk_bi_dashboard_versions_author",
        ),
        sa.ForeignKeyConstraint(
            ["dashboard_id", "organization_id"],
            ["bi_dashboards.id", "bi_dashboards.organization_id"],
            name="fk_bi_dashboard_versions_dashboard",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id",
            "dashboard_id",
            "version",
            name="uq_bi_dashboard_versions_dashboard_version",
        ),
    )
    op.create_index(
        "ix_bi_dashboard_versions_org_dashboard_version",
        "bi_dashboard_versions",
        ["organization_id", "dashboard_id", "version"],
        unique=False,
    )
    op.create_table(
        "bi_dashboard_shares",
        sa.Column("dashboard_id", sa.Uuid(), nullable=False),
        sa.Column("grantee_user_id", sa.Uuid(), nullable=False),
        sa.Column("shared_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.String(length=16), nullable=False),
        sa.Column("bank_id", sa.String(length=16), nullable=False),
        sa.ForeignKeyConstraint(
            ["bank_id", "organization_id"],
            ["banks.id", "banks.organization_id"],
            name="fk_bi_dashboard_shares_bank",
        ),
        sa.ForeignKeyConstraint(
            ["dashboard_id", "organization_id"],
            ["bi_dashboards.id", "bi_dashboards.organization_id"],
            name="fk_bi_dashboard_shares_dashboard",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["grantee_user_id", "organization_id"],
            ["users.id", "users.organization_id"],
            name="fk_bi_dashboard_shares_grantee",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id",
            "dashboard_id",
            "grantee_user_id",
            name="uq_bi_dashboard_shares_dashboard_grantee",
        ),
    )
    op.create_index(
        "ix_bi_dashboard_shares_org_grantee",
        "bi_dashboard_shares",
        ["organization_id", "grantee_user_id", "bank_id"],
        unique=False,
    )
    op.create_table(
        "bi_measures",
        sa.Column("measure_key", sa.String(length=64), nullable=False),
        sa.Column("owner_user_id", sa.Uuid(), nullable=False),
        sa.Column("label", sa.String(length=120), nullable=False),
        sa.Column(
            "description", sa.String(length=400), server_default=sa.text("''"), nullable=False
        ),
        sa.Column("expression", sa.String(length=2000), nullable=False),
        sa.Column("expression_digest", sa.String(length=64), nullable=False),
        sa.Column("referenced_members", sa.JSON(), server_default=sa.text("'[]'"), nullable=False),
        sa.Column("value_type", sa.String(length=16), nullable=False),
        sa.Column("favourable_direction", sa.String(length=24), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("proposed_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("proposed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("proposed_expression_digest", sa.String(length=64), nullable=True),
        sa.Column("proposal_reason", sa.String(length=400), nullable=True),
        sa.Column("approved_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_expression", sa.Text(), nullable=True),
        sa.Column("approved_expression_digest", sa.String(length=64), nullable=True),
        sa.Column("approval_reason", sa.String(length=400), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.String(length=16), nullable=False),
        sa.Column("bank_id", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "(state = 'bank_certified' AND approved_by_user_id IS NOT NULL AND approved_at IS "
            "NOT NULL AND approved_expression IS NOT NULL AND approved_expression_digest IS "
            "NOT NULL AND approval_reason IS NOT NULL) OR (state <> 'bank_certified' AND "
            "approved_by_user_id IS NULL AND approved_at IS NULL AND approved_expression IS "
            "NULL AND approved_expression_digest IS NULL AND approval_reason IS NULL)",
            name="ck_bi_measures_approval_complete",
        ),
        sa.CheckConstraint(
            "favourable_direction IN ('higher_better', 'lower_better', "
            "'magnitude_lower_better', 'neutral')",
            name="ck_bi_measures_favourable_direction",
        ),
        sa.CheckConstraint(
            "state <> 'bank_certified' OR approved_expression_digest = expression_digest",
            name="ck_bi_measures_certified_matches_expression",
        ),
        sa.CheckConstraint(
            "state = 'personal' OR (proposed_by_user_id IS NOT NULL AND proposed_at IS NOT "
            "NULL AND proposed_expression_digest IS NOT NULL AND proposal_reason IS NOT NULL)",
            name="ck_bi_measures_proposal_complete",
        ),
        sa.CheckConstraint(
            "state IN ('personal', 'proposed', 'bank_certified')", name="ck_bi_measures_state"
        ),
        sa.CheckConstraint(
            "value_type IN ('amount', 'pct', 'fraction', 'index', 'duration_years', 'count')",
            name="ck_bi_measures_value_type",
        ),
        sa.CheckConstraint(
            "approved_by_user_id IS NULL OR proposed_by_user_id IS NULL OR approved_by_user_id "
            "<> proposed_by_user_id",
            name="ck_bi_measures_promotion_separation",
        ),
        sa.ForeignKeyConstraint(
            ["bank_id", "organization_id"],
            ["banks.id", "banks.organization_id"],
            name="fk_bi_measures_bank",
        ),
        sa.ForeignKeyConstraint(
            ["owner_user_id", "organization_id"],
            ["users.id", "users.organization_id"],
            name="fk_bi_measures_owner",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id", "bank_id", "measure_key", name="uq_bi_measures_bank_key"
        ),
    )
    op.create_index(
        "ix_bi_measures_org_bank_owner",
        "bi_measures",
        ["organization_id", "bank_id", "owner_user_id"],
        unique=False,
    )
    op.create_index(
        "ix_bi_measures_org_bank_state",
        "bi_measures",
        ["organization_id", "bank_id", "state"],
        unique=False,
    )
    op.create_table(
        "bi_alerts",
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("measure_id", sa.String(length=120), nullable=False),
        sa.Column("filters", sa.JSON(), server_default=sa.text("'[]'"), nullable=False),
        sa.Column("direction", sa.String(length=8), nullable=False),
        sa.Column("threshold_basis", sa.String(length=16), nullable=False),
        sa.Column("threshold", sa.Numeric(precision=28, scale=6), nullable=True),
        sa.Column("owner_user_id", sa.Uuid(), nullable=False),
        sa.Column("notify_user_ids", sa.JSON(), server_default=sa.text("'[]'"), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.String(length=16), nullable=False),
        sa.Column("bank_id", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "(threshold_basis = 'stated' AND threshold IS NOT NULL) OR (threshold_basis = "
            "'governed_limit' AND threshold IS NULL)",
            name="ck_bi_alerts_threshold_matches_basis",
        ),
        sa.CheckConstraint("direction IN ('above', 'below')", name="ck_bi_alerts_direction"),
        sa.CheckConstraint(
            "threshold_basis IN ('stated', 'governed_limit')", name="ck_bi_alerts_threshold_basis"
        ),
        sa.ForeignKeyConstraint(
            ["bank_id", "organization_id"],
            ["banks.id", "banks.organization_id"],
        ),
        sa.ForeignKeyConstraint(
            ["owner_user_id", "organization_id"],
            ["users.id", "users.organization_id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "organization_id", name="uq_bi_alerts_id_org"),
        sa.UniqueConstraint("organization_id", "bank_id", "name", name="uq_bi_alerts_bank_name"),
    )
    op.create_index(
        "ix_bi_alerts_org_bank_active",
        "bi_alerts",
        ["organization_id", "bank_id", "is_active"],
        unique=False,
    )
    op.create_table(
        "bi_alert_events",
        sa.Column("alert_id", sa.Uuid(), nullable=False),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("build_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("observed_value", sa.Numeric(precision=28, scale=6), nullable=True),
        sa.Column("threshold_value", sa.Numeric(precision=28, scale=6), nullable=True),
        sa.Column("threshold_basis", sa.String(length=16), nullable=False),
        sa.Column("limit_source", sa.Text(), nullable=True),
        sa.Column("reason", sa.String(length=32), nullable=True),
        sa.Column("member_ids", sa.JSON(), server_default=sa.text("'[]'"), nullable=False),
        sa.Column("notified_user_ids", sa.JSON(), server_default=sa.text("'[]'"), nullable=False),
        sa.Column("withheld_user_ids", sa.JSON(), server_default=sa.text("'[]'"), nullable=False),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("builder_version", sa.Integer(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.String(length=16), nullable=False),
        sa.Column("bank_id", sa.String(length=16), nullable=False),
        sa.CheckConstraint(
            "(state = 'not_evaluated' AND reason IS NOT NULL AND observed_value IS NULL AND "
            "threshold_value IS NULL) OR (state <> 'not_evaluated' AND reason IS NULL AND "
            "observed_value IS NOT NULL AND threshold_value IS NOT NULL)",
            name="ck_bi_alert_events_verdict_is_complete",
        ),
        sa.CheckConstraint(
            "reason IS NULL OR reason IN ('authorization_revoked', 'no_governed_limit', "
            "'no_figure', 'unknown_member', 'query_refused', 'data_scope_unsupported')",
            name="ck_bi_alert_events_reason",
        ),
        sa.CheckConstraint(
            "state IN ('breached', 'cleared', 'not_evaluated')", name="ck_bi_alert_events_state"
        ),
        sa.CheckConstraint("length(build_fingerprint) = 64", name="ck_bi_alert_events_fingerprint"),
        sa.ForeignKeyConstraint(
            ["alert_id", "organization_id"],
            ["bi_alerts.id", "bi_alerts.organization_id"],
        ),
        sa.ForeignKeyConstraint(
            ["bank_id", "organization_id"],
            ["banks.id", "banks.organization_id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id",
            "alert_id",
            "as_of_date",
            "build_fingerprint",
            name="uq_bi_alert_events_alert_as_of_fingerprint",
        ),
    )
    op.create_index(
        "ix_bi_alert_events_org_alert_evaluated",
        "bi_alert_events",
        ["organization_id", "alert_id", "as_of_date"],
        unique=False,
    )
    op.create_index(
        "ix_bi_alert_events_org_bank_evaluated",
        "bi_alert_events",
        ["organization_id", "bank_id", "evaluated_at"],
        unique=False,
    )
    op.create_table(
        "bi_subscriptions",
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("owner_user_id", sa.Uuid(), nullable=False),
        sa.Column("query", sa.JSON(), nullable=False),
        sa.Column("artifact_format", sa.String(length=8), nullable=False),
        sa.Column("cadence", sa.String(length=16), nullable=False),
        sa.Column("hour", sa.SmallInteger(), nullable=True),
        sa.Column("minute", sa.SmallInteger(), nullable=True),
        sa.Column("day_of_week", sa.SmallInteger(), nullable=True),
        sa.Column("day_of_month", sa.SmallInteger(), nullable=True),
        sa.Column("recipient_user_ids", sa.JSON(), server_default=sa.text("'[]'"), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.String(length=16), nullable=False),
        sa.Column("bank_id", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "(cadence = 'on_new_data' AND hour IS NULL AND minute IS NULL AND day_of_week IS "
            "NULL AND day_of_month IS NULL) OR (cadence = 'daily' AND hour IS NOT NULL AND "
            "minute IS NOT NULL AND day_of_week IS NULL AND day_of_month IS NULL) OR (cadence "
            "= 'weekly' AND hour IS NOT NULL AND minute IS NOT NULL AND day_of_week IS NOT "
            "NULL AND day_of_month IS NULL) OR (cadence = 'monthly' AND hour IS NOT NULL AND "
            "minute IS NOT NULL AND day_of_week IS NULL AND day_of_month IS NOT NULL)",
            name="ck_bi_subscriptions_schedule_matches_cadence",
        ),
        sa.CheckConstraint(
            "artifact_format IN ('csv', 'xlsx', 'pdf')", name="ck_bi_subscriptions_artifact_format"
        ),
        sa.CheckConstraint(
            "cadence IN ('daily', 'weekly', 'monthly', 'on_new_data')",
            name="ck_bi_subscriptions_cadence",
        ),
        sa.CheckConstraint(
            "day_of_month IS NULL OR (day_of_month >= 1 AND day_of_month <= 28)",
            name="ck_bi_subscriptions_day_of_month",
        ),
        sa.CheckConstraint(
            "day_of_week IS NULL OR (day_of_week >= 1 AND day_of_week <= 7)",
            name="ck_bi_subscriptions_day_of_week",
        ),
        sa.CheckConstraint(
            "hour IS NULL OR (hour >= 0 AND hour <= 23)", name="ck_bi_subscriptions_hour"
        ),
        sa.CheckConstraint(
            "minute IS NULL OR (minute >= 0 AND minute <= 59)", name="ck_bi_subscriptions_minute"
        ),
        sa.ForeignKeyConstraint(
            ["bank_id", "organization_id"],
            ["banks.id", "banks.organization_id"],
        ),
        sa.ForeignKeyConstraint(
            ["owner_user_id", "organization_id"],
            ["users.id", "users.organization_id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "organization_id", name="uq_bi_subscriptions_id_org"),
        sa.UniqueConstraint(
            "organization_id", "bank_id", "name", name="uq_bi_subscriptions_bank_name"
        ),
    )
    op.create_index(
        "ix_bi_subscriptions_org_active_cadence",
        "bi_subscriptions",
        ["organization_id", "is_active", "cadence"],
        unique=False,
    )
    op.create_index(
        "ix_bi_subscriptions_org_bank_active",
        "bi_subscriptions",
        ["organization_id", "bank_id", "is_active"],
        unique=False,
    )
    op.create_table(
        "bi_subscription_deliveries",
        sa.Column("subscription_id", sa.Uuid(), nullable=False),
        sa.Column("recipient_user_id", sa.Uuid(), nullable=False),
        sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=False),
        sa.Column("trigger", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("as_of_date", sa.Date(), nullable=True),
        sa.Column("disclosure_class", sa.String(length=16), nullable=True),
        sa.Column("delivery_mode", sa.String(length=16), nullable=True),
        sa.Column("artifact_format", sa.String(length=8), nullable=True),
        sa.Column("artifact_sha256", sa.String(length=64), nullable=True),
        sa.Column("artifact_size_bytes", sa.Integer(), nullable=True),
        sa.Column("row_count", sa.Integer(), nullable=True),
        sa.Column("member_ids", sa.JSON(), server_default=sa.text("'[]'"), nullable=False),
        sa.Column("denied_members", sa.JSON(), server_default=sa.text("'[]'"), nullable=False),
        sa.Column("reason", sa.String(length=48), nullable=True),
        sa.Column("build_fingerprint", sa.String(length=64), nullable=True),
        sa.Column("builder_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.String(length=16), nullable=False),
        sa.Column("bank_id", sa.String(length=16), nullable=False),
        sa.CheckConstraint(
            "(delivery_mode = 'attachment' AND artifact_sha256 IS NOT NULL AND "
            "artifact_size_bytes IS NOT NULL) OR (delivery_mode <> 'attachment' AND "
            "artifact_sha256 IS NULL AND artifact_size_bytes IS NULL) OR delivery_mode IS NULL",
            name="ck_bi_subscription_deliveries_artifact_accounted",
        ),
        sa.CheckConstraint(
            "delivery_mode IS NULL OR delivery_mode IN ('attachment', 'link')",
            name="ck_bi_subscription_deliveries_delivery_mode",
        ),
        sa.CheckConstraint(
            "disclosure_class IS NULL OR disclosure_class IN ('summary', 'record_level')",
            name="ck_bi_subscription_deliveries_disclosure_class",
        ),
        sa.CheckConstraint(
            "status <> 'sent' OR sent_at IS NOT NULL", name="ck_bi_subscription_deliveries_sent_at"
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'sent', 'denied', 'no_data', 'failed')",
            name="ck_bi_subscription_deliveries_status",
        ),
        sa.CheckConstraint(
            "trigger IN ('schedule', 'new_data')", name="ck_bi_subscription_deliveries_trigger"
        ),
        sa.CheckConstraint(
            "artifact_size_bytes IS NULL OR artifact_size_bytes >= 0",
            name="ck_bi_subscription_deliveries_size",
        ),
        sa.CheckConstraint(
            "row_count IS NULL OR row_count >= 0", name="ck_bi_subscription_deliveries_row_count"
        ),
        sa.CheckConstraint(
            "sent_at IS NULL OR sent_at >= created_at",
            name="ck_bi_subscription_deliveries_sent_after_created",
        ),
        sa.ForeignKeyConstraint(
            ["bank_id", "organization_id"],
            ["banks.id", "banks.organization_id"],
        ),
        sa.ForeignKeyConstraint(
            ["recipient_user_id", "organization_id"],
            ["users.id", "users.organization_id"],
        ),
        sa.ForeignKeyConstraint(
            ["subscription_id", "organization_id"],
            ["bi_subscriptions.id", "bi_subscriptions.organization_id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id",
            "subscription_id",
            "recipient_user_id",
            "scheduled_for",
            name="uq_bi_subscription_deliveries_run_recipient",
        ),
    )
    op.create_index(
        "ix_bi_subscription_deliveries_org_bank_created",
        "bi_subscription_deliveries",
        ["organization_id", "bank_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_bi_subscription_deliveries_org_subscription_scheduled",
        "bi_subscription_deliveries",
        ["organization_id", "subscription_id", "scheduled_for"],
        unique=False,
    )

    if op.get_bind().dialect.name != "postgresql":
        return
    for table in TABLES:
        _enable_rls(table)
    for table in APPEND_ONLY_TABLES:
        _install_append_only(table)


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        for table in APPEND_ONLY_TABLES:
            op.execute(f"DROP TRIGGER IF EXISTS {table}_append_only ON {table}")
        for table in TABLES:
            op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table}")
    for table in reversed(TABLES):
        op.drop_table(table)
