"""Corporate (LRT) return family (plan W5).

Widens ``ck_regulatory_packages_return_family`` to include ``'corporate'`` —
the event-driven LRT corporate return packs (LRT-PROFILE / LRT-OUTLET /
LRT-PARTY / LRT-CAPITAL / LRT-PRODUCT) mint normal packages through the same
generate → validate → approve → export → submit lifecycle.

Revision ID: 202607240021
Revises: 202607240020
"""

from __future__ import annotations

from alembic import op

revision = "202607240021"
down_revision = "202607240020"
branch_labels = None
depends_on = None

_RETURN_FAMILIES = ("liquidity", "capital", "irrbb", "fx", "icaap_stress", "corporate")


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
        "return_family IN ('liquidity', 'capital', 'irrbb', 'fx', 'icaap_stress')",
    )
