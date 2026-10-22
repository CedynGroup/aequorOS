"""Add capacity and bank-scoped blind indexes for sensitive operational fields.

Revision ID: 202610220101
Revises: 202610140100

Key access belongs to the explicit per-bank backfill, not schema migration.
Historical audit sources and their chains are deliberately unchanged.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op
from app.db.session import force_rls_suspended

revision = "202610220101"
down_revision = "202610140100"
branch_labels = None
depends_on = None

COUNTERPARTIES = "canonical_counterparties"
PARTIES = "related_parties"
CURRENT = "superseded_by IS NULL AND withdrawn_at IS NULL"
WIDENED = (
    (COUNTERPARTIES, "name", 255),
    (COUNTERPARTIES, "source_reference", 255),
    (COUNTERPARTIES, "group_reference", 255),
    (PARTIES, "full_name", 200),
    ("related_party_roles", "term_of_appointment", 80),
    ("related_party_roles", "icag_registration", 60),
)


def _current_index(column: str) -> None:
    op.create_index(
        "uq_canonical_counterparties_current",
        COUNTERPARTIES,
        ["organization_id", "bank_id", "source_system", column, "as_of_date"],
        unique=True,
        postgresql_where=sa.text(CURRENT),
        sqlite_where=sa.text(CURRENT),
    )


def upgrade() -> None:
    for table, column, width in WIDENED:
        op.alter_column(table, column, existing_type=sa.String(width), type_=sa.Text())
    for column in (
        "source_reference_blind_index",
        "name_blind_index",
        "concentration_group_blind_index",
    ):
        op.add_column(COUNTERPARTIES, sa.Column(column, sa.String(255), nullable=True))
    op.add_column(
        COUNTERPARTIES,
        sa.Column(
            "external_identifiers_blind_indexes",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.add_column(PARTIES, sa.Column("full_name_blind_index", sa.String(255), nullable=True))
    # Preserve existing uniqueness before any bank obtains key access for its
    # resumable backfill. Never let FORCE RLS turn this into a partial update.
    with force_rls_suspended(op.get_bind(), COUNTERPARTIES):
        op.execute(
            sa.text(
                "UPDATE canonical_counterparties "
                "SET source_reference_blind_index = source_reference, "
                "concentration_group_blind_index = coalesce(group_reference, name)"
            )
        )
    op.alter_column(COUNTERPARTIES, "source_reference_blind_index", nullable=False)
    op.drop_index("uq_canonical_counterparties_current", table_name=COUNTERPARTIES)
    _current_index("source_reference_blind_index")
    op.create_index(
        "ix_counterparties_name_blind",
        COUNTERPARTIES,
        ["organization_id", "bank_id", "name_blind_index"],
    )
    op.create_index(
        "ix_related_parties_name_blind",
        PARTIES,
        ["organization_id", "bank_id", "full_name_blind_index"],
    )


def downgrade() -> None:
    # Downgrading capacity cannot decrypt data, discard indexes under ciphertext,
    # or produce a database an older binary could misinterpret as plaintext.
    tables = tuple(dict.fromkeys(table for table, _, _ in WIDENED))
    with force_rls_suspended(op.get_bind(), *tables):
        for table, column, _ in WIDENED:
            if op.get_bind().scalar(
                sa.text(
                    f"SELECT EXISTS (SELECT 1 FROM {table} "
                    f"WHERE {column} LIKE 'aqs-bank-field-v1:%')"
                )
            ):
                raise RuntimeError("Bank-encrypted fields prohibit schema downgrade.")
    op.drop_index("ix_related_parties_name_blind", table_name=PARTIES)
    op.drop_index("ix_counterparties_name_blind", table_name=COUNTERPARTIES)
    op.drop_index("uq_canonical_counterparties_current", table_name=COUNTERPARTIES)
    _current_index("source_reference")
    op.drop_column(PARTIES, "full_name_blind_index")
    for column in (
        "source_reference_blind_index",
        "name_blind_index",
        "concentration_group_blind_index",
        "external_identifiers_blind_indexes",
    ):
        op.drop_column(COUNTERPARTIES, column)
    for table, column, width in reversed(WIDENED):
        op.alter_column(table, column, existing_type=sa.Text(), type_=sa.String(width))
