"""Admit the ``credit`` return family on sealed packages (credit PR-6).

The NPL-MONTHLY return (Notice BG/GOV/SEC/2025/23 Appendix II) registers under
a new ``credit`` family for banks and SDIs alike; this widens the
``regulatory_packages.return_family`` CHECK to admit it. Constraint rewrite
only.

Revision ID: 202609010050
Revises: 202609010049
"""

from __future__ import annotations

from alembic import op

revision = "202609010050"
down_revision = "202609010049"
branch_labels = None
depends_on = None

_TABLE = "regulatory_packages"
_CONSTRAINT = "ck_regulatory_packages_return_family"
_FAMILIES_BEFORE = (
    "'liquidity', 'capital', 'irrbb', 'fx', 'icaap_stress', 'corporate', "
    "'large_exposures', 'dbk', 'stress', 'bsd', 'sdi'"
)
_FAMILIES_AFTER = f"{_FAMILIES_BEFORE}, 'credit'"


def _replace(families: str) -> None:
    with op.batch_alter_table(_TABLE) as batch:
        batch.drop_constraint(_CONSTRAINT, type_="check")
        batch.create_check_constraint(_CONSTRAINT, f"return_family IN ({families})")


def upgrade() -> None:
    _replace(_FAMILIES_AFTER)


def downgrade() -> None:
    _replace(_FAMILIES_BEFORE)
