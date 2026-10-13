"""Bind canonical references to the owning institution, not just its tenant.

Lineage bank ownership is backfilled from its existing ingestion batch. Nodes
without a batch remain organization-wide and cannot anchor financial rows.
Every affected reference is verified under table ownership before replacement.
"""

from __future__ import annotations

from typing import cast

import sqlalchemy as sa

from alembic import op
from app.db.session import force_rls_suspended

revision = "202610110094"
down_revision = "202610110093"
branch_labels = None
depends_on = None

CANONICAL_TABLES = (
    "canonical_gl_accounts",
    "canonical_counterparties",
    "canonical_products",
    "canonical_positions",
    "canonical_position_snapshots",
    "canonical_reference_rows",
    "canonical_yield_curves",
    "canonical_yield_curve_points",
    "canonical_fx_rates",
    "canonical_market_indices",
    "canonical_counterparty_ratings",
    "canonical_loan_events",
)
REFERENCES = (
    *((table, "ingestion_batch_id", "ingestion_batches") for table in CANONICAL_TABLES),
    *(
        (table, "lineage_id", "lineage_records")
        for table in CANONICAL_TABLES
        if table != "canonical_loan_events"
    ),
    ("lineage_records", "ingestion_batch_id", "ingestion_batches"),
    ("canonical_gl_accounts", "parent_account_id", "canonical_gl_accounts"),
    ("canonical_yield_curve_points", "yield_curve_id", "canonical_yield_curves"),
    ("canonical_position_snapshots", "position_id", "canonical_positions"),
    ("canonical_position_snapshots", "counterparty_id", "canonical_counterparties"),
    ("canonical_position_snapshots", "product_id", "canonical_products"),
    ("canonical_position_snapshots", "gl_account_id", "canonical_gl_accounts"),
)
PARENTS = (*CANONICAL_TABLES, "ingestion_batches", "lineage_records")
REFERENCED_PARENTS = tuple(dict.fromkeys(parent for _, _, parent in REFERENCES))


def verify_reference(table: str, column: str, parent: str) -> None:
    invalid = cast(
        int,
        op.get_bind().scalar(
            sa.text(
                f"SELECT count(*) FROM {table} child WHERE child.{column} IS NOT NULL "
                f"AND NOT EXISTS (SELECT 1 FROM {parent} parent WHERE parent.id = child.{column} "
                "AND parent.organization_id = child.organization_id "
                "AND parent.bank_id = child.bank_id)"
            )
        ),
    )
    if invalid:
        raise RuntimeError(f"{table}.{column}: {invalid} references violate bank ownership")


def replace_reference(table: str, column: str, parent: str, *, upgrading: bool) -> None:
    columns = [column, "organization_id"]
    if upgrading:
        foreign_keys = [
            fk
            for fk in sa.inspect(op.get_bind()).get_foreign_keys(table)
            if fk["constrained_columns"] == columns and fk["referred_table"] == parent
        ]
        if len(foreign_keys) != 1:
            raise RuntimeError(f"{table}.{column}: expected one organization-scoped reference")
        name = foreign_keys[0]["name"]
        if name is None:
            raise RuntimeError(f"{table}.{column}: existing foreign key has no name")
        op.drop_constraint(name, table, type_="foreignkey")
        op.create_foreign_key(
            f"fk_{table}_{column}_bank",
            table,
            parent,
            [*columns, "bank_id"],
            ["id", "organization_id", "bank_id"],
        )
    else:
        op.drop_constraint(f"fk_{table}_{column}_bank", table, type_="foreignkey")
        op.create_foreign_key(
            f"fk_{table}_{column}_org",
            table,
            parent,
            columns,
            ["id", "organization_id"],
        )


def upgrade() -> None:
    connection = op.get_bind()
    connection.execute(sa.text(f"LOCK TABLE banks, {', '.join(PARENTS)} IN ACCESS EXCLUSIVE MODE"))
    op.add_column("lineage_records", sa.Column("bank_id", sa.String(16), nullable=True))
    with force_rls_suspended(connection, "banks", *PARENTS):
        connection.execute(
            sa.text(
                "UPDATE lineage_records node SET bank_id = batch.bank_id "
                "FROM ingestion_batches batch "
                "WHERE node.ingestion_batch_id = batch.id "
                "AND node.organization_id = batch.organization_id"
            )
        )
        for table, column, parent in REFERENCES:
            verify_reference(table, column, parent)
        # A batch-bound node may not use SQL NULL to bypass the scoped key.
        invalid = cast(
            int,
            connection.scalar(
                sa.text(
                    "SELECT count(*) FROM lineage_records "
                    "WHERE ingestion_batch_id IS NOT NULL AND bank_id IS NULL"
                )
            ),
        )
        if invalid:
            raise RuntimeError(f"lineage_records: {invalid} batches have no bank ownership")
        for table in REFERENCED_PARENTS:
            op.create_unique_constraint(
                f"uq_{table}_id_org_bank", table, ["id", "organization_id", "bank_id"]
            )
        op.create_check_constraint(
            "ck_lineage_records_batch_bank",
            "lineage_records",
            "ingestion_batch_id IS NULL OR bank_id IS NOT NULL",
        )
        op.create_foreign_key(
            "fk_lineage_records_bank",
            "lineage_records",
            "banks",
            ["bank_id", "organization_id"],
            ["id", "organization_id"],
        )
        for table, column, parent in REFERENCES:
            replace_reference(table, column, parent, upgrading=True)


def downgrade() -> None:
    for table, column, parent in reversed(REFERENCES):
        replace_reference(table, column, parent, upgrading=False)
    op.drop_constraint("fk_lineage_records_bank", "lineage_records", type_="foreignkey")
    op.drop_constraint("ck_lineage_records_batch_bank", "lineage_records", type_="check")
    for table in reversed(REFERENCED_PARENTS):
        op.drop_constraint(f"uq_{table}_id_org_bank", table, type_="unique")
    op.drop_column("lineage_records", "bank_id")
