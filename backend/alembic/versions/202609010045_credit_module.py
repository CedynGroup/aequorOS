"""Admit the ``credit`` calculation module on both computation tiers.

The Credit / Loan Book module (credit PR-2) publishes live portfolio-quality
metrics and seals immutable baseline runs; this migration widens the module
CHECK vocabularies to admit the new key on the three live tables and on
``regulatory_runs``. Constraint rewrite only — no rows are touched.

``credit`` is 6 characters, comfortably inside the ``String(16)`` module
columns (the pre-existing ``enterprise_stress`` 17-character width defect is
NOT addressed here).

Revision ID: 202609010045
Revises: 202609010044
"""

from __future__ import annotations

from alembic import op

revision = "202609010045"
down_revision = "202609010044"
branch_labels = None
depends_on = None

_LIVE_OLD = "module IN ('liquidity', 'capital', 'irr', 'fx', 'ftp', 'rating', 'forecast')"
_LIVE_NEW = (
    "module IN ('liquidity', 'capital', 'credit', 'irr', 'fx', 'ftp', 'rating', 'forecast')"
)
_RUNS_OLD = (
    "module IN ('liquidity', 'capital', 'forecast', 'optimizer', 'whatif', "
    "'irr', 'fx', 'ftp', 'reverse_stress', 'enterprise_stress')"
)
_RUNS_NEW = (
    "module IN ('liquidity', 'capital', 'forecast', 'optimizer', 'whatif', "
    "'irr', 'fx', 'ftp', 'reverse_stress', 'enterprise_stress', 'credit')"
)

_LIVE_TABLES = (
    ("live_metrics", "ck_live_metrics_module"),
    ("live_metric_snapshots", "ck_live_metric_snapshots_module"),
    ("live_findings", "ck_live_findings_module"),
)


def _swap(table: str, constraint: str, expression: str) -> None:
    with op.batch_alter_table(table) as batch_op:
        batch_op.drop_constraint(constraint, type_="check")
        batch_op.create_check_constraint(constraint, expression)


def upgrade() -> None:
    for table, constraint in _LIVE_TABLES:
        _swap(table, constraint, _LIVE_NEW)
    _swap("regulatory_runs", "ck_regulatory_runs_module", _RUNS_NEW)


def downgrade() -> None:
    _swap("regulatory_runs", "ck_regulatory_runs_module", _RUNS_OLD)
    for table, constraint in reversed(_LIVE_TABLES):
        _swap(table, constraint, _LIVE_OLD)
