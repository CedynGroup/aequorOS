"""Submission-pipeline fidelity wave (plan W1 + W7 integrity slice).

Mirrors the documented ORASS lifecycle (LRT Portal User Guide v1.0) and adds
the production API channel seam:

- ``declined`` package status (regulator final refusal, distinct from the
  correctable ``rejected``) + ``declined`` submission event.
- ``orass_api`` submission channel (machine-to-machine client; wire contract
  configured per bank at onboarding).
- ``regulatory_resubmission_requests`` — the formal "Request Resubmission"
  entity: reason -> granted/denied -> one superseding regeneration.
- Package columns: ``snapshot_sha256`` (content seal), ``submission_revision``
  (ORASS 1.0/1.1 revision stamped at submit), ``regulator_comments``
  (supervisor comments from reject/decline).
- Schema-level ``(org, package, kind)`` uniqueness for artifacts (previously
  application-enforced only).

Revision ID: 202607240018
Revises: 202607210017
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "202607240018"
down_revision = "202607210017"
branch_labels = None
depends_on = None

_PACKAGE_STATUSES = (
    "draft",
    "generated",
    "validated",
    "pending_approval",
    "approved",
    "submitted",
    "acknowledged",
    "rejected",
    "declined",
    "superseded",
)
_SUBMISSION_CHANNELS = ("orass_api", "orass_sandbox", "email", "manual")
_SUBMISSION_EVENTS = ("submitted", "status_poll", "acknowledged", "rejected", "declined")
_RESUBMISSION_STATUSES = ("requested", "granted", "denied")


def _values(options: tuple[str, ...]) -> str:
    return ", ".join(f"'{option}'" for option in options)


def upgrade() -> None:
    # --- widened enums (drop + recreate the CHECK constraints) -------------
    op.drop_constraint("ck_regulatory_packages_status", "regulatory_packages", type_="check")
    op.create_check_constraint(
        "ck_regulatory_packages_status",
        "regulatory_packages",
        f"status IN ({_values(_PACKAGE_STATUSES)})",
    )
    op.drop_constraint(
        "ck_regulatory_submission_events_channel", "regulatory_submission_events", type_="check"
    )
    op.create_check_constraint(
        "ck_regulatory_submission_events_channel",
        "regulatory_submission_events",
        f"channel IN ({_values(_SUBMISSION_CHANNELS)})",
    )
    op.drop_constraint(
        "ck_regulatory_submission_events_event", "regulatory_submission_events", type_="check"
    )
    op.create_check_constraint(
        "ck_regulatory_submission_events_event",
        "regulatory_submission_events",
        f"event IN ({_values(_SUBMISSION_EVENTS)})",
    )
    op.drop_constraint(
        "ck_regulatory_channel_configs_channel", "regulatory_channel_configs", type_="check"
    )
    op.create_check_constraint(
        "ck_regulatory_channel_configs_channel",
        "regulatory_channel_configs",
        f"channel IN ({_values(_SUBMISSION_CHANNELS)})",
    )

    # --- new package columns ------------------------------------------------
    op.add_column(
        "regulatory_packages", sa.Column("snapshot_sha256", sa.String(length=64), nullable=True)
    )
    op.add_column(
        "regulatory_packages",
        sa.Column("submission_revision", sa.String(length=8), nullable=True),
    )
    op.add_column("regulatory_packages", sa.Column("regulator_comments", sa.Text(), nullable=True))

    # --- artifact uniqueness at the schema level ----------------------------
    op.create_unique_constraint(
        "uq_regulatory_package_artifacts_pkg_kind",
        "regulatory_package_artifacts",
        ["organization_id", "package_id", "kind"],
    )

    # --- resubmission requests ----------------------------------------------
    op.create_table(
        "regulatory_resubmission_requests",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("package_id", sa.Uuid(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column("requested_by", sa.Uuid(), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "detail",
            sa.dialects.postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column("consumed_by_package_id", sa.Uuid(), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            f"status IN ({_values(_RESUBMISSION_STATUSES)})",
            name="ck_regulatory_resubmission_requests_status",
        ),
        sa.ForeignKeyConstraint(
            ["package_id", "organization_id"],
            ["regulatory_packages.id", "regulatory_packages.organization_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "id", "organization_id", name="uq_regulatory_resubmission_requests_id_org"
        ),
    )
    op.create_index(
        "ix_regulatory_resubmission_requests_org_package",
        "regulatory_resubmission_requests",
        ["organization_id", "package_id"],
    )
    # Tenant isolation, same posture as every reporting table.
    op.execute("ALTER TABLE regulatory_resubmission_requests ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE regulatory_resubmission_requests FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON regulatory_resubmission_requests "
        "USING (organization_id = current_setting('app.organization_id')::uuid) "
        "WITH CHECK (organization_id = current_setting('app.organization_id')::uuid)"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON regulatory_resubmission_requests")
    op.drop_index(
        "ix_regulatory_resubmission_requests_org_package",
        table_name="regulatory_resubmission_requests",
    )
    op.drop_table("regulatory_resubmission_requests")
    op.drop_constraint(
        "uq_regulatory_package_artifacts_pkg_kind",
        "regulatory_package_artifacts",
        type_="unique",
    )
    op.drop_column("regulatory_packages", "regulator_comments")
    op.drop_column("regulatory_packages", "submission_revision")
    op.drop_column("regulatory_packages", "snapshot_sha256")
    op.drop_constraint(
        "ck_regulatory_channel_configs_channel", "regulatory_channel_configs", type_="check"
    )
    op.create_check_constraint(
        "ck_regulatory_channel_configs_channel",
        "regulatory_channel_configs",
        "channel IN ('orass_sandbox', 'email', 'manual')",
    )
    op.drop_constraint(
        "ck_regulatory_submission_events_event", "regulatory_submission_events", type_="check"
    )
    op.create_check_constraint(
        "ck_regulatory_submission_events_event",
        "regulatory_submission_events",
        "event IN ('submitted', 'status_poll', 'acknowledged', 'rejected')",
    )
    op.drop_constraint(
        "ck_regulatory_submission_events_channel", "regulatory_submission_events", type_="check"
    )
    op.create_check_constraint(
        "ck_regulatory_submission_events_channel",
        "regulatory_submission_events",
        "channel IN ('orass_sandbox', 'email', 'manual')",
    )
    op.drop_constraint("ck_regulatory_packages_status", "regulatory_packages", type_="check")
    op.create_check_constraint(
        "ck_regulatory_packages_status",
        "regulatory_packages",
        "status IN ('draft', 'generated', 'validated', 'pending_approval', 'approved', "
        "'submitted', 'acknowledged', 'rejected', 'superseded')",
    )
