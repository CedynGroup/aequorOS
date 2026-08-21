"""Expand the SDI default module set (docs/sdi.md §3.2, founder call 2026-08-21).

Behavioural models (deposit stability / NMD duration / prepayment) and
balance-sheet forecasting are core ALM for a deposit-taking lender, not
Basel-specific, so an SDI keeps them. Market Data (curves) is kept for valuing
GoG securities an SDI holds, and IRRBB for banking-book rate risk. FX, FTP and
trading Positions stay bank-only.

This is a data reconciliation for institution_types rows already seeded by
202608190018 (whose historical ``_SDI_MODULES`` snapshot is left untouched); the
hermetic-test mirror in tests/conftest.py is updated to match. Idempotent: sets
the SDI class rows to the final set regardless of their current value.

Revision ID: 202608210026
Revises: 202608200025
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "202608210026"
down_revision = "202608200025"
branch_labels = None
depends_on = None

# The final SDI module set: the core 10 + behavioural, forecasting, markets, irrbb.
_SDI_MODULES = [
    "command_center",
    "risk",
    "alerts",
    "liquidity",
    "capital",
    "regulatory_reporting",
    "data_engine",
    "institution",
    "reports",
    "settings",
    "irrbb",
    "behavioral",
    "forecasting",
    "markets",
]

# The prior (narrower) SDI set, restored on downgrade.
_SDI_MODULES_PRIOR = [
    "command_center",
    "risk",
    "alerts",
    "liquidity",
    "capital",
    "regulatory_reporting",
    "data_engine",
    "institution",
    "reports",
    "settings",
]


def _set_sdi_modules(modules: list[str]) -> None:
    # institution_types is a GLOBAL, non-RLS reference table — a plain UPDATE by the
    # app role applies (no organization GUC needed).
    op.execute(
        sa.text(
            "UPDATE institution_types SET default_modules = :mods "
            "WHERE institution_class = 'sdi'"
        ).bindparams(sa.bindparam("mods", value=modules, type_=sa.JSON()))
    )


def upgrade() -> None:
    _set_sdi_modules(_SDI_MODULES)


def downgrade() -> None:
    _set_sdi_modules(_SDI_MODULES_PRIOR)
