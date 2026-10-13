"""Refuse a bank scope owned by another organization.

Verification runs under the table owner with FORCE RLS suspended and locks held
until commit. No rows are repaired or discarded: operators must resolve any
reported invalid scope before retrying the migration.
"""

from __future__ import annotations

from typing import cast

import sqlalchemy as sa

from alembic import op
from app.db.session import force_rls_suspended

revision = "202610110093"
down_revision = "202610110092"
branch_labels = None
depends_on = None

TABLES = (
    "capital_plans",
    "ilaap_snapshots",
    "liquidity_ewi_indicators",
    "contingency_funding_plans",
    "cfp_activation_events",
    "market_data_source_preferences",
    "stress_scenarios",
    "macro_scenarios",
    "management_action_plans",
)


def upgrade() -> None:
    connection = op.get_bind()
    connection.execute(sa.text(f"LOCK TABLE banks, {', '.join(TABLES)} IN ACCESS EXCLUSIVE MODE"))
    with force_rls_suspended(connection, "banks", *TABLES):
        for table in TABLES:
            invalid = cast(
                int,
                connection.scalar(
                    sa.text(
                        f"SELECT count(*) FROM {table} child WHERE child.bank_id IS NOT NULL "
                        "AND NOT EXISTS (SELECT 1 FROM banks parent "
                        "WHERE parent.id = child.bank_id "
                        "AND parent.organization_id = child.organization_id)"
                    )
                ),
            )
            if invalid:
                raise RuntimeError(f"{table}: {invalid} bank scopes violate organization ownership")
        for table in TABLES:
            bank_keys = [
                fk
                for fk in sa.inspect(connection).get_foreign_keys(table)
                if fk["constrained_columns"] == ["bank_id"] and fk["referred_table"] == "banks"
            ]
            if len(bank_keys) != 1:
                raise RuntimeError(f"{table}: expected exactly one named bank-only foreign key")
            name = bank_keys[0]["name"]
            if name is None:
                raise RuntimeError(f"{table}: bank-only foreign key has no name")
            op.drop_constraint(name, table, type_="foreignkey")
            op.create_foreign_key(
                f"fk_{table}_org_bank",
                table,
                "banks",
                ["organization_id", "bank_id"],
                ["organization_id", "id"],
            )


def downgrade() -> None:
    for table in reversed(TABLES):
        op.drop_constraint(f"fk_{table}_org_bank", table, type_="foreignkey")
        op.create_foreign_key(f"{table}_bank_id_fkey", table, "banks", ["bank_id"], ["id"])
