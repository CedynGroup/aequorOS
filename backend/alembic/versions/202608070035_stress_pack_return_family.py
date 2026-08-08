"""Stress Test Output Report pack family (product.md §Phase 2 item 6).

Widens ``ck_regulatory_packages_return_family`` to include ``'stress'`` —
the event-driven STRESS-PACK Board/ALCO artifact assembles stored stress
outcomes (capital stress paths, liquidity scenarios, reverse-stress
frontier) into a normal package through the same generate → validate →
approve → export lifecycle.

Revision ID: 202608070035
Revises: 202608070034
"""

from __future__ import annotations

from alembic import op

revision = "202608070035"
down_revision = "202608070034"
branch_labels = None
depends_on = None

_RETURN_FAMILIES = (
    "liquidity",
    "capital",
    "irrbb",
    "fx",
    "icaap_stress",
    "corporate",
    "large_exposures",
    "dbk",
    "stress",
)


def _values(options: tuple[str, ...]) -> str:
    return ", ".join(f"'{option}'" for option in options)


def upgrade() -> None:
    op.drop_constraint("ck_regulatory_packages_return_family", "regulatory_packages", type_="check")
    op.create_check_constraint(
        "ck_regulatory_packages_return_family",
        "regulatory_packages",
        f"return_family IN ({_values(_RETURN_FAMILIES)})",
    )


def downgrade() -> None:
    op.drop_constraint("ck_regulatory_packages_return_family", "regulatory_packages", type_="check")
    op.create_check_constraint(
        "ck_regulatory_packages_return_family",
        "regulatory_packages",
        f"return_family IN ({_values(_RETURN_FAMILIES[:-1])})",
    )
