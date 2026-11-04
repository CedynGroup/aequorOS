"""Admit the separate SDI regulatory-report family on sealed packages.

The public LMTD and Large Exposures Directive packets for specialised
deposit-taking institutions use ``return_family='sdi'``. They must be stored as
their own class-scoped family, never relabelled as universal-bank BSD returns.

Revision ID: 202608230040
Revises: 202608230039
"""

from __future__ import annotations

from alembic import op

revision = "202608230040"
down_revision = "202608230039"
branch_labels = None
depends_on = None

_TABLE = "regulatory_packages"
_CONSTRAINT = "ck_regulatory_packages_return_family"
_FAMILIES_BEFORE = (
    "'liquidity', 'capital', 'irrbb', 'fx', 'icaap_stress', 'corporate', "
    "'large_exposures', 'dbk', 'stress', 'bsd'"
)
_FAMILIES_AFTER = f"{_FAMILIES_BEFORE}, 'sdi'"


def _replace_family_constraint(families: str) -> None:
    # batch mode applies the same operation on SQLite's table-rebuild path and
    # PostgreSQL's native ALTER TABLE path.
    with op.batch_alter_table(_TABLE) as batch:
        batch.drop_constraint(_CONSTRAINT, type_="check")
        batch.create_check_constraint(_CONSTRAINT, f"return_family IN ({families})")


def upgrade() -> None:
    _replace_family_constraint(_FAMILIES_AFTER)


def downgrade() -> None:
    _replace_family_constraint(_FAMILIES_BEFORE)