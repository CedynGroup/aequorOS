"""Enforce live financial links within their organization and bank.

Supersession is deferred to commit so a replacement can be inserted after the
predecessor is retired in one ORM flush. Both records must exist in the same
institution when that transaction commits. Historical/polymorphic pointers are
catalogued separately in backend/docs/database_integrity.md.
"""

from __future__ import annotations

from typing import cast

import sqlalchemy as sa

from alembic import op
from app.db.session import force_rls_suspended

revision = "202610110096"
down_revision = "202610110095"
branch_labels = None
depends_on = None

CANONICAL_TABLES = (
    "canonical_gl_accounts",
    "canonical_counterparties",
    "canonical_products",
    "canonical_positions",
    "canonical_position_snapshots",
    "canonical_yield_curves",
    "canonical_yield_curve_points",
    "canonical_fx_rates",
    "canonical_market_indices",
    "canonical_counterparty_ratings",
    "canonical_loan_events",
)
REFERENCES = (
    *((table, "superseded_by", table) for table in CANONICAL_TABLES),
    *((table, "withdrawn_by_batch_id", "ingestion_batches") for table in CANONICAL_TABLES),
    ("ilaap_snapshots", "reporting_period_id", "bank_reporting_periods"),
)
TABLES = (*CANONICAL_TABLES, "ilaap_snapshots", "ingestion_batches", "bank_reporting_periods")


def upgrade() -> None:
    connection = op.get_bind()
    connection.execute(sa.text(f"LOCK TABLE {', '.join(TABLES)} IN ACCESS EXCLUSIVE MODE"))
    with force_rls_suspended(connection, *TABLES):
        for table, column, parent in REFERENCES:
            invalid = cast(
                int,
                connection.scalar(
                    sa.text(
                        f"SELECT count(*) FROM {table} child WHERE child.{column} IS NOT NULL "
                        f"AND NOT EXISTS (SELECT 1 FROM {parent} parent "
                        f"WHERE parent.id = child.{column} "
                        "AND parent.organization_id = child.organization_id "
                        "AND parent.bank_id = child.bank_id)"
                    )
                ),
            )
            if invalid:
                raise RuntimeError(f"{table}.{column}: {invalid} invalid live scoped references")
        for table, column, parent in REFERENCES:
            supersession = column == "superseded_by"
            connection.execute(sa.text("SET CONSTRAINTS ALL IMMEDIATE"))
            op.create_foreign_key(
                f"fk_{table}_{column}_bank",
                table,
                parent,
                [column, "organization_id", "bank_id"],
                ["id", "organization_id", "bank_id"],
                deferrable=True if supersession else None,
                initially="DEFERRED" if supersession else None,
            )


def downgrade() -> None:
    for table, column, _parent in reversed(REFERENCES):
        op.get_bind().execute(sa.text("SET CONSTRAINTS ALL IMMEDIATE"))
        op.drop_constraint(f"fk_{table}_{column}_bank", table, type_="foreignkey")
