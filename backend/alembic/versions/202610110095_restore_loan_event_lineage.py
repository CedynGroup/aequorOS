"""Restore loan-event structures declared by the canonical model.

The original loan-event revision omitted lineage, the tenant identity key, and
two shared access indexes. Verify lineage across all tenants before installing
the bank-bound key; the identity key is already implied by the id primary key.
"""

from __future__ import annotations

from typing import cast

import sqlalchemy as sa

from alembic import op
from app.db.session import force_rls_suspended

revision = "202610110095"
down_revision = "202610110094"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    connection.execute(
        sa.text("LOCK TABLE canonical_loan_events, lineage_records IN ACCESS EXCLUSIVE MODE")
    )
    with force_rls_suspended(connection, "canonical_loan_events", "lineage_records"):
        invalid = cast(
            int,
            connection.scalar(
                sa.text(
                    "SELECT count(*) FROM canonical_loan_events child WHERE NOT EXISTS "
                    "(SELECT 1 FROM lineage_records parent WHERE parent.id = child.lineage_id "
                    "AND parent.organization_id = child.organization_id "
                    "AND parent.bank_id = child.bank_id)"
                )
            ),
        )
        if invalid:
            raise RuntimeError(
                f"canonical_loan_events.lineage_id: {invalid} invalid scoped references"
            )
        connection.execute(sa.text("SET CONSTRAINTS ALL IMMEDIATE"))
        op.create_foreign_key(
            "fk_canonical_loan_events_lineage_id_bank",
            "canonical_loan_events",
            "lineage_records",
            ["lineage_id", "organization_id", "bank_id"],
            ["id", "organization_id", "bank_id"],
        )
        op.create_unique_constraint(
            "uq_canonical_loan_events_id_org", "canonical_loan_events", ["id", "organization_id"]
        )
        op.create_index(
            "ix_canonical_loan_events_org_bank_as_of",
            "canonical_loan_events",
            ["organization_id", "bank_id", "as_of_date"],
        )
        op.create_index(
            "ix_canonical_loan_events_org_batch",
            "canonical_loan_events",
            ["organization_id", "ingestion_batch_id"],
        )


def downgrade() -> None:
    op.get_bind().execute(sa.text("SET CONSTRAINTS ALL IMMEDIATE"))
    op.drop_index("ix_canonical_loan_events_org_batch", table_name="canonical_loan_events")
    op.drop_index("ix_canonical_loan_events_org_bank_as_of", table_name="canonical_loan_events")
    op.drop_constraint("uq_canonical_loan_events_id_org", "canonical_loan_events", type_="unique")
    op.drop_constraint(
        "fk_canonical_loan_events_lineage_id_bank", "canonical_loan_events", type_="foreignkey"
    )
