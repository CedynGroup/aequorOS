"""Persist bounded live-module retry classification and schedule.

Revision ID: 202608280045
Revises: 202608270044
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "202608280045"
down_revision = "202608270044"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "live_metrics",
        sa.Column("retry_classification", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "live_metrics",
        sa.Column(
            "retry_attempt_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "live_metrics",
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_check_constraint(
        "ck_live_metrics_retry_classification",
        "live_metrics",
        "retry_classification IS NULL OR retry_classification IN "
        "('structural_unavailable', 'transient_failure')",
    )
    op.create_check_constraint(
        "ck_live_metrics_retry_attempt_count",
        "live_metrics",
        "retry_attempt_count >= 0",
    )
    op.create_check_constraint(
        "ck_live_metrics_next_retry_classification",
        "live_metrics",
        "next_retry_at IS NULL OR retry_classification = 'transient_failure'",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_live_metrics_next_retry_classification",
        "live_metrics",
        type_="check",
    )
    op.drop_constraint("ck_live_metrics_retry_attempt_count", "live_metrics", type_="check")
    op.drop_constraint("ck_live_metrics_retry_classification", "live_metrics", type_="check")
    op.drop_column("live_metrics", "next_retry_at")
    op.drop_column("live_metrics", "retry_attempt_count")
    op.drop_column("live_metrics", "retry_classification")
