"""implied rating runs

Revision ID: 202608110049
Revises: 202608110048
Create Date: 2026-08-11 01:00:00.000000

Immutable tenant-scoped Stage-1 implied bank credit-rating and PD runs. The
full input snapshot and approved global methodology version make every result
reproducible without copying or mutating the upstream market-data desk state.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "202608110049"
down_revision = "202608110048"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "implied_rating_runs",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("organization_id", sa.String(length=16), nullable=False),
        sa.Column("bank_id", sa.String(length=16), nullable=False),
        sa.Column("reporting_period_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("methodology_code", sa.String(length=40), nullable=False),
        sa.Column("methodology_version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column("engine_version", sa.String(length=40), nullable=False),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column("input_snapshot", sa.JSON(), nullable=False),
        sa.Column("results", sa.JSON(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('succeeded', 'failed')", name="ck_implied_rating_runs_status"
        ),
        sa.ForeignKeyConstraint(
            ["bank_id", "organization_id"], ["banks.id", "banks.organization_id"]
        ),
        sa.ForeignKeyConstraint(
            ["reporting_period_id", "organization_id", "bank_id"],
            [
                "bank_reporting_periods.id",
                "bank_reporting_periods.organization_id",
                "bank_reporting_periods.bank_id",
            ],
        ),
        sa.UniqueConstraint(
            "id", "organization_id", "bank_id", name="uq_implied_rating_runs_id_org_bank"
        ),
    )
    op.create_index(
        "ix_implied_rating_runs_org_bank_period",
        "implied_rating_runs",
        ["organization_id", "bank_id", "reporting_period_id"],
    )
    op.create_index(
        "ix_implied_rating_runs_org_input_hash",
        "implied_rating_runs",
        ["organization_id", "input_hash"],
    )


def downgrade() -> None:
    op.drop_index("ix_implied_rating_runs_org_input_hash", table_name="implied_rating_runs")
    op.drop_index("ix_implied_rating_runs_org_bank_period", table_name="implied_rating_runs")
    op.drop_table("implied_rating_runs")