"""The governed forecast assumption register, and each run's record of what it used.

Revision ID: 202610070084
Revises: 202610040083

Forecast, what-if and optimizer runs resolved their base, adverse and severely
adverse presets from ``param_stress_shock`` rows with ``module = 'forecast'``. No
product path ever wrote those rows, so for every provisioned bank forecasting
refused with ``missing_parameter`` and there was no governed way out.

``forecast_assumption_versions`` replaces them as the only source the engine
reads: one row per version of a bank's COMPLETE assumption set, drafted by a
maker, decided by a different checker, and effective from a book date. It is
bank-scoped (each bank's board sets its own drivers; two banks of one
organization share an RLS tenant), with at most one draft or submission in
flight per bank (``uq_fav_one_open_per_bank``, partial on both dialects).

``regulatory_runs.assumption_provenance`` records which approved version a
forecasting run consumed, beside the value-based snapshot and never inside it.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "202610070084"
down_revision = "202610040083"
branch_labels = None
depends_on = None

_TENANT_ID_EXPR = "NULLIF(current_setting('app.organization_id', true), '')"

TABLE = "forecast_assumption_versions"

#: Vocabularies PINNED here rather than imported, like every other revision in
#: this chain, so a later model edit cannot change what this revision created.
_STATUSES = "'draft', 'submitted', 'approved', 'rejected'"
_OPEN_STATUSES = "'draft', 'submitted'"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.String(length=16), nullable=False),
        sa.Column("bank_id", sa.String(length=16), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("presets", sa.JSON(), nullable=False),
        sa.Column("change_note", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("submitted_by", sa.Uuid(), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reviewed_by", sa.Uuid(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("review_note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=f"pk_{TABLE}"),
        sa.CheckConstraint(f"status IN ({_STATUSES})", name="ck_fav_status"),
        sa.CheckConstraint("version_number >= 1", name="ck_fav_version_number"),
        sa.CheckConstraint(
            "status NOT IN ('approved', 'rejected') OR reviewed_at IS NOT NULL",
            name="ck_fav_reviewed_at",
        ),
        sa.CheckConstraint(
            "status <> 'approved' OR reviewed_by IS NOT NULL",
            name="ck_fav_approver",
        ),
        sa.CheckConstraint(
            "reviewed_by IS NULL OR submitted_by IS NULL OR reviewed_by <> submitted_by",
            name="ck_fav_checker_not_submitter",
        ),
        sa.CheckConstraint(
            "reviewed_by IS NULL OR reviewed_by <> created_by",
            name="ck_fav_checker_not_author",
        ),
        sa.UniqueConstraint(
            "organization_id", "bank_id", "version_number", name="uq_fav_version_number"
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], name=f"fk_{TABLE}_organization"
        ),
        sa.ForeignKeyConstraint(
            ["bank_id", "organization_id"],
            ["banks.id", "banks.organization_id"],
            name="fk_fav_bank",
        ),
    )
    op.create_index(
        "ix_fav_bank_status_effective",
        TABLE,
        ["organization_id", "bank_id", "status", "effective_from"],
    )
    op.create_index(
        "uq_fav_one_open_per_bank",
        TABLE,
        ["organization_id", "bank_id"],
        unique=True,
        postgresql_where=sa.text(f"status IN ({_OPEN_STATUSES})"),
        sqlite_where=sa.text(f"status IN ({_OPEN_STATUSES})"),
    )
    with op.batch_alter_table("regulatory_runs") as batch:
        batch.add_column(sa.Column("assumption_provenance", sa.JSON(), nullable=True))

    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(f"ALTER TABLE {TABLE} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {TABLE} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY {TABLE}_tenant_isolation ON {TABLE} FOR ALL "
            f"USING ((organization_id)::text = {_TENANT_ID_EXPR}) "
            f"WITH CHECK ((organization_id)::text = {_TENANT_ID_EXPR})"
        )


def downgrade() -> None:
    with op.batch_alter_table("regulatory_runs") as batch:
        batch.drop_column("assumption_provenance")
    if op.get_bind().dialect.name == "postgresql":
        op.execute(f"DROP POLICY IF EXISTS {TABLE}_tenant_isolation ON {TABLE}")
    op.drop_table(TABLE)
