"""desk curve definitions (FC-3)

Revision ID: 202608110048
Revises: 202608110047
Create Date: 2026-08-11 00:00:00.000000

Governed forward-curve definitions (forward_curve_construction.md §2.6, FC-3):
the "Curve 1/2/3" analogue as a versioned, dual-controlled desk object. ONE new
GLOBAL table, ``desk_curve_definitions`` — desk master data, deliberately NOT
tenant-scoped and NOT RLS-forced (the ``desk_methodologies`` precedent). No
CHECK-swaps: the AEQ.GHS.SOV.FWD ``forward`` curve type and the ``AEQUOR_DESK``
source system were already widened by 202608090043, so publication of a
constructed curve needs no further constraint changes here.

SQLite-createable (the hermetic suite builds via ``Base.metadata.create_all``);
``downgrade`` drops the table.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "202608110048"
down_revision = "202608110047"
branch_labels = None
depends_on = None

_TABLE = "desk_curve_definitions"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("curve_code", sa.String(length=60), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "status", sa.String(length=12), server_default=sa.text("'draft'"), nullable=False
        ),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("calendar_name", sa.String(length=40), nullable=False),
        sa.Column(
            "curve_kind", sa.String(length=12), server_default=sa.text("'forward'"), nullable=False
        ),
        sa.Column("projection_index", sa.String(length=40), nullable=True),
        sa.Column("discount_curve_code", sa.String(length=60), nullable=True),
        sa.Column("instrument_set_ref", sa.String(length=80), nullable=False),
        sa.Column("interpolation_method", sa.String(length=40), nullable=False),
        sa.Column("output_daycount", sa.String(length=20), nullable=False),
        sa.Column("payment_frequency", sa.String(length=20), nullable=True),
        sa.Column("payment_interval_months", sa.Integer(), nullable=False),
        sa.Column("curve_frequency", sa.String(length=20), nullable=False),
        sa.Column("spot_lag_days", sa.Integer(), server_default=sa.text("2"), nullable=False),
        sa.Column(
            "roll_convention",
            sa.String(length=30),
            server_default=sa.text("'modified_following'"),
            nullable=False,
        ),
        sa.Column(
            "extrapolation_rule",
            sa.String(length=30),
            server_default=sa.text("'flat_forward'"),
            nullable=False,
        ),
        sa.Column("params", sa.JSON(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("change_rationale", sa.Text(), nullable=False),
        sa.Column("proposed_by", sa.String(length=320), nullable=False),
        sa.Column("approved_by", sa.String(length=320), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("effective_from", sa.Date(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('draft', 'approved', 'retired')",
            name="ck_desk_curve_definitions_status",
        ),
        sa.CheckConstraint(
            "curve_kind IN ('forward', 'zero', 'discount')",
            name="ck_desk_curve_definitions_curve_kind",
        ),
        sa.UniqueConstraint(
            "curve_code", "version", name="uq_desk_curve_definitions_code_version"
        ),
    )


def downgrade() -> None:
    op.drop_table(_TABLE)
