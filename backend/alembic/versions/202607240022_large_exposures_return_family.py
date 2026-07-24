"""Large Exposures return family (plan W6).

Widens ``ck_regulatory_packages_return_family`` to include
``'large_exposures'`` — the monthly Large Exposures Directive return
(LE-MONTHLY, Templates 1/1a/2/3/4) mints normal packages through the same
generate → validate → approve → export → submit lifecycle.

Revision ID: 202607240022
Revises: 202607240021
"""

from __future__ import annotations

from alembic import op

revision = "202607240022"
down_revision = "202607240021"
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
        "return_family IN ('liquidity', 'capital', 'irrbb', 'fx', 'icaap_stress', 'corporate')",
    )
