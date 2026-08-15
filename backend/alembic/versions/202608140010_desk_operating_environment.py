"""desk operating-environment assessments

Revision ID: 202608140010
Revises: 202608120054
Create Date: 2026-08-14 00:10:00.000000

Creates the GLOBAL ``desk_operating_environment_assessments`` table
(docs/internal/operating_environment_score.md): one maker-checker-governed
determination of the jurisdiction operating-environment strength per COB date.
Deliberately NOT tenant-scoped and NOT RLS-forced — the ``desk_*`` precedent
(desk state is AequorOS-owned, upstream of every tenant; tenant visibility
happens only at publication, when the approved score fans out as the
``GHANA_OPERATING_ENVIRONMENT_SCORE`` canonical market index through the
existing ``aequor_desk`` adapter seam, which requires no schema change).
"""

import sqlalchemy as sa

from alembic import op

revision = "202608140010"
down_revision = "202608120054"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "desk_operating_environment_assessments",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("jurisdiction_code", sa.String(length=8), nullable=False),
        sa.Column("cob_date", sa.Date(), nullable=False),
        sa.Column("methodology_version", sa.String(length=60), nullable=False),
        sa.Column("input_snapshot", sa.JSON(), nullable=False),
        sa.Column("input_digest", sa.String(length=64), nullable=False),
        sa.Column("computed_breakdown", sa.JSON(), nullable=False),
        sa.Column("score", sa.Numeric(9, 6), nullable=False),
        sa.Column(
            "status", sa.String(length=16), server_default=sa.text("'draft'"), nullable=False
        ),
        sa.Column("proposed_by", sa.String(length=320), nullable=False),
        sa.Column("approved_by", sa.String(length=320), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('draft', 'pending_review', 'approved', 'published')",
            name="ck_desk_operating_environment_assessments_status",
        ),
    )
    op.create_index(
        "ix_desk_operating_environment_assessments_jur_cob",
        "desk_operating_environment_assessments",
        ["jurisdiction_code", "cob_date"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_desk_operating_environment_assessments_jur_cob",
        table_name="desk_operating_environment_assessments",
    )
    op.drop_table("desk_operating_environment_assessments")
