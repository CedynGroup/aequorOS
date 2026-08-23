"""Seed the Basel HQLA haircuts + Level-2 caps into the control plane.

Enterprise audit 2026-08-20 **P0-8**: ``app/domain/liquidity/engine.py`` summed
every fact carrying an ``hqla_level`` at FACE VALUE — no 15% Level-2A haircut, no
Level-2B haircut, no 40% Level-2 cap, no 15% Level-2B sub-cap — so the LCR was
correct only for a book that is entirely Level 1 and overstated otherwise.

The engine now takes those rates from ``LiquidityParams`` and refuses to weight
an asset whose rate it cannot resolve. This migration adds the five governed
values (BCBS 238 §II.A) to the GLOBAL ``regulatory_parameter`` table for the
``bank`` institution class — the LCR is a Basel, bank-only measure, so no SDI row
is seeded.

The values themselves come from ``app.services.regulatory_parameters.SEED_
PARAMETERS``, the single catalogue the resolver and the hermetic-test seed also
read, so the three can never drift. Rows already present (a fresh deployment
whose ``202608200025`` seed ran after the catalogue was extended) are skipped, so
this migration is safe to run against a database seeded either way.

``regulatory_parameter`` is deliberately NOT RLS-forced (a global reference
table), so this data step runs correctly under the ordinary app role.

Revision ID: 202608220034
Revises: 202608220033
"""

from __future__ import annotations

from datetime import UTC, datetime

import sqlalchemy as sa

from alembic import op
from app.core.ids import new_uuid4

revision = "202608220034"
down_revision = "202608220033"
branch_labels = None
depends_on = None

TABLE = "regulatory_parameter"
PARAM_CODES = (
    "hqla_l1_haircut_pct",
    "hqla_l2a_haircut_pct",
    "hqla_l2b_haircut_pct",
    "hqla_level2_cap_pct",
    "hqla_level2b_cap_pct",
)


def _seed_table() -> sa.TableClause:
    return sa.table(
        TABLE,
        sa.column("id", sa.Uuid(as_uuid=True)),
        sa.column("scope_type", sa.String),
        sa.column("scope_key", sa.String),
        sa.column("param_code", sa.String),
        sa.column("jurisdiction_code", sa.String),
        sa.column("value_numeric", sa.Numeric),
        sa.column("value_json", sa.JSON),
        sa.column("unit", sa.String),
        sa.column("source_citation", sa.String),
        sa.column("confirmation_status", sa.String),
        sa.column("effective_from", sa.Date),
        sa.column("effective_to", sa.Date),
        sa.column("status", sa.String),
        sa.column("proposed_by", sa.String),
        sa.column("approved_by", sa.String),
        sa.column("approved_at", sa.DateTime(timezone=True)),
        sa.column("change_rationale", sa.String),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )


def upgrade() -> None:
    # Deferred import: the single seed catalogue lives with the resolver.
    from app.services.regulatory_parameters import seed_rows  # noqa: PLC0415

    bind = op.get_bind()
    # Dialect-neutral IN list; PARAM_CODES is a module constant, never user input.
    codes_sql = ", ".join(f"'{code}'" for code in PARAM_CODES)
    existing = {
        (row[0], row[1], row[2], row[3])
        for row in bind.execute(
            sa.text(
                "SELECT scope_type, scope_key, param_code, jurisdiction_code "
                f"FROM {TABLE} WHERE param_code IN ({codes_sql})"
            )
        )
    }

    now = datetime.now(UTC)
    rows = [
        {
            **row,
            "id": new_uuid4(),
            "approved_at": now,
            "change_rationale": None,
            "created_at": now,
            "updated_at": now,
        }
        for row in seed_rows()
        if row["param_code"] in PARAM_CODES
        and (
            row["scope_type"],
            row["scope_key"],
            row["param_code"],
            row["jurisdiction_code"],
        )
        not in existing
    ]
    if rows:
        op.bulk_insert(_seed_table(), rows)


def downgrade() -> None:
    codes_sql = ", ".join(f"'{code}'" for code in PARAM_CODES)
    op.execute(sa.text(f"DELETE FROM {TABLE} WHERE param_code IN ({codes_sql})"))
