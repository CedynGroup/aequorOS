"""live rating module

Revision ID: 202608110051
Revises: 202608110050
Create Date: 2026-08-11 03:00:00.000000

Adds the live Treasury/ALM implied-rating scorecard to the constrained live
metric and finding module vocabulary.
"""

from __future__ import annotations

from alembic import op

revision = "202608110051"
down_revision = "202608110050"
branch_labels = None
depends_on = None

_OLD = "module IN ('liquidity', 'capital', 'irr', 'fx', 'ftp', 'forecast')"
_NEW = "module IN ('liquidity', 'capital', 'irr', 'fx', 'ftp', 'rating', 'forecast')"


def _replace(table: str, constraint: str) -> None:
    with op.batch_alter_table(table) as batch_op:
        batch_op.drop_constraint(constraint, type_="check")
        batch_op.create_check_constraint(constraint, _NEW)


def upgrade() -> None:
    _replace("live_metrics", "ck_live_metrics_module")
    _replace("live_metric_snapshots", "ck_live_metric_snapshots_module")
    _replace("live_findings", "ck_live_findings_module")


def downgrade() -> None:
    for table, constraint in (
        ("live_findings", "ck_live_findings_module"),
        ("live_metric_snapshots", "ck_live_metric_snapshots_module"),
        ("live_metrics", "ck_live_metrics_module"),
    ):
        with op.batch_alter_table(table) as batch_op:
            batch_op.drop_constraint(constraint, type_="check")
            batch_op.create_check_constraint(constraint, _OLD)